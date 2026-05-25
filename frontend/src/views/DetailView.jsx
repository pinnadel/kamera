import { useCallback, useEffect, useRef, useState } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import {
  ChevronsLeft,
  ChevronsRight,
  Copy,
  Check as CheckIcon,
  Moon,
  RotateCcw,
  Sparkles,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import { API } from '../api'
import { formatShutter, iqaLabel, aestheticLabel, faceQualityScore, pickHeadlineScore } from '../ui/format'
import { ScoreBar, InfoTooltip, HoverPopover, ScoreBadge, DecisionBadge, DecisionWord } from '../ui/primitives'
import { PullVisionModelButton } from '../ui/PullVisionModelButton'
import { InstallOllamaCTA } from '../ui/InstallOllamaCTA'
import { THUMB_ASPECT, FILMSTRIP_CHROME_WITH_BADGES, stripHeight } from '../ui/filmstripMetrics'
import { Filmstrip, FILMSTRIP_TOOLBAR_HEIGHT } from '../ui/Filmstrip'
import { CollapsibleSection, Chevron } from '../ui/CollapsibleSection'
import { useLocalStorageState } from '../hooks/useLocalStorageState'

// Fullscreen photo zoom cycle — same model as GroupLoupe + CompareView so
// click-on-photo behaves consistently app-wide. 1× = fit-to-viewport.
// Fullscreen zoom cycle. Click cycles through these scales; after the last
// scale, the next click EXITS fullscreen (does not loop back to 1×). The
// 1× entry is kept so the zoom-level index 0 still corresponds to the
// "just entered fullscreen, not zoomed" state.
const FS_ZOOM_SCALES = [1, 1.5, 3]

// DetailView — full-screen overlay with tiered scores and decision buttons.
//
// When opened from the GroupLoupe, `groupContext` is the active similarity
// group and `onPickGroupMember(id)` switches the focused photo. A small
// filmstrip pins to the bottom of the left preview pane so the user can
// hop between siblings without losing the detail panel context. Without
// `groupContext`, DetailView behaves exactly as before — prev/next walk
// the global images array.
export function DetailView({ image, modelInfo, onClose, onDecide, onUndoImage, onUndo, onAmend = null, onRegisterDecisionIntent = null, onPrev, onNext, hasPrev, hasNext, autoGenerate, onExplanationGenerated, groupContext = null, onPickGroupMember = null, gridFilmstrip = null, onBulk = null, addToast = null, filmstripCollapsed = false, onToggleFilmstripCollapsed = null, setFilmstripCollapsed = null, onStartStripResize = null, setBottomToolbarSlot = null, suppressPanelResize = false }) {
  const shutter = formatShutter(image.shutter_speed)
  // Click on the in-panel photo enters fullscreen (true fullscreen overlay,
  // no info panel, no chrome — UX feedback 2026-05-06). Inside fullscreen,
  // click cycles zoom 1× → 2× → 3× → 1× — same model as GroupLoupe and
  // CompareView so the gesture is consistent app-wide.
  const [fullscreen, setFullscreen] = useState(false)
  // Slot ref for the bottom-toolbar pill. App.jsx portals its pill contents
  // into this node when DetailView is mounted (Luminar-style). On unmount we
  // clear the slot so App falls back to the floating pill.
  const toolbarSlotRef = useRef(null)
  useEffect(() => {
    if (!setBottomToolbarSlot) return
    setBottomToolbarSlot(toolbarSlotRef.current)
    return () => setBottomToolbarSlot(null)
  }, [setBottomToolbarSlot])
  const [fsZoomLevel, setFsZoomLevel] = useState(0)              // 0|1|2 → 1×|2×|3×
  const [fsZoomOrigin, setFsZoomOrigin] = useState({ x: 0.5, y: 0.5 })
  const fsZoomScale = FS_ZOOM_SCALES[fsZoomLevel]
  // Cycle: 1× → 1.5× → 3× → exit fullscreen. The previous behaviour wrapped
  // back to 1×; users asked for an explicit "click out" gesture so the
  // pattern is: enter from DetailView → step through zoom → click again to
  // return. setFullscreen(false) is paired with a zoom-level reset by the
  // existing fullscreen-exit effect.
  const cycleFsZoom = useCallback((origin) => {
    if (origin) setFsZoomOrigin(origin)
    setFsZoomLevel(z => {
      const next = z + 1
      if (next >= FS_ZOOM_SCALES.length) {
        setFullscreen(false)
        return 0
      }
      return next
    })
  }, [])

  // Drag-to-pan inside fullscreen — same model as GroupLoupe so the
  // gesture feels identical app-wide. dragRef holds the live drag, and
  // lastWasDragRef tells the click handler "this mouseup completed a
  // drag, don't cycle zoom on the synthesized click".
  const dragRef        = useRef(null)
  const lastWasDragRef = useRef(false)
  const [isDragging, setIsDragging] = useState(false)

  const handleFsPanMouseDown = useCallback((e) => {
    if (fsZoomLevel === 0) return  // not zoomed → no panning, fall through to click cycle
    e.preventDefault()
    e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originAtStart: { ...fsZoomOrigin },
      containerW: rect.width,
      containerH: rect.height,
      moved: false,
    }
    document.body.style.cursor = 'grabbing'
  }, [fsZoomLevel, fsZoomOrigin])

  const handleFsPanMouseMove = useCallback((e) => {
    if (!dragRef.current || fsZoomLevel === 0) return
    const dx = e.clientX - dragRef.current.startX
    const dy = e.clientY - dragRef.current.startY
    if (!dragRef.current.moved && Math.hypot(dx, dy) < 4) return
    if (!dragRef.current.moved) setIsDragging(true)
    dragRef.current.moved = true
    const w = dragRef.current.containerW || 1
    const h = dragRef.current.containerH || 1
    const ox = dragRef.current.originAtStart.x - dx / w / (1 - 1 / fsZoomScale)
    const oy = dragRef.current.originAtStart.y - dy / h / (1 - 1 / fsZoomScale)
    setFsZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [fsZoomLevel, fsZoomScale])

  const handleFsPanMouseUp = useCallback(() => {
    if (!dragRef.current) return
    lastWasDragRef.current = dragRef.current.moved
    dragRef.current = null
    setIsDragging(false)
    document.body.style.cursor = ''
    // Clear on next microtask so the imminent synthesised click can read
    // it; subsequent clicks are clean.
    setTimeout(() => { lastWasDragRef.current = false }, 0)
  }, [])

  // Wire global listeners so dragging that leaves the image still pans
  // smoothly. Tear down when not zoomed (no panning possible).
  useEffect(() => {
    if (!fullscreen || fsZoomLevel === 0) {
      document.body.style.cursor = ''
      dragRef.current = null
      setIsDragging(false)
      return
    }
    window.addEventListener('mousemove', handleFsPanMouseMove)
    window.addEventListener('mouseup',  handleFsPanMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleFsPanMouseMove)
      window.removeEventListener('mouseup',  handleFsPanMouseUp)
    }
  }, [fullscreen, fsZoomLevel, handleFsPanMouseMove, handleFsPanMouseUp])

  // Trackpad two-finger scroll pans the zoomed view (same as GroupLoupe).
  const handleFsPanWheel = useCallback((e) => {
    if (fsZoomLevel === 0) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    const ox = fsZoomOrigin.x + e.deltaX / rect.width  / (1 - 1 / fsZoomScale)
    const oy = fsZoomOrigin.y + e.deltaY / rect.height / (1 - 1 / fsZoomScale)
    setFsZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [fsZoomLevel, fsZoomOrigin, fsZoomScale])

  // Clipping overlay toggles — lifted out of HistogramSection so the preview
  // image can render the same on/off state as the histogram pills. Reset on
  // image change so a toggle from the previous photo doesn't leak forward.
  const [showShadows,    setShowShadows]    = useState(false)
  const [showHighlights, setShowHighlights] = useState(false)

  // Reset fullscreen, zoom, and clipping overlays on image change.
  useEffect(() => {
    setFullscreen(false)
    setFsZoomLevel(0); setFsZoomOrigin({ x: 0.5, y: 0.5 })
    setShowShadows(false); setShowHighlights(false)
  }, [image.id])

  // Reset fullscreen zoom whenever the user exits fullscreen — fresh exposure
  // every time they re-enter, instead of remembering "I left it at 3× last time".
  useEffect(() => {
    if (!fullscreen) {
      setFsZoomLevel(0)
      setFsZoomOrigin({ x: 0.5, y: 0.5 })
    }
  }, [fullscreen])

  // Esc cascades: fullscreen first, then close DetailView. The default
  // browser handling of Esc is consumed by useHotkeys upstream; we mount our
  // own listener so the cascade order is explicit.
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); setFullscreen(false) } }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [fullscreen])

  // Resizable info panel — drag handle on the panel's left edge updates this
  // state; an effect mirrors it to localStorage so the width survives reload.
  // Clamped to a band that keeps text readable (280) and prevents the preview
  // pane from disappearing on narrow viewports (600).
  const [panelWidth, setPanelWidth] = useState(() => {
    try {
      const raw = parseInt(localStorage.getItem('pca.detailPanelWidth') || '384', 10)
      if (Number.isFinite(raw)) return Math.max(280, Math.min(600, raw))
    } catch { /* localStorage disabled */ }
    return 384
  })
  useEffect(() => {
    try { localStorage.setItem('pca.detailPanelWidth', String(panelWidth)) } catch { /* quota / disabled */ }
  }, [panelWidth])

  // Collapse the info panel down to a 36px rail with just a re-expand chevron.
  // Persisted across sessions; the filmstrip's `right:` offset reads the same
  // effectiveWidth so the strip stretches when the panel is collapsed.
  const [panelCollapsed, setPanelCollapsed] = useState(() => {
    try { return localStorage.getItem('pca.detailPanelCollapsed') === '1' }
    catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('pca.detailPanelCollapsed', panelCollapsed ? '1' : '0') } catch { /* quota / disabled */ }
  }, [panelCollapsed])
  const COLLAPSED_WIDTH = 40
  const effectiveWidth = panelCollapsed ? COLLAPSED_WIDTH : panelWidth

  // Filmstrip collapse is owned by App.jsx so the floating pill above the
  // toolbar can react to the toggle. DetailView just renders + forwards.

  const startPanelResize = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startWidth = panelWidth
    const onMove = (ev) => {
      const next = Math.max(280, Math.min(600, startWidth - (ev.clientX - startX)))
      setPanelWidth(next)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }, [panelWidth])

  // Lock background scroll while DetailView is mounted; restore on unmount.
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // Arrow key navigation — only active when this component is mounted
  useHotkeys('arrowLeft',  (e) => { e.preventDefault(); if (hasPrev) { onPrev() } }, [hasPrev, onPrev])
  useHotkeys('arrowRight', (e) => { e.preventDefault(); if (hasNext) { onNext() } }, [hasNext, onNext])

  // K / M / R — decide the open photo and stay in DetailView. In grid-
  // filmstrip mode App.sendDecision already advances selectedIdx along
  // displayGridItems, so DetailView's panelImage re-renders to the next
  // item automatically — we must NOT call onNext here or we double-step.
  // In group-loupe mode there is no in-DetailView advance (the panel's
  // own onNext walks within the group, but App.sendDecision advances the
  // global grid cursor which would jump out of the group); we fall back
  // to closing so the user lands back in GroupLoupe and can keep culling
  // there. Silent no-op when a group is focused in the filmstrip.
  const decideFromDetail = useCallback(async (d) => {
    if (gridFilmstrip?.groupFocused) return
    // Double-press amend: if the last K/M/R landed within ~400ms, re-apply the
    // new decision to that previous photo and leave the current one untouched
    // (don't advance, don't close groupContext). The intent is registered
    // synchronously *before* the amend awaits — see useKeyboard::decideOrAmend
    // for the full race-condition explanation.
    const amendPromise = onAmend ? onAmend(d) : Promise.resolve(false)
    onRegisterDecisionIntent?.()
    const amended = await amendPromise
    if (amended) return
    onDecide(d)
    if (groupContext) onClose()
  }, [gridFilmstrip, groupContext, onDecide, onClose, onAmend, onRegisterDecisionIntent])
  useHotkeys('k', () => decideFromDetail('keep'),   { enableOnFormTags: false }, [decideFromDetail])
  useHotkeys('m', () => decideFromDetail('maybe'),  { enableOnFormTags: false }, [decideFromDetail])
  useHotkeys('r', () => decideFromDetail('reject'), { enableOnFormTags: false }, [decideFromDetail])

  // O — open the focused group in GroupLoupe. Only active when DetailView
  // is showing a group entry from the grid filmstrip; silent no-op for solo
  // photos and for group-context DetailViews (already inside a group).
  const openFocusedGroup = useCallback(() => {
    if (!gridFilmstrip?.groupFocused || !gridFilmstrip?.group || !gridFilmstrip?.onOpenGroup) return
    gridFilmstrip.onOpenGroup(gridFilmstrip.group)
  }, [gridFilmstrip])
  useHotkeys('o', openFocusedGroup, { enableOnFormTags: false }, [openFocusedGroup])

  // ── Group-focused batch actions (Stage 3) ──────────────────────────────────
  // When the filmstrip's focused item is a group, the panel shows the hero
  // for browsing but the actionable buttons become "Keep best · Maybe rest"
  // and "Keep best · Reject rest" — mirroring GroupLoupe's top-bar buttons
  // so the muscle memory transfers. One-click: keep hero, apply bulk decision
  // to the rest, undo per-photo from the toast.
  const groupForBatch = gridFilmstrip?.groupFocused ? gridFilmstrip?.group : null
  const heroIdForBatch = groupForBatch?.best_image_id ?? null
  const heroImageForBatch = groupForBatch
    ? groupForBatch.images.find(img => img.id === heroIdForBatch)
    : null
  const nonHeroForBatch = groupForBatch
    ? groupForBatch.images.filter(img => img.id !== heroIdForBatch)
    : []
  const runGroupBatch = useCallback(async (decision) => {
    if (!groupForBatch || !onBulk) return
    const ids = nonHeroForBatch.map(img => img.id)
    if (ids.length === 0) return
    if (heroIdForBatch != null && heroImageForBatch?.decision !== 'keep') {
      await onBulk([heroIdForBatch], 'keep')
    }
    await onBulk(ids, decision)
    if (addToast) {
      const verb = decision === 'reject' ? 'rejected' : 'moved to Maybe'
      addToast({
        type: 'info',
        message: `Kept best · ${verb} ${ids.length} photo${ids.length === 1 ? '' : 's'} — undo each with U on the photo`,
        duration: 4000,
      })
    }
  }, [groupForBatch, nonHeroForBatch, heroIdForBatch, heroImageForBatch, onBulk, addToast])

  // U / Cmd+Z — pops the app-global undo stack first; falls back to per-photo
  // undo of the open photo if the stack is empty. Silent no-op when neither
  // path applies.
  const undoCurrent = useCallback(async () => {
    if (onUndo) {
      const handled = await onUndo()
      if (handled) return
    }
    if (!image?.decision) return
    onUndoImage?.(image.id)
  }, [image, onUndo, onUndoImage])
  useHotkeys('u',      undoCurrent, { enableOnFormTags: false }, [undoCurrent])
  useHotkeys('meta+z', undoCurrent, { enableOnFormTags: false }, [undoCurrent])
  // Z cycles zoom while in fullscreen, mirroring GroupLoupe / CompareView.
  useHotkeys('z', (e) => {
    if (!fullscreen) return
    e.preventDefault()
    cycleFsZoom()  // no origin → keeps current centre
  }, { enabled: fullscreen }, [fullscreen, cycleFsZoom])

  // F toggles "focus mode" — hides both the side panel and the filmstrip so
  // the photo fills the viewport. Pressing F again restores the prior state
  // of both panels. A ref holds the snapshot so we can return to e.g. an
  // expanded panel + collapsed strip combo without forcing both to expand.
  const focusModeSnapshotRef = useRef(null)
  const toggleFocusMode = useCallback(() => {
    if (focusModeSnapshotRef.current) {
      const { panel, strip } = focusModeSnapshotRef.current
      setPanelCollapsed(panel)
      if (setFilmstripCollapsed) setFilmstripCollapsed(strip)
      focusModeSnapshotRef.current = null
    } else {
      focusModeSnapshotRef.current = { panel: panelCollapsed, strip: filmstripCollapsed }
      setPanelCollapsed(true)
      if (setFilmstripCollapsed) setFilmstripCollapsed(true)
    }
  }, [panelCollapsed, filmstripCollapsed, setFilmstripCollapsed])
  useHotkeys('f', (e) => {
    if (fullscreen) return  // fullscreen owns its own keys
    e.preventDefault()
    toggleFocusMode()
  }, { enableOnFormTags: false }, [toggleFocusMode, fullscreen])

  const [explanation, setExplanation]   = useState(image.explanation ?? null)
  const [explainState, setExplainState] = useState(
    image.explanation ? 'done' : 'idle'
  )
  // Rich Ollama status snapshot — captured during runGenerate so the
  // unavailable branch can show specific guidance (install vs start vs pull).
  const [explainStatus, setExplainStatus] = useState(null)

  // Shared generate logic — used by auto-generate AND the manual button
  const runGenerate = useCallback(async () => {
    if (explanation) return
    let cancelled = false
    setExplainState('checking')

    try {
      const statusRes = await fetch(`${API}/lm-status`)
      const status = await statusRes.json()
      setExplainStatus(status)
      if (!status.available) {
        setExplainState('unavailable')
        return
      }
      setExplainState('generating')
      const res = await fetch(`${API}/generate-explanation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: image.id }),
      })
      const data = await res.json()
      if (data.explanation) {
        onExplanationGenerated?.(image.id, data.explanation)
        if (!cancelled) {
          setExplanation(data.explanation)
          setExplainState('done')
        }
      } else if (!cancelled) {
        setExplainState('unavailable')
      }
    } catch {
      if (!cancelled) setExplainState('unavailable')
    }

    return () => { cancelled = true }
  }, [image.id, explanation])

  // Auto-generate on open — only when the setting is enabled
  useEffect(() => {
    if (!autoGenerate || explanation) return
    const cancel = runGenerate()
    return () => { cancel?.then?.(fn => fn?.()) }
  }, [image.id, autoGenerate])

  // Clear the stored explanation; resets to idle so user can regenerate.
  const runClear = useCallback(async () => {
    try {
      await fetch(`${API}/explanation/${image.id}`, { method: 'DELETE' })
    } catch { /* server unreachable — clear locally anyway */ }
    onExplanationGenerated?.(image.id, null)
    setExplanation(null)
    setExplainState('idle')
  }, [image.id, onExplanationGenerated])

  const hasAiScores =
    image.iqa_score != null ||
    image.aesthetic_score != null ||
    !!image.face_detected
  const hasPersonal = image.personal_score != null

  // activeClass — applied when the photo's current decision matches this button.
  // idleClass   — default neutral look; hover transitions to the decision tint
  //               so it's clear which keystroke maps to which color.
  const DECISION_BTNS = [
    { d: 'keep',   label: 'Keep',   hotkey: 'K',
      activeClass: 'bg-[rgba(125,184,154,0.20)] border-[rgba(125,184,154,0.50)] text-[#7DB89A]',
      idleClass:   'bg-transparent border-[rgba(255,255,255,0.10)] text-[#f9f9f9] hover:bg-[rgba(125,184,154,0.12)] hover:border-[rgba(125,184,154,0.40)] hover:text-[#7DB89A]' },
    { d: 'maybe',  label: 'Maybe',  hotkey: 'M',
      activeClass: 'bg-[rgba(232,184,74,0.20)] border-[rgba(232,184,74,0.50)] text-[#E8B84A]',
      idleClass:   'bg-transparent border-[rgba(255,255,255,0.10)] text-[#f9f9f9] hover:bg-[rgba(232,184,74,0.12)] hover:border-[rgba(232,184,74,0.40)] hover:text-[#E8B84A]' },
    { d: 'reject', label: 'Reject', hotkey: 'R',
      activeClass: 'bg-[rgba(201,123,123,0.20)] border-[rgba(201,123,123,0.50)] text-[#C97B7B]',
      idleClass:   'bg-transparent border-[rgba(255,255,255,0.10)] text-[#f9f9f9] hover:bg-[rgba(201,123,123,0.12)] hover:border-[rgba(201,123,123,0.40)] hover:text-[#C97B7B]' },
  ]

  // Reserve bottom space for the filmstrip. groupContext uses the fixed
  // small strip (~112 px). gridFilmstrip is user-sized via filmstripMetrics:
  // stripHeight(thumbWidth, FILMSTRIP_CHROME_WITH_BADGES).
  // The slim toolbar above each strip adds FILMSTRIP_TOOLBAR_HEIGHT; when the
  // user collapses the strip, only the toolbar's height is reserved.
  const stripBodyHeight = groupContext
    ? 112
    : gridFilmstrip
      ? stripHeight(gridFilmstrip.thumbSize || 80, FILMSTRIP_CHROME_WITH_BADGES)
      : 0
  const filmstripPadBottom = stripBodyHeight
    ? (filmstripCollapsed ? FILMSTRIP_TOOLBAR_HEIGHT : stripBodyHeight + FILMSTRIP_TOOLBAR_HEIGHT)
    : 0

  return (
    <div className="fixed inset-0 z-50 bg-[#07080a] flex">
      {/* Large preview — click image to enter fullscreen. The black letterbox
          area no longer closes the panel; only the explicit X button (top-
          left of the preview, hover-visible) or Esc do.
          With groupContext / gridFilmstrip, reserve room at the bottom for
          the strip. Tight padding on the other three sides so portraits
          fill more of the viewport and so resizing the strip produces a
          visible reflow of the photo above.
          `group` enables the hover-fade on the close button below. */}
      <div className="group flex-1 relative flex items-center justify-center px-2 pt-2"
           style={filmstripPadBottom ? { paddingBottom: `${filmstripPadBottom}px` } : { paddingBottom: '0.5rem' }}>
        {/* Close button — overlays the picture pane top-left, fades in on
            pane hover. Replaces the X that used to live in the info-panel
            header. */}
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-3 left-3 z-20 w-8 h-8 inline-flex items-center justify-center rounded-md bg-[rgba(7,8,10,0.70)] border border-transparent text-[#cecece] hover:text-[#f0f0f0] hover:bg-[rgba(7,8,10,0.85)] hover:border-[rgba(255,255,255,0.20)] opacity-0 group-hover:opacity-100 transition-[opacity,border-color,background-color,color]"
        >
          <X size={16} />
        </button>

        {/* Preview + clipping overlays.
            CSS grid wrapper with one auto-sized cell: every child placed in
            grid-area 1/1 stacks on top of the others, and the cell sizes to
            its tallest/widest child (the <img>). This gives the overlay PNG
            an identical box to the img — same width, same height, same
            position — without needing any JS measurement.
            `max-h-full max-w-full` on the wrapper passes the parent's size
            constraint through, so `max-h-full` on the img still works.
            `pointer-events-none` on the overlay keeps click-to-zoom working. */}
        <div
          className="relative h-full w-full min-h-0 min-w-0"
          style={{ display: 'grid', gridTemplateRows: 'minmax(0, 1fr)', gridTemplateColumns: 'minmax(0, 1fr)', placeItems: 'center' }}
        >
          <img
            src={`${API}/previews/${image.id}`}
            alt={image.filename}
            style={{ gridArea: '1 / 1' }}
            className="max-h-full max-w-full object-contain rounded shadow-2xl cursor-zoom-in block"
            onClick={e => {
              e.stopPropagation()
              // Single click on the in-panel photo enters fullscreen.
              // Inside fullscreen, click cycles zoom 1× → 2× → 3× → 1×.
              setFullscreen(true)
            }}
            onDoubleClick={e => {
              // Double-click on a group entry opens GroupLoupe — mirrors the
              // grid's double-click-to-loupe gesture. Suppress the single-
              // click fullscreen by closing it again on the same gesture.
              if (!gridFilmstrip?.groupFocused || !gridFilmstrip?.group || !gridFilmstrip?.onOpenGroup) return
              e.stopPropagation()
              setFullscreen(false)
              gridFilmstrip.onOpenGroup(gridFilmstrip.group)
            }}
          />
          {(showShadows || showHighlights) && (
            <ClippingOverlays
              imageId={image.id}
              showShadows={showShadows}
              showHighlights={showHighlights}
            />
          )}
        </div>
      </div>

      {/* Filmstrip — two modes:
            · group-mode (DetailView opened from GroupLoupe): one thumb per
              group member, hero gets a subtle outline, focused gets cyan.
            · grid-mode (DetailView opened from the regular grid): one thumb
              per displayGridItems entry, mixing solo photos and group cells.
              Group cells render as a stacked-paper tile; double-click /
              Enter / Space opens the group in GroupLoupe.
          Pinned to the bottom of the preview pane (absolute) so the photo
          itself scales to the space above. Stops click propagation so
          clicking a thumb doesn't close the panel. */}
      {groupContext ? (
        <Filmstrip
          items={groupContext.images}
          focusedIndex={-1}
          collapsed={filmstripCollapsed}
          onToggleCollapsed={onToggleFilmstripCollapsed}
          onStartResize={onStartStripResize}
          toolbarLabel={<>
            <span className="text-[10px] uppercase tracking-widest text-[#6a6b6c]">In group</span>
            <span className="text-[11px] text-[#9c9c9d]">{groupContext.size} photos</span>
          </>}
          toolbarControls={<div ref={toolbarSlotRef} className="contents" />}
          className="absolute left-0 bottom-0 z-10 bg-[#101111] border-t border-[rgba(255,255,255,0.06)]"
          style={{ right: `${effectiveWidth}px` }}
          onClick={e => e.stopPropagation()}
          renderThumb={(img, idx) => {
            const isFocused = img.id === image.id
            const isHero    = img.id === groupContext.best_image_id
            const ring = isFocused
              ? 'ring-1 ring-[#5BB8D4]'
              : isHero
                ? 'ring-1 ring-[rgba(255,255,255,0.20)]'
                : 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.20)]'
            return (
              <button
                key={img.id}
                type="button"
                data-filmstrip-idx={idx}
                onClick={() => onPickGroupMember?.(img.id)}
                className={`relative flex-shrink-0 w-20 rounded-md overflow-hidden bg-[#161718] cursor-pointer transition-all ${ring} ${img.decision === 'reject' && !isFocused ? 'opacity-[0.45]' : ''}`}
                title={img.filename}
              >
                <div className="bg-[#07080a] h-14 flex items-center justify-center overflow-hidden">
                  <img
                    src={`${API}/previews/${img.id}`}
                    alt={img.filename}
                    className="max-h-full max-w-full object-contain"
                  />
                </div>
                <div className="px-1 py-0.5 flex items-center gap-1">
                  <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
                  {img.decision && <DecisionBadge decision={img.decision} />}
                </div>
              </button>
            )
          }}
        />
      ) : gridFilmstrip && gridFilmstrip.items.length > 0 ? (
        <DetailGridFilmstrip
          items={gridFilmstrip.items}
          focusedIndex={gridFilmstrip.focusedIndex}
          onPickIndex={gridFilmstrip.onPickIndex}
          onOpenGroup={gridFilmstrip.onOpenGroup}
          rightOffset={effectiveWidth}
          thumbSize={gridFilmstrip.thumbSize}
          modelInfo={modelInfo}
          collapsed={filmstripCollapsed}
          onToggleCollapsed={onToggleFilmstripCollapsed}
          onStartResize={onStartStripResize}
          toolbarControls={<div ref={toolbarSlotRef} className="contents" />}
        />
      ) : null}

      {/* Info panel — resizable via the col-resize handle on its left edge.
          Width persists to localStorage. The handle sits inside the panel to
          keep its hit area easy to grab without affecting the preview pane's
          flex sizing. Collapses to a 36px rail with re-expand chevron. */}
      {panelCollapsed ? (
        <div
          className="bg-[#101111] border-l border-[rgba(255,255,255,0.06)] relative flex-shrink-0"
          style={{ width: `${COLLAPSED_WIDTH}px` }}
          onClick={e => e.stopPropagation()}
        >
          <button
            onClick={() => setPanelCollapsed(false)}
            aria-label="Expand panel"
            title="Expand panel"
            className="absolute top-1.5 right-1.5 w-7 h-7 inline-flex items-center justify-center rounded-md text-[#9c9c9d] hover:text-[#f0f0f0] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
          >
            <ChevronsLeft size={16} aria-hidden="true" />
          </button>
        </div>
      ) : (
      <div
        className="bg-[#101111] border-l border-[rgba(255,255,255,0.06)] flex flex-col overflow-hidden relative flex-shrink-0"
        style={{ width: `${panelWidth}px` }}
        onClick={e => e.stopPropagation()}
      >
        <div
          onMouseDown={suppressPanelResize ? undefined : startPanelResize}
          aria-label="Resize info panel"
          className={`absolute top-0 left-0 w-1.5 h-full z-10 transition-colors ${
            suppressPanelResize
              ? 'pointer-events-none'
              : 'cursor-col-resize hover:bg-[rgba(91,184,212,0.30)] active:bg-[rgba(91,184,212,0.50)]'
          }`}
        />
        {/* Panel header with filename + (per-photo) decision buttons OR group
            banner with batch actions. The collapse chevron is absolutely
            positioned at top-right so it sits at the exact same screen
            position as the collapsed-rail chevron (no jump on toggle). */}
        <div className="relative px-5 pt-4 pb-4 bg-[#161718] border-b border-[#2f3031] flex-shrink-0 space-y-3">
          <button
            onClick={() => setPanelCollapsed(true)}
            aria-label="Collapse panel"
            title="Collapse panel"
            className="absolute top-1.5 right-1.5 z-10 w-7 h-7 inline-flex items-center justify-center rounded-md text-[#9c9c9d] hover:text-[#f0f0f0] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
          >
            <ChevronsRight size={16} aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2 pr-8">
            {gridFilmstrip?.groupFocused && groupForBatch ? (
              <div className="min-w-0 flex-1">
                <div className="text-[10px] uppercase tracking-widest text-[#6a6b6c] mb-0.5">
                  Group of {groupForBatch.size} photos
                </div>
                <div className="text-[13px] text-[#f0f0f0] truncate" title={image.filename}>
                  Best: {image.filename}
                </div>
              </div>
            ) : (
              <FilenameWithCopy filename={image.filename} />
            )}
          </div>

          {/* Per-photo decision buttons OR group batch buttons. After deciding
              a single photo, App.sendDecision advances the cursor so DetailView
              re-renders to the next item without leaving. For a group, batch
              actions hit POST /bulk-decision via the parent. */}
          {gridFilmstrip?.groupFocused ? (
            <div className="space-y-2">
              <div className="text-[11px] text-[#9c9c9d] leading-relaxed">
                Showing the AI&rsquo;s best of {groupForBatch?.size ?? 0} photos.
                Decide the whole group below, or{' '}
                <button
                  type="button"
                  onClick={() => groupForBatch && gridFilmstrip.onOpenGroup(groupForBatch)}
                  className="text-[#5BB8D4] hover:opacity-70 underline-offset-2 hover:underline"
                >
                  open in loupe
                </button>{' '}
                to triage individually — double-click the photo or press O
              </div>
              {nonHeroForBatch.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <button
                    onClick={() => runGroupBatch('maybe')}
                    className="w-full inline-flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-lg text-xs bg-[rgba(232,184,74,0.12)] text-[#E8B84A] border border-[rgba(232,184,74,0.30)] hover:opacity-70 transition-opacity"
                    title={`Keep best, mark the other ${nonHeroForBatch.length} as Maybe`}
                  >
                    Keep best · <DecisionWord kind="maybe">Maybe</DecisionWord> rest
                  </button>
                  <button
                    onClick={() => runGroupBatch('reject')}
                    className="w-full inline-flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-lg text-xs bg-[rgba(201,123,123,0.12)] text-[#C97B7B] border border-[rgba(201,123,123,0.30)] hover:opacity-70 transition-opacity"
                    title={`Reject the other ${nonHeroForBatch.length} photo${nonHeroForBatch.length === 1 ? '' : 's'}`}
                  >
                    Keep best · <DecisionWord kind="reject">Reject</DecisionWord> rest
                  </button>
                </div>
              )}
            </div>
          ) : (
            // -mx-3 widens the row 12 px past the header padding so the
            // K/M/R buttons line up with the Section cards in the body,
            // which use the same -mx-3 trick.
            <div className="-mx-3 flex gap-1.5">
              {DECISION_BTNS.map(({ d, label, hotkey, activeClass, idleClass }) => (
                <button
                  key={d}
                  onClick={() => decideFromDetail(d)}
                  className={`flex-1 py-2 px-2 rounded-lg text-xs font-semibold border transition-colors flex items-center justify-center gap-1 ${image.decision === d ? activeClass : idleClass}`}
                >
                  <span
                    className="inline-flex items-center justify-center w-[18px] h-[18px] bg-gradient-to-b from-[#121212] to-[#0d0d0d] rounded-[4px] text-[11px] font-semibold mr-1"
                    style={{boxShadow:'0 1.5px 0.5px 2.5px rgba(0,0,0,0.4),inset 0 1px 0 rgba(255,255,255,0.08),inset 0 -1px 0 rgba(0,0,0,0.4),0 1px 0 rgba(0,0,0,0.5),0 0 0 1px rgba(255,255,255,0.04)'}}
                  >{hotkey}</span>
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-3 p-5 flex-1 min-h-0 overflow-y-auto">

          {/* Personal Scoring — temporarily disabled while debugging
              black-screen on detail open. Re-enable once the runtime issue
              is identified. */}
          {/* <PersonalSection image={image} modelInfo={modelInfo} /> */}

          {/* Technical Quality */}
          <Section
            id="technical"
            label="Technical Quality"
            chips={[{
              label: 'Overall',
              value: image.overall_score,
              tint: technicalTint(image.overall_score),
              hoverInfo: <OverallChipExplanation image={image} />,
            }]}
            tooltip={
              <>Pixel-level measurements from the raw image. <strong className="text-[#f9f9f9] font-semibold">Overall</strong> is a weighted blend: <strong className="text-[#f9f9f9] font-semibold">sharpness × 0.65 + exposure × 0.35</strong>. Sharpness is weighted higher because blur cannot be recovered in post.</>
            }
          >
            <div className="space-y-3 pt-1">
              <div>
                <p className="text-[14px] text-[#cecece] mb-1">Sharpness</p>
                <ScoreBar
                  value={image.sharpness_score}
                  tooltip="How sharp the image is. Below 40 usually means blur or missed focus."
                />
              </div>
              <div>
                <p className="text-[14px] text-[#cecece] mb-1">Exposure</p>
                <ScoreBar
                  value={image.exposure_score}
                  tooltip="Tonal balance. Below 40 means clipped highlights or crushed shadows."
                />
              </div>
            </div>
          </Section>

          {/* AI Quality — perceptual + aesthetic + composite face quality.
              Section header shows ONE band-word chip (worst-available band)
              instead of three sub-chips, so the headline isn't redundant
              with the bars on expand. Drill down to the per-metric bars
              when you need to see which signal is weak. */}
          {hasAiScores && <AiQualitySection image={image} />}

          {/* Content signals — SigLIP zero-shot perception axes (subject
              prominence, background, eye contact, decisive moment). Same
              section-summary band-chip pattern as AI Quality. The section
              hides itself when no axis has data (pre-v39 rows). */}
          <ContentSignalsSection image={image} />

          <SectionDivider />

          {/* Histogram — RGB tonal distribution with on-demand clipping
              callouts. Computed lazily from the preview JPEG, so the section
              fetches its own data when first opened. The clipping toggles
              live in DetailView so the preview overlay reflects the same
              state. Sits right before EXIF as a chrome-less collapsible
              section so both data-style sections read as one family. */}
          <HistogramSection
            imageId={image.id}
            showShadows={showShadows}
            showHighlights={showHighlights}
            onToggleShadows={() => setShowShadows(s => !s)}
            onToggleHighlights={() => setShowHighlights(s => !s)}
          />

          <SectionDivider />

          {/* EXIF — pure metadata. Scene used to live here but moved into
              Content Signals where it belongs (it's a zero-shot perception
              read, not camera metadata). */}
          <CollapsibleSection storageKey="pca.detail.section.exif" label="EXIF">
            {[
              ['Camera',  image.camera],
              ['Shot at', image.shot_at],
              ['Focal',   image.focal_length_mm ? `${image.focal_length_mm}mm` : null],
              ['f/',      image.aperture ? `f/${image.aperture}` : null],
              ['Shutter', shutter],
              ['ISO',     image.iso],
            ].map(([label, val]) => val != null && (
              <div key={label} className="flex justify-between text-xs">
                <span className="text-[#9c9c9d]">{label}</span>
                <span className="text-[#f9f9f9] font-mono">{val}</span>
              </div>
            ))}
          </CollapsibleSection>

          <SectionDivider />

          {/* AI Analysis / Explanation */}
          <CollapsibleSection storageKey="pca.detail.section.explanation" label="Explanation">
            {/* Generated text */}
            {explainState === 'done' && explanation && (
              <>
                {/* Text-only-model warning: when the connected model isn't
                    vision-capable, the explanation was generated from
                    numeric scores + EXIF only — the model never saw the
                    photo. Without this banner the user can't tell the
                    difference between a vision-aware and a text-only
                    explanation, so they may trust prose that doesn't
                    actually describe the image. */}
                {explainStatus?.vision_capable === false && (
                  <div className="mb-2 flex items-start gap-2 px-2.5 py-1.5 rounded-md bg-[rgba(232,184,74,0.08)] border border-[rgba(232,184,74,0.20)]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#E8B84A] shrink-0 mt-1" />
                    <span className="text-[11px] text-[#cecece] leading-relaxed">
                      Generated from scores + EXIF only — the connected
                      model (<span className="font-mono text-[#E8B84A]">{explainStatus.model}</span>) can't see images. Pull{' '}
                      <span className="font-mono text-[#E8B84A]">qwen2.5vl:7b</span> for vision-aware explanations
                      (Settings → Narrative explanations).
                    </span>
                  </div>
                )}
                <p className="text-sm text-[#8a8a8a] leading-relaxed">{explanation}</p>
                <button
                  onClick={runClear}
                  className="inline-flex items-center gap-1 text-xs text-[#9c9c9d] hover:text-[#cecece] transition-colors underline mt-1"
                >
                  <Trash2 size={13} /> Clear explanation
                </button>
              </>
            )}

            {/* Busy states */}
            {(explainState === 'checking' || explainState === 'generating') && (
              <p className="text-xs text-[#4a4a4a] italic animate-pulse">
                {explainState === 'checking' ? 'Checking Ollama…' : 'Generating explanation…'}
              </p>
            )}

            {/* Manual generate button — shown when auto-generate is off and no explanation yet */}
            {!autoGenerate && explainState === 'idle' && !explanation && (
              <button
                onClick={runGenerate}
                className="w-full inline-flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold bg-[rgba(123,130,201,0.12)] hover:opacity-70 text-[#7B82C9] transition-opacity border border-[rgba(123,130,201,0.30)]"
              >
                <Sparkles size={16} /> Generate explanation
              </button>
            )}

            {/* Ollama not ready — show context-aware guidance */}
            {explainState === 'unavailable' && (
              <OllamaUnavailable
                status={explainStatus}
                onRetry={() => { setExplainState('idle'); runGenerate() }}
              />
            )}
          </CollapsibleSection>

        </div>
      </div>
      )}

      {/* True fullscreen viewer — z-[100] sits above DetailView's z-50 chrome.
          Click on the photo cycles zoom 1× → 2× → 3× → 1×; click on the dark
          background, the × button, or Esc exits fullscreen. */}
      {fullscreen && (
        <div
          className="fixed inset-0 z-[100] bg-[#07080a] flex items-center justify-center cursor-zoom-out"
          onClick={() => setFullscreen(false)}
        >
          <img
            src={`${API}/previews/${image.id}`}
            alt={image.filename}
            style={{
              transform: `scale(${fsZoomScale})`,
              transformOrigin: `${fsZoomOrigin.x * 100}% ${fsZoomOrigin.y * 100}%`,
              // Skip the transition while actively dragging — otherwise
              // every pan delta lerps and the image feels rubbery.
              transition: isDragging ? 'none' : 'transform 0.18s ease-out',
            }}
            className={`max-h-screen max-w-screen object-contain select-none ${fsZoomLevel > 0 ? (isDragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'}`}
            draggable={false}
            onMouseDown={handleFsPanMouseDown}
            onWheel={handleFsPanWheel}
            onClick={(e) => {
              e.stopPropagation()
              // Suppress the click that follows a pan-drag — mouseup
              // already handled the user's intent.
              if (lastWasDragRef.current) return
              const rect = e.currentTarget.getBoundingClientRect()
              const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
              const y = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height))
              cycleFsZoom({ x, y })
            }}
          />
          {/* Zoom level indicator — only when zoomed in */}
          {fsZoomLevel > 0 && (
            <div className="absolute top-4 left-4 px-2 py-1 rounded-md bg-[rgba(7,8,10,0.70)] text-[#cecece] text-[11px] font-mono pointer-events-none select-none">
              {fsZoomScale}×
            </div>
          )}
          {/* Close affordance — single character, faint, top-right */}
          <button
            onClick={(e) => { e.stopPropagation(); setFullscreen(false) }}
            className="absolute top-4 right-4 w-9 h-9 rounded-full bg-[rgba(7,8,10,0.70)] hover:opacity-70 text-[#f9f9f9] transition-opacity flex items-center justify-center text-base leading-none"
            title="Exit fullscreen (Esc)"
          >×</button>
        </div>
      )}

    </div>
  )
}

// OllamaUnavailable — context-aware "explanation can't run" panel.
//   ready         → Ollama is up but generation failed (timeout, empty response)
// Grid-mode filmstrip — renders displayGridItems at the bottom of the
// preview pane. Solo photos render as a single thumb; groups render with a
// stacked-paper edge + count chip so they're visually distinct from solo
// cells (same affordance as GroupTile in the main grid). Single-click
// focuses; double-click / Enter / Space on a group cell opens the loupe.
// Auto-scrolls the focused cell into view so ←/→ navigation never leaves
// the user staring at a thumb that's been scrolled off-screen.
// thumbSize: width in px of each thumb cell. Height scales 0.7× width so the
// 4:3 aspect ratio reads as a strip, not square thumbs.
// FilmstripToolbar — slim bar that sits above the thumbnail row. Hosts:
//   · collapse chevron (toggles thumbnail visibility)
//   · optional contextual label (e.g. "In group · N photos")
//   · the grid's pill controls (Sort / Filter / View / Tab settings) passed
//     in via the `controls` slot, so DetailView doesn't have to know about
//     App.jsx's state shape.
// Stays visible whether or not the thumbnail row is collapsed.
// DetailGridFilmstrip — DetailView-specific wrapper around the shared
// Filmstrip primitive. Renders one thumb per displayGridItems entry,
// mixing solo photos and stacked-paper group cells. Group cells double-
// click / Enter / Space open the group in GroupLoupe.
function DetailGridFilmstrip({ items, focusedIndex, onPickIndex, onOpenGroup, rightOffset, thumbSize = 80, modelInfo, collapsed = false, onToggleCollapsed = null, onStartResize = null, toolbarControls = null }) {
  const thumbW = thumbSize
  const thumbH = Math.round(thumbSize * THUMB_ASPECT)
  return (
    <Filmstrip
      items={items}
      focusedIndex={focusedIndex}
      autoScrollToFocused
      collapsed={collapsed}
      onToggleCollapsed={onToggleCollapsed}
      onStartResize={onStartResize}
      toolbarControls={toolbarControls}
      className="absolute left-0 bottom-0 z-10 bg-[#101111] border-t border-[rgba(255,255,255,0.06)]"
      style={{ right: `${rightOffset}px` }}
      onClick={e => e.stopPropagation()}
      renderThumb={(item, idx) => {
        const isFocused = idx === focusedIndex
        const ring = isFocused
          ? 'ring-1 ring-[#5BB8D4]'
          : 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.20)]'
        if (item.type === 'group') {
          const group = item.group
          const hero = group.images.find(i => i.id === group.best_image_id) || group.images[0]
          return (
            <div
              key={`g-${group.id}`}
              data-filmstrip-idx={idx}
              role="button"
              tabIndex={0}
              onClick={() => onPickIndex(idx)}
              onDoubleClick={() => onOpenGroup(group)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onOpenGroup(group)
                }
              }}
              style={{ width: `${thumbW}px` }}
              className={`relative flex-shrink-0 rounded-md bg-[#161718] cursor-pointer transition-all ${ring}`}
              title={`Group of ${group.size} photos`}
            >
              {/* Stacked-paper edge — two offset rectangles peeking out behind */}
              <div className="absolute -top-0.5 left-1 right-1 h-1 rounded-sm bg-[#1f2022] ring-1 ring-[rgba(255,255,255,0.06)]" />
              <div className="absolute -top-1 left-2 right-2 h-1 rounded-sm bg-[#262728] ring-1 ring-[rgba(255,255,255,0.04)]" />
              <div
                style={{ height: `${thumbH}px` }}
                className="relative bg-[#07080a] flex items-center justify-center overflow-hidden rounded-t-md"
              >
                {hero && (
                  <img
                    src={`${API}/previews/${hero.id}`}
                    alt=""
                    className="max-h-full max-w-full object-contain"
                  />
                )}
                <span className="absolute top-1 right-1 px-1 py-px rounded text-[9px] font-semibold leading-none bg-[rgba(7,8,10,0.75)] text-[#cecece] ring-1 ring-[rgba(255,255,255,0.10)]">
                  {group.size}
                </span>
              </div>
              <div className="px-1 py-0.5 text-[9px] text-[#9c9c9d] uppercase tracking-wider text-center">
                Group
              </div>
            </div>
          )
        }
        const img = item.image
        return (
          <button
            key={img.id}
            type="button"
            data-filmstrip-idx={idx}
            onClick={() => onPickIndex(idx)}
            style={{ width: `${thumbW}px` }}
            className={`relative flex-shrink-0 rounded-md overflow-hidden bg-[#161718] cursor-pointer transition-all ${ring} ${img.decision === 'reject' && !isFocused ? 'opacity-[0.45]' : ''}`}
            title={img.filename}
          >
            <div
              style={{ height: `${thumbH}px` }}
              className="bg-[#07080a] flex items-center justify-center overflow-hidden"
            >
              <img
                src={`${API}/previews/${img.id}`}
                alt={img.filename}
                className="max-h-full max-w-full object-contain"
              />
            </div>
            <div className="px-1 py-0.5 flex items-center gap-1">
              <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
              {img.decision && <DecisionBadge decision={img.decision} />}
            </div>
          </button>
        )
      }}
    />
  )
}

//   not_installed → install hint + link to ollama.com
//   not_running   → start hint (run `ollama serve`)
//   no_models     → pull hint (run `ollama pull qwen2.5vl:7b`)
//   anything else → fallback generic message
function OllamaUnavailable({ status, onRetry }) {
  const s = status?.status

  let title = 'Local language model not ready'
  let body  = null

  if (s === 'ready') {
    title = 'Generation failed'
    body  = (
      <>
        Ollama is running (<span className="font-mono text-[#cecece]">{status.model}</span>) but didn't return a result — the model may have timed out. Try again; if it keeps failing, check <span className="font-mono text-[#cecece]">data/app.log</span> for details.
      </>
    )
  } else if (s === 'not_installed') {
    title = 'Ollama is not installed'
    body  = <InstallOllamaCTA />
  } else if (s === 'not_running') {
    title = 'Ollama is installed but not running'
    body  = (
      <>
        Run <span className="font-mono text-[#cecece]">ollama serve</span> in a Terminal window — the daemon stays running in the background. Then click Try again.
      </>
    )
  } else if (s === 'no_models') {
    title = 'Ollama has no models installed'
    body  = (
      <div className="space-y-2">
        <PullVisionModelButton onDone={onRetry} />
        <p>
          Or run <span className="font-mono text-[#cecece]">ollama pull qwen2.5vl:7b</span> in Terminal (≈6 GB, vision-capable) — or any model name from{' '}
          <a href="https://ollama.com/library" target="_blank" rel="noreferrer" className="text-[#5BB8D4] hover:opacity-70 underline">ollama.com/library</a>.
        </p>
      </div>
    )
  } else {
    body = (
      <>Start Ollama and pull a model for narrative explanations.</>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-[#cecece] font-medium">{title}</p>
      {/* `body` is sometimes inline text (other branches return a fragment of
          spans + text) and sometimes block-level (the no_models branch which
          embeds the PullVisionModelButton). A `<div>` wrapper handles both —
          historically this was a `<p>` but invalid `<div>` inside `<p>` blanks
          the panel in production. */}
      <div className="text-xs text-[#9c9c9d] leading-relaxed">{body}</div>
      <button
        onClick={onRetry}
        className="inline-flex items-center gap-1 text-xs text-[#9c9c9d] hover:text-[#cecece] transition-colors underline"
      >
        <RotateCcw size={13} /> Try again
      </button>
    </div>
  )
}

// FilenameWithCopy — DetailView header filename with hover-revealed copy
// button. Click copies the bare filename (no path) to the clipboard and shows
// a brief "Copied" flash so the user gets feedback the action happened.
function FilenameWithCopy({ filename }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async (e) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(filename)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch { /* clipboard API unavailable — silent no-op */ }
  }, [filename])

  return (
    <div className="group/filename flex items-center gap-2 min-w-0 flex-1">
      <p className="text-[22px] font-medium text-[#f9f9f9] truncate font-sans" title={filename}>
        {filename}
      </p>
      <button
        onClick={handleCopy}
        className="flex-shrink-0 opacity-0 group-hover/filename:opacity-100 focus-visible:opacity-100 transition-opacity p-1 rounded hover:bg-[rgba(255,255,255,0.06)]"
        title={copied ? 'Copied!' : 'Copy filename'}
        aria-label="Copy filename to clipboard"
      >
        {copied
          ? <CheckIcon size={14} strokeWidth={2} className="text-[#7DB89A]" />
          : <Copy size={14} strokeWidth={1.5} className="text-[#9c9c9d]" />
        }
      </button>
    </div>
  )
}

// ScoreChip — compact "label · value" pill shown in section headers.
// Monospace numeral matches the design system. When `tint` is set the chip
// uses the tint's bg + border to color-code the value's quality band (used by
// the AI Quality chip to communicate TOPIQ level). When `hoverInfo` is set
// the chip itself becomes the hover trigger for a popover explaining the
// value (used by the Personal chip to surface delta + top driver).
// SectionDivider — fainter than the chrome divider under the panel header,
// inset from the panel edges so it reads as an internal grouping rule rather
// than a hard-edge separator.
function SectionDivider() {
  // -mx-3 matches the Section / CollapsibleSection cards so the rule extends
  // to the same horizontal edges as the bordered containers above and below.
  return <div className="-mx-3 border-t border-[rgba(255,255,255,0.12)]" />
}

function ScoreChip({ label, value, tint, hoverInfo }) {
  if (value == null) return null
  const cls = tint
    ? `border ${tint.border} ${tint.bg}`
    : 'border border-[rgba(255,255,255,0.10)] bg-[#1b1c1e]'
  const valueColor = tint?.text ?? 'text-[#f9f9f9]'
  const chip = (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md ${cls} ${hoverInfo ? 'cursor-help' : ''}`}>
      <span className="text-[10px] text-[#9c9c9d] uppercase tracking-wider">{label}</span>
      <span className={`text-[12px] font-mono tabular-nums ${valueColor}`}>{value.toFixed(0)}</span>
    </span>
  )
  return hoverInfo ? <HoverPopover content={hoverInfo}>{chip}</HoverPopover> : chip
}

// BandChip — section-summary chip variant. Same chrome as ScoreChip but
// displays a band word (Excellent / Good / Fair / Poor) instead of a
// numeric value. Used by sections without a defined composite (AI Quality,
// Content Signals): the band reflects the section's worst-available signal,
// so the chip flags "is anything in this layer poor?" at a glance without
// inventing a fake aggregate number. Tinted by `bandTint(word)`.
function BandChip({ label, band, hoverInfo }) {
  if (band == null) return null
  const tint = bandTint(band)
  const cls = tint
    ? `border ${tint.border} ${tint.bg}`
    : 'border border-[rgba(255,255,255,0.10)] bg-[#1b1c1e]'
  const valueColor = tint?.text ?? 'text-[#f9f9f9]'
  const chip = (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md ${cls} ${hoverInfo ? 'cursor-help' : ''}`}>
      {label && <span className="text-[10px] text-[#9c9c9d] uppercase tracking-wider">{label}</span>}
      <span className={`text-[11px] font-medium ${valueColor}`}>{band}</span>
    </span>
  )
  return hoverInfo ? <HoverPopover content={hoverInfo}>{chip}</HoverPopover> : chip
}

// bandTint — maps band words to the universal TIER tint palette so band
// chrome stays consistent across sections regardless of which scorer the
// band came from (IQA 75/55/35, Aesthetic 70/50/30, Content 0.75/0.55/0.35).
function bandTint(band) {
  switch (band) {
    case 'Excellent': return TINT_TIER_4
    case 'Good':      return TINT_TIER_3
    case 'Fair':      return TINT_TIER_2
    case 'Poor':      return TINT_TIER_1
    default:          return null
  }
}

// bandHoverContent — explanatory popover for a section-summary BandChip.
// Returns short, section-specific copy that explains what the worst-signal
// band actually tells the user — "Fair on the AI layer" vs "Fair on Content
// signals" mean different things in practice. Returns null for unknown
// bands so the chip falls back to "no popover" rather than rendering a
// blank hover. Keeps language concrete; no marketing tone.
function bandHoverContent(band, kind) {
  if (kind === 'ai') {
    switch (band) {
      case 'Excellent':
        return <p>Every available AI signal — Perceptual, Aesthetic, and (when there's a face) Face quality — reads as Excellent.</p>
      case 'Good':
        return <p>The weakest AI signal is in the Good band. The photo reads cleanly across noise, composition, and (if applicable) face quality.</p>
      case 'Fair':
        return <p>At least one AI signal is Fair — could be visible noise, weak composition, or a soft / poorly-framed face. Expand to see which.</p>
      case 'Poor':
        return <p>At least one AI signal is Poor — a real weakness in noise, composition, or face quality. Expand to see which signal flagged.</p>
      default:
        return null
    }
  }
  if (kind === 'content') {
    switch (band) {
      case 'Excellent':
        return <p>Every available content axis — subject prominence, background, eye contact (when there's a face), decisive moment — reads as Strong.</p>
      case 'Good':
        return <p>The weakest content axis is in the Good band. The photo reads decently across the perception axes.</p>
      case 'Fair':
        return <p>At least one content axis is Weak — could be a cluttered background, an unclear subject, or a static rather than decisive moment. Expand to see which.</p>
      case 'Poor':
        return <p>At least one content axis is Poor — meaningful weakness in how the photo reads. Expand to see which axis flagged.</p>
      default:
        return null
    }
  }
  return null
}

// aiQualitySummary — single band word for the AI Quality section, derived
// from the worst-available signal's band across Perceptual (iqaLabel),
// Aesthetic (aestheticLabel), and the composite Face quality (iqaLabel).
// Null signals are ignored, not zeroed: a portrait without a face still
// reads as Good if Perceptual and Aesthetic are both Good. Returns null
// when no AI signal exists (e.g. analysis hasn't run yet).
function aiQualitySummary(image) {
  const bands = []
  if (image.iqa_score != null)       bands.push(iqaLabel(image.iqa_score))
  if (image.aesthetic_score != null) bands.push(aestheticLabel(image.aesthetic_score))
  const face = faceQualityScore(image)
  if (face != null)                  bands.push(iqaLabel(face))
  if (bands.length === 0) return null
  // Order: Poor < Fair < Good < Excellent — return the lowest band present.
  for (const tier of ['Poor', 'Fair', 'Good', 'Excellent']) {
    if (bands.includes(tier)) return tier
  }
  return null
}

// contentSignalsSummary — single band word for Content Signals, derived
// the same way as aiQualitySummary but mapping Content's section-specific
// band vocabulary (Strong/Decent/Weak/Poor for plain axes, Clean/Tidy/
// Busy/Cluttered for distraction) to the universal four-tier scale.
function contentSignalsSummary(image) {
  const tiers = []
  if (image.subject_prominence_score     != null) tiers.push(contentTier(image.subject_prominence_score, 'subject_prominence'))
  if (image.background_distraction_score != null) tiers.push(contentTier(image.background_distraction_score, 'background_distraction'))
  if (image.eye_contact_score            != null) tiers.push(contentTier(image.eye_contact_score, 'eye_contact'))
  if (image.decisive_moment_score        != null) tiers.push(contentTier(image.decisive_moment_score, 'decisive_moment'))
  if (tiers.length === 0) return null
  const minTier = Math.min(...tiers)
  return ['Poor', 'Fair', 'Good', 'Excellent'][minTier - 1]
}

// Section — collapsible block with always-visible header showing label + chips.
// State persists per-section in localStorage (key: pca.detail.section.<id>).
// Children only render when expanded; expanded state also gets a subtle
// rounded stroke so the section reads as a contained group.
function Section({ id, label, chips, tooltip, defaultOpen = false, children }) {
  const [open, setOpen] = useLocalStorageState(`pca.detail.section.${id}`, defaultOpen)
  return (
    <div className={`-mx-3 rounded-lg border p-3 transition-colors ${open ? 'border-[rgba(255,255,255,0.10)]' : 'border-transparent hover:border-[rgba(255,255,255,0.10)]'}`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex flex-wrap items-center gap-x-2 gap-y-1.5 text-left hover:opacity-80 transition-opacity"
        aria-expanded={open}
      >
        <p className="label flex-shrink-0">{label}</p>
        {tooltip && (
          <span onClick={e => e.stopPropagation()} className="inline-flex items-center">
            <InfoTooltip>{tooltip}</InfoTooltip>
          </span>
        )}
        <div className="flex flex-wrap items-center gap-1.5 ml-auto">
          {chips.map((c, i) => c.band != null
            ? <BandChip key={i} {...c} />
            : <ScoreChip key={i} {...c} />)}
        </div>
      </button>
      {open && <div className="space-y-3 mt-3">{children}</div>}
    </div>
  )
}

// ClippingOverlays — Lightroom-style coloured overlay on the DetailView
// preview that tints clipped pixels (highlights amber, shadows cyan).
// Each overlay is a transparent PNG fetched from /clipping-mask/{id}?mode=…
// and rendered as an absolutely-positioned `<img>` matching the preview's
// dimensions. `mix-blend-mode: screen` makes the tint additive against the
// photo so it reads on dark backgrounds (where a flat overlay would be
// invisible). `pointer-events-none` keeps click-to-zoom working.
//
// We mount each mask only when its toggle is on (no eager fetch of both),
// and rely on the browser's HTTP cache to make repeated toggling instant.
function ClippingOverlays({ imageId, showShadows, showHighlights }) {
  // Identical CSS classes to the preview <img>: grid stacks them in the same
  // cell, and `object-contain` + `max-h-full max-w-full` produces an identical
  // computed box because both share the same source aspect ratio (the mask
  // PNG matches the preview's exact dimensions). `gridArea: '1 / 1'` is what
  // makes the children stack instead of flowing.
  // Without `mix-blend-mode` — flat opacity overlay so the tint reads
  // consistently against ANY background brightness. `screen` blend mode
  // disappears against white pixels (which is exactly where clipped
  // highlights live), making the overlay invisible where it matters most.
  const cls = 'max-h-full max-w-full object-contain pointer-events-none block'
  const style = { gridArea: '1 / 1' }

  return (
    <>
      {showHighlights && (
        <img src={`${API}/clipping-mask/${imageId}?mode=highlights`} alt="" className={cls} style={style} />
      )}
      {showShadows && (
        <img src={`${API}/clipping-mask/${imageId}?mode=shadows`} alt="" className={cls} style={style} />
      )}
    </>
  )
}

// HistogramSection — collapsible RGB histogram with shadow/highlight clipping
// callouts. Fetches lazily on first expand (and on image change) so closed
// sections cost nothing.
//
// Visual: 96px-tall SVG with R/G/B curves drawn at 50% alpha; overlaps blend
// additively against the dark panel, matching the look of every photo
// histogram you've seen in Lightroom/Capture One. Mid-gray luminance line
// sits underneath as a reference. Numeric clip stats below; "Shadows" and
// "Highlights" pills toggle vertical band overlays at the 0 and 255 edges
// to make the clipped bins visually obvious.
function HistogramSection({ imageId, showShadows, showHighlights, onToggleShadows, onToggleHighlights }) {
  const [open, setOpen] = useLocalStorageState('pca.detail.section.histogram', false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  // Fetch only when the section is open (or has been opened) and the image
  // changed. Closed-by-default sections stay free until the user expands.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setData(null)
    setError(null)
    fetch(`${API}/histogram/${imageId}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [imageId, open])

  // Headline chip would be misleading: ScoreChip rounds to int, so "0.20%
  // clipped" displays as "0" — a false negative. Drop the chip; the per-
  // toggle pills below already show the percentages with proper formatting.

  return (
    <div className={`-mx-3 rounded-lg border p-3 transition-colors ${open ? 'border-[rgba(255,255,255,0.10)]' : 'border-transparent hover:border-[rgba(255,255,255,0.10)]'}`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 text-left hover:opacity-80 transition-opacity"
        aria-expanded={open}
      >
        <p className="label flex-shrink-0">Histogram</p>
        <span className="ml-auto">
          <Chevron open={open} />
        </span>
      </button>

      {open && (
        <div className="space-y-3 mt-3">
          {!data && !error && (
            <p className="text-xs text-[#4a4a4a] italic animate-pulse">Computing histogram…</p>
          )}
          {error && (
            <p className="text-xs text-[#C97B7B]">Couldn't load histogram: {error}</p>
          )}
          {data && (
            <>
              <HistogramCanvas data={data} showShadows={showShadows} showHighlights={showHighlights} />

              {/* Clipping toggles + per-toggle stats. Active state also drives
                  the overlay tint on the preview image (state lives in
                  DetailView, not here). */}
              <div className="flex gap-1.5">
                <ClippingToggle
                  label="Shadows"
                  icon={Moon}
                  pct={data.clip_lo.visible}
                  active={showShadows}
                  onClick={onToggleShadows}
                  tint={TINT_SHADOW}
                />
                <ClippingToggle
                  label="Highlights"
                  icon={Sun}
                  pct={data.clip_hi.visible}
                  active={showHighlights}
                  onClick={onToggleHighlights}
                  tint={TINT_HIGHLIGHT}
                />
              </div>

              {/* Per-channel breakdown — only shown when a toggle is active. */}
              {(showShadows || showHighlights) && (
                <div className="text-[11px] text-[#9c9c9d] font-mono space-y-0.5 pt-1">
                  {showShadows && (
                    <div className="flex justify-between">
                      <span>Shadows clipped (R/G/B)</span>
                      <span>{fmtPct(data.clip_lo.r)} · {fmtPct(data.clip_lo.g)} · {fmtPct(data.clip_lo.b)}</span>
                    </div>
                  )}
                  {showHighlights && (
                    <div className="flex justify-between">
                      <span>Highlights clipped (R/G/B)</span>
                      <span>{fmtPct(data.clip_hi.r)} · {fmtPct(data.clip_hi.g)} · {fmtPct(data.clip_hi.b)}</span>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// HistogramCanvas — SVG renderer for the four channel curves. Filled paths
// with low alpha overlap additively against the dark panel; the result is
// the familiar Lightroom look without needing a real Canvas element. Width
// is set to the natural panel width (~336px after the section's -mx-3 +
// p-3); height is fixed at 96px so the section reads as compact data.
function HistogramCanvas({ data, showShadows, showHighlights }) {
  const W = 320
  const H = 96
  // Normalize against the global max across all four channels so scale is
  // shared — otherwise the luminance curve (always tallest because it sums
  // three channels' worth of pixels) would dwarf RGB.
  const maxVal = Math.max(
    1,
    ...data.r, ...data.g, ...data.b, ...data.lum,
  )
  const path = bins => buildAreaPath(bins, W, H, maxVal)

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-24 block">
        {/* Faint baseline so the area paths read as filled. */}
        <line x1={0} y1={H - 0.5} x2={W} y2={H - 0.5} stroke="rgba(255,255,255,0.08)" strokeWidth={1} />

        {/* Luminance — neutral white underlay for tonal context. */}
        <path d={path(data.lum)} fill="rgba(255,255,255,0.18)" />

        {/* Per-channel curves — additive screening simulated with low alpha. */}
        <path d={path(data.r)} fill="rgba(232,107,107,0.55)" />
        <path d={path(data.g)} fill="rgba(125,200,125,0.55)" />
        <path d={path(data.b)} fill="rgba(91,150,232,0.55)" />

        {/* Clipping zone bands — *under* the curves (drawn before each curve
            visually, but SVG paint order is render order, so we drew curves
            first; these go on top with very low alpha so they read as a
            zone marker rather than fake data). 0.06 alpha is enough to mark
            the region without making empty bins look populated. */}
        {showShadows && (
          <rect x={0} y={0} width={W * (6 / 256)} height={H} fill="rgba(91,184,212,0.08)" />
        )}
        {showHighlights && (
          <rect x={W * (250 / 256)} y={0} width={W * (6 / 256)} height={H} fill="rgba(232,184,74,0.08)" />
        )}
      </svg>

      {/* Subtle 0 / 255 axis labels, only shown when a clipping band is on. */}
      {(showShadows || showHighlights) && (
        <div className="flex justify-between text-[9px] text-[#6a6b6c] font-mono mt-0.5 px-0.5">
          <span>0</span>
          <span>255</span>
        </div>
      )}
    </div>
  )
}

// buildAreaPath — turn a 256-bin array into an SVG path string for a filled
// area chart. Bins are mapped to evenly spaced x positions; counts to height.
// The path closes along the baseline so `fill` produces a solid area.
function buildAreaPath(bins, W, H, maxVal) {
  const n = bins.length
  const dx = W / (n - 1)
  let d = `M0,${H}`
  for (let i = 0; i < n; i++) {
    const x = i * dx
    const y = H - (bins[i] / maxVal) * H
    d += ` L${x.toFixed(2)},${y.toFixed(2)}`
  }
  d += ` L${W},${H} Z`
  return d
}

function ClippingToggle({ label, pct, active, onClick, tint, icon: Icon }) {
  const activeCls = tint
    ? `${tint.bg} ${tint.border} ${tint.text}`
    : 'bg-[#1b1c1e] border-[rgba(255,255,255,0.20)] text-[#f9f9f9]'
  const idleCls = 'bg-transparent border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:text-[#cecece]'
  return (
    <button
      onClick={onClick}
      className={`flex-1 py-1.5 px-2 rounded-md text-[11px] font-medium border transition-opacity flex items-center justify-between gap-1.5 ${active ? activeCls : idleCls}`}
      aria-pressed={active}
    >
      <span className="inline-flex items-center gap-1.5">
        {Icon && <Icon size={13} />}
        {label}
      </span>
      <span className="font-mono tabular-nums text-[10px]">{fmtPct(pct)}</span>
    </button>
  )
}

// fmtPct — compact percent formatter. Hides 0.000 noise (common for
// well-exposed shots) by collapsing to "0%"; everything else gets one decimal
// so 0.05% shadow clip doesn't round away.
function fmtPct(v) {
  if (v == null) return '—'
  if (v < 0.01) return '0%'
  if (v < 1) return `${v.toFixed(2)}%`
  return `${v.toFixed(1)}%`
}

// Band tint palette — a stepped cool-slate luminance scale used by every
// score chip in the panel (Technical, AI sub-chips, Content sub-chips,
// Personal). Quality reads as luminance + warmth: high band is bright/cool,
// low band is dim/warm. Distinct from the decision palette (sage/amber/
// coral) on purpose — chips communicate quality bands; decision colors are
// reserved for K/M/X buttons, ImageCard rings, and decision badges. This
// separation eliminates the "is this amber because of Maybe or because of
// Fair quality?" ambiguity that the previous shared palette created.
const TINT_TIER_4 = { border: 'border-[rgba(200,216,228,0.45)]', bg: 'bg-[rgba(200,216,228,0.12)]', text: 'text-[#C8D8E4]' }  // Pearl — Excellent
const TINT_TIER_3 = { border: 'border-[rgba(156,173,187,0.45)]', bg: 'bg-[rgba(156,173,187,0.12)]', text: 'text-[#9CADBB]' }  // Steel — Good
const TINT_TIER_2 = { border: 'border-[rgba(160,148,128,0.45)]', bg: 'bg-[rgba(160,148,128,0.12)]', text: 'text-[#A09480]' }  // Stone — Fair
const TINT_TIER_1 = { border: 'border-[rgba(138,120,120,0.45)]', bg: 'bg-[rgba(138,120,120,0.12)]', text: 'text-[#8A7878]' }  // Iron — Poor

// Histogram clipping toggle tints — cyan for shadows, amber for highlights.
// These encode the actual tint color of the on-photo overlay (the toggle
// pill matches the color the overlay paints), NOT a quality band. Kept
// separate from the band palette so the meaning stays unambiguous.
const TINT_SHADOW    = { border: 'border-[rgba(91,184,212,0.45)]',  bg: 'bg-[rgba(91,184,212,0.12)]',  text: 'text-[#5BB8D4]' }
const TINT_HIGHLIGHT = { border: 'border-[rgba(232,184,74,0.45)]',  bg: 'bg-[rgba(232,184,74,0.12)]',  text: 'text-[#E8B84A]' }

// Mirror of phase2_quality/iqa_scorer.py band cutoffs (75/55/35).
function iqaTint(score) {
  if (score == null) return null
  if (score >= 75) return TINT_TIER_4
  if (score >= 55) return TINT_TIER_3
  if (score >= 35) return TINT_TIER_2
  return TINT_TIER_1
}

// Mirror of phase2_quality/aesthetic_scorer.py band cutoffs (46/42/36).
// TOPIQ-IAA's distribution centers around 42 with stdev ~5 — narrower than
// the previous CLIP+SAC scorer. Cutoffs partition into top 15% / 50% / 85%.
function aestheticTint(score) {
  if (score == null) return null
  if (score >= 46) return TINT_TIER_4
  if (score >= 42) return TINT_TIER_3
  if (score >= 36) return TINT_TIER_2
  return TINT_TIER_1
}

// Composite face quality reuses TOPIQ-band thresholds — it's a 0–100 score
// derived in faceQualityScore() and benefits from the same visual ladder.
const faceTint = iqaTint

// Technical Overall — uses the same 75/55/35 bands as TOPIQ so the chip
// reads consistently with sharpness/exposure quality intuitions.
function technicalTint(score) {
  if (score == null) return null
  if (score >= 75) return TINT_TIER_4
  if (score >= 55) return TINT_TIER_3
  if (score >= 35) return TINT_TIER_2
  return TINT_TIER_1
}

// Personal Your-model — same cutoffs. Personal scores can swing wider than
// technical ones (delta of ±25 from base), but the band reading still maps
// cleanly: a personal_score below 35 means the model thinks this is unlikely
// to keep, regardless of the underlying technical quality.
const personalTint = technicalTint

// AiQualitySection — Perceptual + Aesthetic + composite Face quality.
// Header carries ONE BandChip (the worst-available band across the
// section's signals) so the chip flags "is this layer poor?" without
// duplicating the values shown by the bars on expand. Drill down to the
// per-metric bars when you need to see which signal is weak.
function AiQualitySection({ image }) {
  const iqa       = image.iqa_score
  const aesthetic = image.aesthetic_score
  const face      = faceQualityScore(image)

  const band = aiQualitySummary(image)
  const chips = band ? [{ band, hoverInfo: bandHoverContent(band, 'ai') }] : []

  return (
    <Section
      id="ai-quality"
      label="AI Quality"
      chips={chips}
      tooltip={
        <>What machine-learning models see in this photo, beyond pixel-level measurements. The header band reflects the <em className="text-[#f9f9f9] not-italic font-semibold">weakest</em> available signal across <strong className="text-[#f9f9f9] font-semibold">Perceptual</strong> (artifact-free cleanness, TOPIQ-NR), <strong className="text-[#f9f9f9] font-semibold">Aesthetic</strong> (composition / color / mood, TOPIQ-IAA), and <strong className="text-[#f9f9f9] font-semibold">Face</strong> (sharpness / eye openness / framing, only when a face is detected). Expand to see each signal's bar.</>
      }
    >
      {iqa != null && (
        <AiQualityRow
          label="Perceptual"
          band={iqaLabel(iqa)}
          tint={iqaTint(iqa)}
          value={iqa}
          tooltip={<>TOPIQ neural model. <strong className="text-[#f9f9f9] font-semibold">Above 75</strong> excellent, <strong className="text-[#f9f9f9] font-semibold">55–75</strong> good, <strong className="text-[#f9f9f9] font-semibold">35–55</strong> fair, <strong className="text-[#f9f9f9] font-semibold">below 35</strong> poor — usually visible noise, blur, or compression artifacts.</>}
        />
      )}
      {aesthetic != null && (
        <AiQualityRow
          label="Aesthetic"
          band={aestheticLabel(aesthetic)}
          tint={aestheticTint(aesthetic)}
          value={aesthetic}
          tooltip={<>TOPIQ-IAA model trained on the AVA dataset of human aesthetic ratings. <strong className="text-[#f9f9f9] font-semibold">Above 46</strong> excellent, <strong className="text-[#f9f9f9] font-semibold">42–46</strong> good, <strong className="text-[#f9f9f9] font-semibold">36–42</strong> fair, <strong className="text-[#f9f9f9] font-semibold">below 36</strong> poor. Distribution is narrow by design — use it for ranking within a shoot rather than as an absolute verdict.</>}
        />
      )}
      {face != null && (
        <AiQualityRow
          label="Face quality"
          band={iqaLabel(face)}
          tint={faceTint(face)}
          value={face}
          subtitle={faceSubtitle(image)}
          tooltip={<>Composite of <strong className="text-[#f9f9f9] font-semibold">face sharpness</strong>, <strong className="text-[#f9f9f9] font-semibold">eye openness</strong>, and <strong className="text-[#f9f9f9] font-semibold">framing</strong> (how much of the frame the face occupies). Same band cutoffs as Perceptual. Only shown when a face is detected.</>}
        />
      )}
    </Section>
  )
}

// Content-axis band thresholds, calibrated from a SigLIP-2 60-photo distribution
// survey (2026-05-12). All four axes are sigmoid outputs in 0–1 but their
// distributions diverge meaningfully — eye_contact is squeezed into ~0.40–0.57
// while subject_prominence spans ~0.20–0.65. A single shared ladder would
// flatten eye_contact entirely, so we keep per-axis [p25, p50, p75] cutoffs
// that divide each axis's population into roughly even quartiles.
//
// background_distraction is the only axis where high = bad; we keep its
// cutoffs defined on the raw 0–1 axis ("good is low") and invert at lookup
// time, so the same threshold table reads uniformly.
const CONTENT_AXIS_CUTOFFS = {
  subject_prominence:     [0.50, 0.42, 0.35],  // tier 4 ≥ p75, tier 3 ≥ p50, tier 2 ≥ p25, else tier 1
  background_distraction: [0.45, 0.38, 0.32],  // inverted: lower raw = better
  eye_contact:            [0.51, 0.48, 0.47],
  decisive_moment:        [0.52, 0.48, 0.44],
}

const CONTENT_AXIS_KIND = {
  subject_prominence:     'plain',
  background_distraction: 'distraction',
  eye_contact:            'plain',
  decisive_moment:        'plain',
}

function contentTier(value, axis) {
  if (value == null) return null
  const cutoffs = CONTENT_AXIS_CUTOFFS[axis]
  if (!cutoffs) return null
  const [t4, t3, t2] = cutoffs
  // For 'plain' axes, higher raw = better. For 'distraction', lower raw = better,
  // so the comparisons flip: a raw 0.32 (≤ p25 of distraction) is tier 4.
  if (CONTENT_AXIS_KIND[axis] === 'distraction') {
    if (value <= t2) return 4
    if (value <= t3) return 3
    if (value <= t4) return 2
    return 1
  }
  if (value >= t4) return 4
  if (value >= t3) return 3
  if (value >= t2) return 2
  return 1
}

const PLAIN_LABELS       = ['Poor', 'Weak', 'Decent', 'Strong']
const DISTRACTION_LABELS = ['Cluttered', 'Busy', 'Tidy', 'Clean']

function contentBand(value, axis) {
  const tier = contentTier(value, axis)
  if (tier == null) return null
  const labels = CONTENT_AXIS_KIND[axis] === 'distraction' ? DISTRACTION_LABELS : PLAIN_LABELS
  return labels[tier - 1]
}

function contentTint(value, axis) {
  const tier = contentTier(value, axis)
  if (tier == null) return null
  return [TINT_TIER_1, TINT_TIER_2, TINT_TIER_3, TINT_TIER_4][tier - 1]
}

// ContentSignalsSection — SigLIP zero-shot content axes. Hidden when no axis
// has data (pre-v39 rows show NULL for every column). eye_contact also hides
// when face_detected is false. Section header carries ONE BandChip (the
// worst-available band across the four axes); expand reveals the per-axis
// bars. Scene tag + confidence shows at the top of the expand body.
function ContentSignalsSection({ image }) {
  const sp = image.subject_prominence_score
  const bd = image.background_distraction_score
  const ec = image.eye_contact_score   // null when no face detected
  const dm = image.decisive_moment_score

  if (sp == null && bd == null && ec == null && dm == null) return null

  const band = contentSignalsSummary(image)
  const chips = band ? [{ band, hoverInfo: bandHoverContent(band, 'content') }] : []

  return (
    <Section
      id="content-signals"
      label="Content signals"
      chips={chips}
      tooltip={
        <>Zero-shot perception axes from the same SigLIP model that powers burst grouping. The header band reflects the <em className="text-[#f9f9f9] not-italic font-semibold">weakest</em> available axis across <strong className="text-[#f9f9f9] font-semibold">Subject prominence</strong>, <strong className="text-[#f9f9f9] font-semibold">Background</strong> (cleanliness, high is good), <strong className="text-[#f9f9f9] font-semibold">Eye contact</strong> (portraits only), and <strong className="text-[#f9f9f9] font-semibold">Decisive moment</strong>. Expand to see each axis's bar.</>
      }
    >
      {/* Scene classification — moved here from EXIF (2026-05-10). Scene
          is a zero-shot perception read from the same SigLIP model that
          drives the four axes below, not camera metadata, so it belongs
          in Content Signals. Hidden when no scene was inferred (pre-v32
          rows or low-confidence images). */}
      {image.scene && (
        <div className="flex items-center justify-between text-[12px] -mt-1 pb-1 border-b border-[rgba(255,255,255,0.05)]">
          <span className="text-[#9c9c9d]">Scene</span>
          <span className="flex items-center gap-1.5">
            <span className="text-[#f9f9f9] capitalize">{image.scene}</span>
            {image.scene_confidence != null && (
              <span className="text-[#6a6b6c] text-[10px] font-mono tabular-nums">
                {Math.round(image.scene_confidence * 100)}%
              </span>
            )}
          </span>
        </div>
      )}
      {sp != null && (
        <AiQualityRow
          label="Subject prominence"
          band={contentBand(sp, 'subject_prominence')}
          tint={contentTint(sp, 'subject_prominence')}
          value={Math.round(sp * 100)}
        />
      )}
      {bd != null && (
        <AiQualityRow
          label="Background"
          band={contentBand(bd, 'background_distraction')}
          tint={contentTint(bd, 'background_distraction')}
          value={Math.round((1 - bd) * 100)}
        />
      )}
      {ec != null && (
        <AiQualityRow
          label="Eye contact"
          band={contentBand(ec, 'eye_contact')}
          tint={contentTint(ec, 'eye_contact')}
          value={Math.round(ec * 100)}
        />
      )}
      {dm != null && (
        <AiQualityRow
          label="Decisive moment"
          band={contentBand(dm, 'decisive_moment')}
          tint={contentTint(dm, 'decisive_moment')}
          value={Math.round(dm * 100)}
        />
      )}
    </Section>
  )
}

// AiQualityRow — single labelled bar inside AiQualitySection. Label + band
// label sit above the bar; the bar itself uses neutral white per DESIGN.md
// §4.4. The band's tinted text color carries the semantic encoding without
// painting the bar (which would conflict with the design system rule).
// Optional tooltip surfaces band cutoffs and what the underlying scorer
// actually measures so the score isn't a black box.
function AiQualityRow({ label, band, tint, value, subtitle, tooltip }) {
  // The info content used to live behind a per-row (i) icon. The row itself
  // is now the trigger — hovering the bar opens the same popover, dropping
  // the per-row icon noise. The wrapping HoverPopover renders as a block so
  // the existing layout (label/band row + bar + subtitle) is unchanged.
  const body = (
    <div className="w-full">
      <div className="flex items-baseline justify-between mb-1 gap-2">
        <p className="text-[14px] text-[#cecece] truncate">{label}</p>
        {band && (
          <span className={`text-[11px] flex-shrink-0 ${tint?.text ?? 'text-[#9c9c9d]'}`}>{band}</span>
        )}
      </div>
      <ScoreBar value={value} />
      {subtitle && (
        <p className="text-xs text-[#9c9c9d] mt-1">{subtitle}</p>
      )}
    </div>
  )
  return tooltip ? <HoverPopover block content={tooltip}>{body}</HoverPopover> : body
}

// faceSubtitle — compact single-line summary of face-specific facts for the
// Face row's subtitle slot. Surfaces what the composite Face quality number
// doesn't carry: how many faces, whether eyes are open (per-face when the
// v37 column is populated), how much of the frame the face fills. Joined
// with middle-dots; missing signals are skipped so empty data doesn't
// leave dangling separators.
function faceSubtitle(image) {
  const parts = []
  if (image.face_count != null) {
    parts.push(`${image.face_count} face${image.face_count !== 1 ? 's' : ''}`)
  }
  // Per-face eyes from schema v37 — fall back to singular eyes_open for
  // older rows so pre-v37 photos still report something useful.
  let eyesNote = null
  if (image.faces_eyes_open_json) {
    try {
      const arr = JSON.parse(image.faces_eyes_open_json)
      if (Array.isArray(arr) && arr.length > 0) {
        const open   = arr.filter(Boolean).length
        const closed = arr.length - open
        eyesNote = closed > 0 ? `${open} eyes open, ${closed} closed` : `eyes open`
      }
    } catch { /* malformed JSON — fall through to legacy */ }
  }
  if (eyesNote == null && image.eyes_open != null) {
    eyesNote = image.eyes_open ? 'eyes open' : 'eyes closed'
  }
  if (eyesNote) parts.push(eyesNote)
  if (image.face_size_ratio != null) {
    parts.push(`${(image.face_size_ratio * 100).toFixed(0)}% of frame`)
  }
  return parts.length ? parts.join(' · ') : null
}

// FEATURE_DESCRIPTIONS — short human explanation for each feature the personal
// model tracks. Mirrors phase3_learning/feature_extractor.py:_COLUMNS — when
// _COLUMNS gains a new feature, add its description here too.
const FEATURE_DESCRIPTIONS = {
  sharpness_score:      'overall frame sharpness',
  exposure_score:       'exposure accuracy and tonal balance',
  iqa_score:            'AI-assessed perceptual cleanness (TOPIQ)',
  aesthetic_score:      'predicted composition and mood appeal (TOPIQ-IAA)',
  highlight_clip_pct:   'percent of pixels blown to pure white',
  shadow_clip_pct:      'percent of pixels crushed to pure black',
  shake_detected:       'evidence of camera shake or motion blur',
  face_present:         'whether a face is in frame at all',
  face_detected:        'whether the face detector fired',
  face_count:           'how many faces appear in the frame',
  face_sharpness_score: 'sharpness measured on the detected face',
  eyes_open:            'whether the primary face has open eyes',
  eye_openness_ratio:   'how wide the eyes are open (squint vs. wide)',
  face_size_ratio:      'how much of the frame the face occupies',
  smile_score:          'how strongly the subject is smiling',
  mouth_open_score:     'whether the mouth is open (talking, laughing)',
  focal_length_mm:      'lens focal length you tend to favor',
  aperture:             'aperture setting (depth-of-field preference)',
  iso:                  'ISO setting (low-light vs. clean shooting)',
  scene_is_portrait:    'whether the photo reads as a portrait',
  scene_is_landscape:   'whether the photo reads as a landscape',
  scene_is_street:      'whether the photo reads as street',
  scene_is_night:       'whether the photo reads as a night scene',
  scene_is_macro:       'whether the photo reads as macro / close-up',
  scene_is_indoor:      'whether the photo reads as indoor',
  scene_is_action:      'whether the photo reads as action / motion',
  scene_is_water:       'whether the photo reads as a water scene',
  subject_prominence_score:    'how clearly the subject reads as the focal point',
  background_distraction_score:'how cluttered or busy the backdrop is',
  eye_contact_score:           'whether the subject looks at the camera (portraits)',
  decisive_moment_score:       'whether the photo captures a decisive moment',
}

const featureDescription = (rawName) => FEATURE_DESCRIPTIONS[rawName] ?? null

// Chip popovers — one short paragraph on the mechanism, then a value-specific
// reading (band label or component breakdown). The section-level InfoTooltip
// already explains what the section is for; these focus on interpreting the
// bare number on this particular photo.

function bandReading(value, band) {
  if (value == null || !band) return null
  const color =
    band === 'Excellent' ? 'text-[#C8D8E4]' :
    band === 'Good'      ? 'text-[#9CADBB]' :
    band === 'Fair'      ? 'text-[#A09480]' :
                           'text-[#8A7878]'
  return { word: band.toLowerCase(), color }
}

function OverallChipExplanation({ image }) {
  const sharp = image.sharpness_score
  const expo  = image.exposure_score
  return (
    <div className="space-y-2">
      <p>
        Pixel-level quality from sharpness and exposure, weighted{' '}
        <strong className="text-[#f9f9f9] font-semibold">65 / 35</strong> — sharpness counts more because blur cannot be recovered.
      </p>
      {(sharp != null || expo != null) && (
        <p className="text-[#9c9c9d]">
          This photo:{' '}
          {sharp != null && <>sharpness <span className="text-[#cecece] font-mono">{sharp.toFixed(0)}</span></>}
          {sharp != null && expo != null && ' · '}
          {expo  != null && <>exposure <span className="text-[#cecece] font-mono">{expo.toFixed(0)}</span></>}.
        </p>
      )}
    </div>
  )
}


// PersonalSection — wrapper that renders Personal Scoring in one of two
// states: READY (model has reached the readiness gate — training_size ≥
// 50 AND beats the baseline — and this image has a prediction → shows
// the personal_score chip + LearnedSignals breakdown) or LEARNING (model
// is still collecting samples toward the readiness gate → shows a
// "Learning" status chip and progress line). The user sees the feature
// exists either way, but we don't surface a score until the model is
// actually trustworthy. The "learning" model_status still computes a
// personal_score on every image, but the score is unreliable below 50
// samples — showing it would mislead. Section never auto-expands.
function PersonalSection({ image, modelInfo }) {
  const modelReady     = modelInfo?.model_status === 'ready'
  const hasScore       = modelReady && image.personal_score != null
  const inLearningMode = modelInfo && !modelReady

  // Edge cases: no modelInfo at all, or model is ready but this image
  // has no prediction yet (e.g. unanalysed since the model first trained).
  // Hide the section rather than render an empty shell.
  if (!hasScore && !inLearningMode) return null

  if (hasScore) {
    return (
      <Section
        id="personal"
        label="Personal scoring"
        chips={[{
          label: 'Your model',
          value: image.personal_score,
          tint: personalTint(image.personal_score),
          hoverInfo: <PersonalChipExplanation image={image} modelInfo={modelInfo} />,
        }]}
        tooltip={
          <>Your personal taste model. Trained on photos you've kept and rejected — it learns the patterns in <em className="text-[#f9f9f9] not-italic font-semibold">your</em> decisions and adjusts the technical/AI scores up or down to predict whether you'll keep this one. Retrains every time you cull more photos.</>
        }
      >
        <LearnedSignals modelInfo={modelInfo} />
      </Section>
    )
  }

  // Learning mode: model isn't yet trustworthy. There are two sub-phases —
  // pre-first-training (need to hit MIN_DECISIONS=30) and post-first-training-
  // but-below-readiness-gate (need to hit ~50 samples). Show progress
  // toward whichever target is the next meaningful milestone.
  const status     = modelInfo.model_status
  const decided    = modelInfo.decided_count ?? 0
  const trained    = modelInfo.training_size ?? 0
  const minDec     = modelInfo.min_decisions ?? 30
  // Pre-training: progress = decided / 30. Post-training: progress =
  // training_size / 50. We tell the user which milestone is next.
  const preTraining = status === 'untrained'
  const target      = preTraining ? minDec : 50
  const current     = preTraining ? decided : trained
  const remaining   = Math.max(0, target - current)
  const milestone   = preTraining ? 'first training pass' : 'reliable predictions'

  return (
    <Section
      id="personal"
      label="Personal scoring"
      chips={[{
        band: 'Learning',
        hoverInfo: (
          <div className="space-y-2">
            <p>
              Your personal taste model {preTraining ? 'is collecting decisions before its first training pass' : 'has started training but isn\'t yet reliable'}. It needs{' '}
              <strong className="text-[#f9f9f9] font-semibold">{target}</strong>{' '}
              {preTraining ? 'decisions' : 'training samples'} to reach the {milestone} —{' '}
              {remaining > 0
                ? <>still <strong className="text-[#f9f9f9] font-semibold">{remaining}</strong> to go.</>
                : <>{preTraining ? 'training will kick in on the next decision.' : 'almost there.'}</>}
            </p>
            <p className="text-[#9c9c9d]">
              Until then, this photo's score isn't shown — the model would be guessing.
            </p>
          </div>
        ),
      }]}
      tooltip={
        <>Your personal taste model learns from photos you've kept and rejected. Per-photo predictions appear once it crosses the readiness threshold (50 training samples and beats the baseline).</>
      }
    >
      <p className="text-[12px] text-[#9c9c9d]">
        <span className="font-mono tabular-nums text-[#cecece]">{current}</span>
        {' of '}
        <span className="font-mono tabular-nums text-[#cecece]">{target}</span>
        {' '}{preTraining ? 'decisions' : 'training samples'} — {remaining > 0
          ? <>{remaining} more to {milestone}.</>
          : <>at the {milestone}.</>}
      </p>
    </Section>
  )
}

// PersonalChipExplanation — content for the Personal chip's hover popover.
// Surfaces the two facts that the bare score doesn't communicate:
//   1. Delta from technical baseline — is your model rating this above or
//      below what the raw quality signals would suggest?
//   2. Which feature most strongly drives your model overall, plus a short
//      description so the name isn't opaque.
function PersonalChipExplanation({ image, modelInfo }) {
  const personal  = image.personal_score
  const technical = image.overall_score
  const delta     = (personal != null && technical != null) ? personal - technical : null
  const topRaw    = modelInfo?.top_features?.[0]?.name
  const topName   = topRaw?.replace(/_score$/, '').replace(/_/g, ' ')
  const topDesc   = topRaw ? featureDescription(topRaw) : null

  let direction = null
  if (delta != null) {
    if      (delta >=  1) direction = { word: 'higher', color: 'text-[#7DB89A]' }
    else if (delta <= -1) direction = { word: 'lower',  color: 'text-[#C97B7B]' }
    else                  direction = { word: 'in line', color: 'text-[#9c9c9d]' }
  }

  return (
    <div className="space-y-2">
      {delta != null && direction && (
        <p>
          Your model rates this{' '}
          <strong className={`font-semibold ${direction.color}`}>
            {Math.abs(delta).toFixed(0)} points {direction.word}
          </strong>
          {' '}than its technical score ({personal.toFixed(0)} vs {technical.toFixed(0)}).
        </p>
      )}
      {topName && (
        <p>
          Top influence overall: <strong className="text-[#f9f9f9] font-semibold">{topName}</strong>
          {topDesc && <span className="text-[#9c9c9d]"> — {topDesc}</span>}.
        </p>
      )}
    </div>
  )
}

// Total feature count from phase3_learning/feature_extractor.py — 27 features:
// 19 base + 8 binary scene labels (scene_is_portrait … scene_is_water, added 2026-05-06).
const TOTAL_FEATURES = 27

// Feature-identity palette for LearnedSignals stacked-bar segments. These
// colors mean "which feature" — NOT "how good." None of the decision colors
// (Keep Sage / Maybe Amber / Reject Coral) appear, and selection Cool Cyan
// is also avoided, so segments can't be misread as quality bands or
// decisions. Slot 1 is Indigo on purpose — it's the Personal-section
// identity color and ties the bar to the section it lives in. Bronze
// (#8C7A5E) is reserved separately for the pooled "other N" segment.
// Each slot jumps ~150° on the color wheel so adjacent segments never
// share a hue family; all entries are desaturated to keep them subordinate
// to the photo content above.
const SIGNAL_COLORS = [
  'bg-[#7B82C9]', // indigo — anchored to Personal section identity
  'bg-[#D49A5B]', // warm ochre
  'bg-[#8C9BD9]', // periwinkle
  'bg-[#B070D4]', // plum
  'bg-[#5DBA9B]', // sea green
  'bg-[#C9A07B]', // tan
  'bg-[#7BC9C9]', // teal
  'bg-[#D47BB8]', // rose magenta
  'bg-[#5B9CD4]', // sky blue
  'bg-[#9CC97B]', // moss
  'bg-[#A07BC9]', // mauve
  'bg-[#D4C95B]', // chartreuse
  'bg-[#6FB0A8]', // muted aqua
  'bg-[#B07BC9]', // violet
  'bg-[#C9C97B]', // olive
]

const formatSignalName = name => name.replace(/_score$/, '').replace(/_/g, ' ')

// LearnedSignals — describes what the personal model has learned to weight
// from the user's decisions. Renders as a stacked horizontal bar.
// Collapsed: top-3 named + 1 pooled "other" segment.
// Expanded: every feature individually. Toggle revealed on hover.
// Honest about the full distribution (segments sum to 100%) instead of
// normalizing against the max, which would make a 28% feature look like 100%.
// Threshold below which a feature is too small to bother surfacing.
// 5% reflects the "actionable contribution" bar — anything below this is
// noise to the user even though the model still uses it. The remaining
// mass gets pooled into the bronze "other" segment for completeness.
const LEARNED_SIGNALS_THRESHOLD = 0.05

function LearnedSignals({ modelInfo }) {
  const features = modelInfo?.top_features ?? []
  if (features.length === 0) return null

  const named   = features.filter(f => f.importance >= LEARNED_SIGNALS_THRESHOLD)
  const namedSum = named.reduce((a, f) => a + f.importance, 0)
  const otherMass  = Math.max(0, 1 - namedSum)
  const otherCount = features.length - named.length

  const segments = named.map((f, i) => ({
    key:   f.name,
    name:  formatSignalName(f.name),
    desc:  featureDescription(f.name),
    pct:   f.importance * 100,
    color: SIGNAL_COLORS[i % SIGNAL_COLORS.length],
    muted: false,
  }))

  if (otherCount > 0 && otherMass > 0) {
    segments.push({
      key:   '_other',
      name:  `other ${otherCount} signal${otherCount !== 1 ? 's' : ''}`,
      desc:  `remaining feature weight pooled (each below ${Math.round(LEARNED_SIGNALS_THRESHOLD * 100)}%)`,
      pct:   otherMass * 100,
      color: 'bg-[#8C7A5E]',
      muted: true,
    })
  }

  return (
    <div className="pt-1">
      <div className="flex items-center gap-1.5 mb-1.5">
        <p className="text-[14px] text-[#cecece]">What your model weights</p>
        <InfoTooltip>
          Your personal model considers <strong className="text-[#f9f9f9] font-semibold">{TOTAL_FEATURES} signals</strong> per photo (sharpness, exposure, face presence, lens settings, etc.). This bar shows the share of decision-making weight each signal carries — learned from your past keep/reject choices. Only signals contributing at least <strong className="text-[#f9f9f9] font-semibold">{Math.round(LEARNED_SIGNALS_THRESHOLD * 100)}%</strong> are named individually; the rest are pooled as <strong className="text-[#f9f9f9] font-semibold">other</strong>.
        </InfoTooltip>
      </div>
      {/* Stacked bar — segments separated by 1px white dividers (via gap on flex parent).
          Hover title carries the same description shown below the bar so users
          can poke individual segments without scanning the legend. */}
      <div className="flex h-1.5 rounded-[3px] overflow-hidden bg-[#1b1c1e] mb-2 gap-px">
        {segments.map(s => (
          <div
            key={s.key}
            className={s.color}
            style={{ width: `${s.pct}%` }}
            title={s.desc ? `${s.name} (${s.pct.toFixed(0)}%) — ${s.desc}` : `${s.name} ${s.pct.toFixed(0)}%`}
          />
        ))}
      </div>
      <div className="space-y-1">
        {segments.map(s => (
          <div key={s.key} className="flex items-center gap-2 text-[11px]">
            <span className={`w-2 h-2 rounded-[1px] flex-shrink-0 ${s.color}`} />
            <span className={`flex-1 truncate ${s.muted ? 'text-[#6a6b6c]' : 'text-[#9c9c9d]'}`} title={s.desc ? `${s.name} — ${s.desc}` : s.name}>
              {s.name}
            </span>
            <span className={`font-mono ${s.muted ? 'text-[#6a6b6c]' : 'text-[#cecece]'}`}>{s.pct.toFixed(0)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
