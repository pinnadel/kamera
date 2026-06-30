import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { ArrowLeft, Check, ChevronLeft, ChevronRight, MousePointerSquareDashed, ZoomIn } from 'lucide-react'
import { API } from '../api'
import { DecisionBadge, DecisionWord, ScoreBadge } from '../ui/primitives'
import { PullVisionModelButton } from '../ui/PullVisionModelButton'
import { InstallOllamaCTA } from '../ui/InstallOllamaCTA'
import { InfoTooltip } from '../ui/primitives'
import { pickHeadlineScore } from '../ui/format'
import { compareImages } from '../sortMetrics'
import { ViewPill } from '../ui/ViewPill'
import { THUMB_ASPECT, FILMSTRIP_CHROME_BARE, stripHeight } from '../ui/filmstripMetrics'
import { Filmstrip } from '../ui/Filmstrip'
import { useMultiSelect } from '../hooks/useMultiSelect'

// GroupLoupe — full-screen workspace for triaging one similarity group.
//
// Two sub-modes:
//   Survey (default): every group member rendered at equal size in a CSS
//     grid. Click a tile to focus it (cyan ring). K/M/X then decides the
//     focused photo. Pressing Z toggles a synchronized zoom — clicking any
//     photo while zoomed pans the centre point on all photos in lockstep so
//     you can compare sharpness/eyes at the same pixel region across frames.
//   Loupe: one large preview with a horizontal filmstrip below. Filmstrip
//     thumb click swaps the focused photo. Enter opens the existing
//     DetailView in `groupContext` mode (filmstrip pinned to bottom; arrow
//     keys cycle within the group).
//
// Batch actions live in the toolbar:
//   - Keep best · Reject rest: keep hero, bulk-decision('reject') for the rest,
//                              close loupe, undo per photo from the toast
//   - Keep best · Maybe rest:  keep hero, bulk-decision('maybe') for the rest,
//                              close loupe, undo per photo from the toast
//
// All hotkeys for this view are owned here (useHotkeys), keyed off `open`,
// so they don't leak when the overlay is closed.
import { useHotkeys } from 'react-hotkeys-hook'

const MODE_SURVEY = 'survey'
const MODE_LOUPE  = 'loupe'
const ZOOM_SCALE  = 2.4

// Process-local cache for burst-rank results, keyed by sorted-ids string.
// Survives loupe unmount/remount (open/close DetailView from inside the
// loupe) so we don't repaint a "loading" state for ~100ms while the
// backend's cached row gets re-fetched. Holds only the last fetched
// status+result per membership; small footprint (one entry per unique
// burst opened this session). Entries are never evicted — refreshing
// the page is the natural reset.
const _burstResultCache = new Map()

// fileFormat — uppercased filename suffix, used to badge tiles so users can
// tell at a glance which version of a shot they're looking at. The same shot
// captured as RAF + JPG (or RAF + HIF on Fuji) will score differently because
// in-camera JPEG/HIF processing applies sharpening and tone-mapping the raw
// decode skips — surfacing the format makes that intentional, not confusing.
function fileFormat(filename) {
  if (!filename) return null
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return null
  return filename.slice(dot + 1).toUpperCase()
}

// formatBadgeTone — distinguish raw formats (warmer) from rendered formats
// (cooler) so users can tell families apart at a glance.
function formatBadgeTone(fmt) {
  if (!fmt) return null
  if (fmt === 'RAF' || fmt === 'NEF' || fmt === 'CR2' || fmt === 'CR3' || fmt === 'ARW' || fmt === 'DNG') {
    return 'bg-[rgba(232,184,74,0.15)] text-[#E8B84A] border border-[rgba(232,184,74,0.30)]'
  }
  if (fmt === 'HIF' || fmt === 'HEIC') {
    return 'bg-[rgba(91,184,212,0.15)] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)]'
  }
  if (fmt === 'JPG' || fmt === 'JPEG') {
    return 'bg-[rgba(125,184,154,0.15)] text-[#7DB89A] border border-[rgba(125,184,154,0.30)]'
  }
  return 'bg-[rgba(255,255,255,0.10)] text-[#cecece] border border-[rgba(255,255,255,0.15)]'
}

export function GroupLoupe({
  group,
  onClose,
  onAllDecided,       // () => void  — fired when the last undecided photo in the
                      // group has just been decided. Parent uses this to close
                      // the loupe and advance grid focus past the group.
  onDecide,           // (imageId, decision) => Promise<{decision, new_path}|undefined>
  onUndoImage,        // (imageId) => Promise<void>  — per-photo undo (App.jsx)
  onUndo,             // () => Promise<boolean>      — app-global undo stack pop
  onAmend = null,     // (decision) => Promise<boolean>  — double-press amend; true = consumed
  onRegisterDecisionIntent = null,  // () => void — stamps keypress time for amend race-safety
  onBulk,             // (imageIds, decision) => Promise<void>  — uses POST /bulk-decision
  onOpenDetail,       // (focusedId, group) => void  — opens DetailView with groupContext
  // Globally-shared sort (App.jsx → useSort). Reorders the group's photos
  // inside Survey + Loupe filmstrip without altering hero/AI-pick rings,
  // which are tied to image IDs.
  sortField,
  sortDir,
  modelInfo,
  // Manual group composition surface — the left rail renders allGroups as
  // mini GroupTiles, click switches the open loupe to that group, drag
  // photos onto a rail tile to fold them in. onSetManualGroup is the
  // shared POST /set-manual-group callback (also used by the grid).
  allGroups,
  onSelectGroup,
  onSetManualGroup,
  onRankComplete,     // () => void  — fired after a fresh (non-cached) /rank-burst
                      // succeeds. Parent re-fetches /similarity-groups so the grid
                      // tile flips from "pending"/"in_progress" to "ready" without
                      // waiting for the next prerank-worker advance (which never
                      // comes when the loupe ranks on demand).
  onSetGroupHero,     // ({ group_image_ids, hero_image_id }) => Promise<void>
                      // POSTs to /group-hero and reloads groups. Bound to "B" so the
                      // focused photo becomes the group's BEST.
}) {
  // Sorted view of group.images. All downstream rendering and navigation
  // works against this array; group.images itself is never read directly.
  const sortedImages = useMemo(() => {
    if (!group) return []
    const arr = [...group.images]
    if (sortField) arr.sort((a, b) => compareImages(a, b, sortField, sortDir))
    return arr
  }, [group, sortField, sortDir])
  // Mode persists across loupe re-opens (localStorage `pca.loupeMode`) so
  // a user who prefers Filmstrip doesn't have to flip back to it every time
  // they enter a different group.
  const [mode, setMode] = useState(
    () => localStorage.getItem('pca.loupeMode') === MODE_LOUPE ? MODE_LOUPE : MODE_SURVEY
  )
  useEffect(() => { localStorage.setItem('pca.loupeMode', mode) }, [mode])
  const [focusedId, setFocusedId]       = useState(null)
  const [zoomOn, setZoomOn]             = useState(false)
  // Origin (0..1) where the synchronized zoom is centred. Updated by drag or
  // two-finger scroll — all tiles render with the same origin so the user is
  // comparing the same relative region across frames.
  const [zoomOrigin, setZoomOrigin]     = useState({ x: 0.5, y: 0.5 })
  // Ref used to detect drag vs. click (suppress focus-change on drag release)
  const dragState = useRef(null) // { startX, startY, originAtStart, moved }
  const [isDragging, setIsDragging] = useState(false)
  const [tileSize, setTileSize] = useState(() => localStorage.getItem('pca.loupeSize') || 'M')
  // Filmstrip thumb size (px) inside the loupe's "Filmstrip" sub-mode. Same
  // option pool as the main grid's ViewPill, but persisted under a separate
  // key so the loupe + main grid don't fight over a single setting.
  // Filmstrip thumbnail width — user resizes by dragging the toolbar's top
  // edge. Clamped to [80, 260] (matches DetailView).
  const LOUPE_STRIP_MIN = 80
  const LOUPE_STRIP_MAX = 260
  const [loupeStripThumb, setLoupeStripThumb] = useState(() => {
    const raw = parseInt(localStorage.getItem('pca.loupeStripThumb') || '', 10)
    if (!Number.isFinite(raw)) return 120
    return Math.max(LOUPE_STRIP_MIN, Math.min(LOUPE_STRIP_MAX, raw))
  })
  // Collapse state for the loupe's filmstrip — the 40 px toolbar stays
  // visible above the thumbnail row, mirroring DetailView. Persists across
  // sessions under a separate key from DetailView's filmstrip.
  const [loupeFilmstripCollapsed, setLoupeFilmstripCollapsed] = useState(() => {
    try { return localStorage.getItem('pca.loupeFilmstripCollapsed') === '1' }
    catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('pca.loupeFilmstripCollapsed', loupeFilmstripCollapsed ? '1' : '0') } catch { /* quota / disabled */ }
  }, [loupeFilmstripCollapsed])
  // Last expanded thumb size — restored when re-expanding from collapsed.
  const [loupeStripThumbAtExpand, setLoupeStripThumbAtExpand] = useState(() => {
    const raw = parseInt(localStorage.getItem('pca.loupeStripThumbAtExpand') || '', 10)
    if (!Number.isFinite(raw)) return 120
    return Math.max(LOUPE_STRIP_MIN, Math.min(LOUPE_STRIP_MAX, raw))
  })
  useEffect(() => { localStorage.setItem('pca.loupeStripThumbAtExpand', String(loupeStripThumbAtExpand)) }, [loupeStripThumbAtExpand])

  // Drag-to-resize the loupe filmstrip. See App.startStripResize for the
  // matching DetailView implementation; behaviour is identical. Tracking
  // collapse state in a local mirror is critical — the React-state value
  // read from the closure is frozen at mousedown and would lie about the
  // current state once the user crosses MIN mid-drag.
  const startLoupeStripResize = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    const startY = e.clientY
    const wasCollapsed = loupeFilmstripCollapsed
    const startSize = wasCollapsed ? 0 : loupeStripThumb
    let isCollapsed = wasCollapsed
    let savedRestoreTarget = false
    const onMove = (ev) => {
      const dy = ev.clientY - startY
      const next = startSize - dy
      if (next < LOUPE_STRIP_MIN) {
        if (!isCollapsed && !savedRestoreTarget && startSize >= LOUPE_STRIP_MIN) {
          setLoupeStripThumbAtExpand(startSize)
          savedRestoreTarget = true
        }
        if (!isCollapsed) {
          setLoupeFilmstripCollapsed(true)
          isCollapsed = true
        }
      } else {
        const clamped = Math.min(LOUPE_STRIP_MAX, next)
        if (isCollapsed) {
          setLoupeFilmstripCollapsed(false)
          isCollapsed = false
        }
        setLoupeStripThumb(clamped)
      }
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      // Swallow the click that follows mouseup so it doesn't bubble to any
      // ancestor onClick handler (e.g. GroupLoupe's preview-click pick).
      const swallow = (ev) => {
        ev.preventDefault()
        ev.stopPropagation()
        document.removeEventListener('click', swallow, true)
      }
      document.addEventListener('click', swallow, true)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'row-resize'
  }, [loupeStripThumb, loupeFilmstripCollapsed])

  // Restore previous expanded size when re-expanding via the chevron.
  const onToggleLoupeFilmstripCollapsed = useCallback(() => {
    setLoupeFilmstripCollapsed(prev => {
      if (prev) {
        setLoupeStripThumb(loupeStripThumbAtExpand)
        return false
      }
      setLoupeStripThumbAtExpand(loupeStripThumb)
      return true
    })
  }, [loupeStripThumb, loupeStripThumbAtExpand])
  const [viewOpen, setViewOpen] = useState(false)

  // ── Left group rail (manual group composition) ─────────────────────────
  // Mirrors the DetailView side-panel pattern: width + collapsed state
  // persist to localStorage; resizable via drag handle; collapses to a
  // 36px chevron rail. Auto-expands while a drag is over it so the user
  // always has a usable drop target when in a drag gesture.
  const RAIL_COLLAPSED_WIDTH = 36
  const [railWidth, setRailWidth] = useState(() => {
    try {
      const raw = parseInt(localStorage.getItem('pca.loupeRailWidth') || '', 10)
      if (Number.isFinite(raw) && raw >= 140 && raw <= 400) return raw
    } catch { /* localStorage disabled */ }
    return 200
  })
  useEffect(() => {
    try { localStorage.setItem('pca.loupeRailWidth', String(railWidth)) } catch { /* */ }
  }, [railWidth])

  const [railCollapsed, setRailCollapsed] = useState(() => {
    try { return localStorage.getItem('pca.loupeRailCollapsed') === '1' }
    catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('pca.loupeRailCollapsed', railCollapsed ? '1' : '0') } catch { /* */ }
  }, [railCollapsed])

  // Drag-hover auto-expand. Persisted collapsed state is preserved — the
  // bar widens only while a drag is in progress AND the cursor is over
  // the rail. Snaps back to the saved preference on drop / cancel.
  const [isDragActive, setIsDragActive] = useState(false)
  const [isDragOverRail, setIsDragOverRail] = useState(false)
  const effectiveRailWidth = (railCollapsed && !(isDragActive && isDragOverRail))
    ? RAIL_COLLAPSED_WIDTH
    : railWidth

  const startRailResize = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startWidth = railWidth
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      const next = Math.max(140, Math.min(400, startWidth + dx))
      setRailWidth(next)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [railWidth])

  // ── Multi-select (loupe-local) ─────────────────────────────────────────
  // Scoped to this loupe instance — does not bleed into the grid's
  // selection. Reset whenever the open group changes (different
  // best_image_id) so opening a new burst always starts clean.
  const loupeSelect = useMultiSelect()
  const [pickTargetMode, setPickTargetMode] = useState(false)
  // "Finish group" no-keeps guard: when the user triggers Finish with zero
  // Keeps marked, we don't sweep — instead we surface a confirm affordance.
  // True = the "Reject whole group?" confirmation is showing.
  const [finishWarnOpen, setFinishWarnOpen] = useState(false)
  const groupIdForResetKey = group?.best_image_id ?? null
  useEffect(() => {
    loupeSelect.exit()
    setPickTargetMode(false)
    setFinishWarnOpen(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupIdForResetKey])

  // LLM burst ranking (Phase 2): lazy, fires on group open, cached per
  // membership-hash by the backend. Falls back to score-based pick on any
  // non-'ranked' status. Status lifecycle:
  //   idle → loading → ranked | no_vision_model | too_few | too_many | error
  //
  // The initial state hydrates from a process-local cache keyed by the
  // memberKey (sorted ids). This matters when the loupe unmounts (the
  // user opens DetailView from inside) and remounts (DetailView closes):
  // we'd otherwise re-show the loading spinner for ~100ms while the
  // backend's cached row is fetched again, and worse, a /rank-burst call
  // that lands during a prerank in-flight could collide. Reading from
  // the module cache lets us paint the prior result immediately.
  const initialKey = group ? group.images.map(img => img.id).sort((a, b) => a - b).join(',') : ''
  const cached = initialKey ? _burstResultCache.get(initialKey) : null
  const [burstStatus, setBurstStatus] = useState(cached ? cached.status : 'idle')
  const [burstResult, setBurstResult] = useState(cached ? cached.result : null)

  const setAndSaveTileSize = (s) => { setTileSize(s); localStorage.setItem('pca.loupeSize', s) }
  // Strip thumb size is set continuously during drag, so persist via effect
  // (vs. wrapper) — otherwise every pixel of the drag would call setItem.
  useEffect(() => { localStorage.setItem('pca.loupeStripThumb', String(loupeStripThumb)) }, [loupeStripThumb])

  // Stable signature of the group's membership — drives the burst-rank fetch
  // dep so re-clustering (different members) re-fires the call, but re-renders
  // within the same membership don't.
  const memberKey = useMemo(
    () => group ? group.images.map(img => img.id).sort((a, b) => a - b).join(',') : '',
    [group],
  )

  // Fire POST /rank-burst whenever the group's membership changes.
  // Cache hits return instantly; misses run a vision LLM call (5–30 s).
  // No retry on failure — the user can close + reopen to retry.
  //
  // Two important behaviours of the lifecycle here:
  //
  // 1. If we already have a cached result for this memberKey (the user
  //    just re-opened the same loupe), DON'T flash 'loading'. Stay on
  //    the prior status/result while the network round-trip refreshes
  //    in the background, then overwrite only if the response is a
  //    different/upgrade state.
  //
  // 2. Every successful fetch writes to the module-level _burstResultCache,
  //    so a subsequent loupe mount can hydrate from it. This is the
  //    defense-in-depth pairing with the backend's inflight registry —
  //    the user shouldn't see "AI rank unavailable" flicker on a remount
  //    even if the backend happens to be mid-call.
  useEffect(() => {
    if (!memberKey) return
    const ids = memberKey.split(',').map(Number)
    const hadPrior = _burstResultCache.has(memberKey)
    if (!hadPrior) {
      setBurstStatus('loading')
      setBurstResult(null)
    }
    let cancelled = false
    fetch(`${API}/rank-burst`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_ids: ids }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(data => {
        if (cancelled) return
        const nextStatus = data.status || 'error'
        const nextResult = data.status === 'ranked' ? data : null
        // Don't overwrite a prior 'ranked' result with an 'error' from a
        // transient backend hiccup. The prior result is still valid — the
        // user just needs to wait briefly for the next prerank-status tick
        // to refresh the membership view. (Backend inflight registry
        // already makes this rare, but defense-in-depth is cheap.)
        if (hadPrior && nextStatus !== 'ranked' && _burstResultCache.get(memberKey)?.status === 'ranked') {
          return
        }
        setBurstStatus(nextStatus)
        setBurstResult(nextResult)
        _burstResultCache.set(memberKey, { status: nextStatus, result: nextResult })
        // Fresh rank just landed — the burst_rankings cache row is new, so
        // the grid's prerank_state chip is stale. Ask the parent to refresh
        // /similarity-groups so the tile flips to "ready" / "near_duplicates".
        // Cached responses skip this; the chip was already showing the right
        // state for those.
        const wroteCacheRow = (
          (nextStatus === 'ranked' || nextStatus === 'near_duplicates')
          && data.cached === false
        )
        if (wroteCacheRow && typeof onRankComplete === 'function') {
          try { onRankComplete() } catch { /* parent error, don't break loupe */ }
        }
      })
      .catch(() => {
        if (cancelled) return
        // Same defense as above — keep showing a prior 'ranked' result
        // on a network error. Only flip to 'error' if there's nothing
        // better to show.
        if (hadPrior && _burstResultCache.get(memberKey)?.status === 'ranked') return
        setBurstStatus('error')
        setBurstResult(null)
        _burstResultCache.set(memberKey, { status: 'error', result: null })
      })
    return () => { cancelled = true }
  }, [memberKey, onRankComplete])

  // Effective AI pick = LLM rank-1 when available, else the score-based hero
  // shipped on the group payload. This drives both the warm-amber ring/badge
  // AND the batch "Keep best · ..." actions, so clicking those operates on
  // what the LLM picked when a ranking is available.
  const llmHero = useMemo(() => {
    if (burstStatus !== 'ranked' || !burstResult?.rankings) return null
    const top = burstResult.rankings.find(r => r.rank === 1)
    return top ? { id: top.image_id, reason: top.reason } : null
  }, [burstStatus, burstResult])

  const effectiveHeroId     = llmHero?.id      ?? group?.best_image_id ?? null
  const effectiveHeroReason = llmHero?.reason  ?? group?.best_reason   ?? null

  // Pre-filter awareness. When the backend trimmed a burst >12 down to its
  // top-12 candidates (see backend/group_scoring.top_n_candidates), the
  // burst-rank response includes `evaluated_ids` (the ids the LLM actually
  // saw) and `filtered_from` (the original input count). We surface this
  // so users can see which tiles were LLM-evaluated and which kept their
  // score-based standing.
  //   evaluatedSet  → Set<int> of ids the LLM ranked (empty when N/A)
  //   wasFiltered   → true iff the backend trimmed candidates
  const evaluatedSet = useMemo(() => {
    const ids = burstResult?.evaluated_ids
    return Array.isArray(ids) ? new Set(ids) : null
  }, [burstResult])
  const wasFiltered = !!(
    burstStatus === 'ranked' &&
    burstResult &&
    burstResult.filtered_from > (burstResult.evaluated_ids?.length || 0)
  )

  // Pick a sensible default focus (the AI hero) on open or when the group
  // changes. Falls back to the first photo if hero somehow isn't in the list.
  // Deliberately keyed on the score-based hero only — we don't auto-shift
  // focus when the LLM rank arrives later, that would feel like the cursor
  // is jumping under the user. The amber ring/badge does shift to the LLM
  // pick; only the focused tile stays where the user put it.
  useEffect(() => {
    if (!group) return
    const heroId = group.best_image_id ?? sortedImages[0]?.id ?? null
    setFocusedId(heroId)
    // Mode is NOT reset on group change — the user's preferred view
    // (Survey / Filmstrip) persists across loupes via localStorage.
    setZoomOn(false)
    setZoomOrigin({ x: 0.5, y: 0.5 })
  }, [group?.best_image_id])

  // Close View dropdown on outside click
  useEffect(() => {
    if (!viewOpen) return
    const handler = (e) => { if (!e.target.closest('[data-dropdown="true"]')) setViewOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [viewOpen])

  // Lock background scroll while the loupe is mounted — DetailView does the
  // same trick and the two never coexist.
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  const focusedIdx = useMemo(() => {
    if (!group || focusedId == null) return -1
    return sortedImages.findIndex(img => img.id === focusedId)
  }, [group, focusedId, sortedImages])
  const focusedImage = focusedIdx >= 0 ? sortedImages[focusedIdx] : null

  // ── Hotkeys ──────────────────────────────────────────────────────────────
  // All hotkeys gate on `group` being truthy — the loupe lives at z-50 and
  // takes ownership of K/M/X/arrows while open. App.jsx's useKeyboard hook
  // also gates its own grid-mode shortcuts off `loupeOpen`.

  const stepFocus = useCallback((delta) => {
    if (!group) return
    const i = focusedIdx >= 0 ? focusedIdx : 0
    const next = Math.max(0, Math.min(sortedImages.length - 1, i + delta))
    setFocusedId(sortedImages[next].id)
  }, [group, focusedIdx, sortedImages])

  useHotkeys('arrowRight', (e) => { e.preventDefault(); stepFocus(+1) }, { enabled: !!group, enableOnFormTags: false }, [stepFocus, group])
  useHotkeys('arrowLeft',  (e) => { e.preventDefault(); stepFocus(-1) }, { enabled: !!group, enableOnFormTags: false }, [stepFocus, group])

  const decideFocused = useCallback(async (decision) => {
    if (!focusedImage) return
    const id = focusedImage.id
    const wasUndecided = !focusedImage.decision
    await onDecide(id, decision)

    // Auto-advance so K/M/X feels like progress. Prefer the next *undecided*
    // photo (the common culling rhythm); if there isn't one, step sequentially
    // +1 along the strip like the grid does — re-deciding inside an already-
    // resolved group shouldn't strand the user. Only fire onAllDecided when
    // this K/M/R is the action that actually transitioned the group from
    // "has undecided" → "fully resolved"; otherwise keep the loupe open and
    // just move focus.
    if (group) {
      const start = focusedIdx
      for (let step = 1; step < sortedImages.length; step++) {
        const next = sortedImages[(start + step) % sortedImages.length]
        if (!next.decision) { setFocusedId(next.id); return }
      }
      if (wasUndecided) {
        onAllDecided?.()
        return
      }
      if (sortedImages.length > 1) {
        const next = sortedImages[(start + 1) % sortedImages.length]
        setFocusedId(next.id)
      }
    }
  }, [focusedImage, onDecide, group, focusedIdx, sortedImages, onAllDecided])

  // Bulk K/M/R when a multi-selection is active. Mirrors the grid: the
  // selected ids all get the same decision in one /bulk-decision round-trip,
  // and selection persists so users can apply another action to the same
  // set. onBulk → bulkDecide already handles the "did this resolve the
  // whole group?" check and fires onAllDecided via the parent ref, so the
  // loupe closes + grid advances when the last undecided photo lands.
  const decideSelectedOrFocused = useCallback(async (decision) => {
    // Double-press amend (See App.jsx::amendLastDecision): within ~400ms of the
    // last K/M/R, re-apply the new decision to those previous photo(s) and
    // leave the loupe cursor / selection where it is. Register intent FIRST
    // (synchronously) so that this press, if it ends up being the "first" of a
    // future double-press, has a real keypress timestamp on record — not one
    // that lands ~500ms later after the network call resolves.
    const amendPromise = onAmend ? onAmend(decision) : Promise.resolve(false)
    onRegisterDecisionIntent?.()
    const amended = await amendPromise
    if (amended) return
    if (loupeSelect.isSelectMode && loupeSelect.size > 0) {
      const ids = Array.from(loupeSelect.selected)
      await onBulk?.(ids, decision)
      return
    }
    await decideFocused(decision)
  }, [loupeSelect, onBulk, decideFocused, onAmend, onRegisterDecisionIntent])

  useHotkeys('k', () => decideSelectedOrFocused('keep'),   { enabled: !!group }, [decideSelectedOrFocused, group])
  useHotkeys('m', () => decideSelectedOrFocused('maybe'),  { enabled: !!group }, [decideSelectedOrFocused, group])
  useHotkeys('r', () => decideSelectedOrFocused('reject'), { enabled: !!group }, [decideSelectedOrFocused, group])

  // B — promote the focused photo to BEST for this group. Silent: no toast,
  // no auto-advance; the amber badge moving is the success signal. No-op if
  // the focused photo is already the hero.
  const promoteFocusedToBest = useCallback(async () => {
    if (!group || !focusedImage || !onSetGroupHero) return
    if (focusedImage.id === group.best_image_id) return
    await onSetGroupHero({
      group_image_ids: group.images.map(img => img.id),
      hero_image_id: focusedImage.id,
    })
  }, [group, focusedImage, onSetGroupHero])
  useHotkeys('b', promoteFocusedToBest, { enabled: !!group }, [promoteFocusedToBest, group])

  // U / Cmd+Z — undo, selection-aware like decideSelectedOrFocused. Silent
  // no-op when there's nothing decided to reverse (matches grid behaviour).
  // Doesn't auto-advance: undo is reflective, the user is correcting these.
  //
  // The app-global stack (onUndo) is tried first: a just-applied bulk K/M/R
  // is a single stack entry that already reverses every photo at once. The
  // selection-aware branch below is the fallback for when the stack is empty
  // or exhausted but a multi-selection is still active (e.g. photos decided
  // in an earlier session) — without it, U would undo only the focused tile
  // even though the user has several selected.
  const undoFocused = useCallback(async () => {
    if (onUndo) {
      const handled = await onUndo()
      if (handled) return
    }
    if (loupeSelect.isSelectMode && loupeSelect.size > 0) {
      // Undo every selected photo that actually has a decision. Selection
      // persists so the user can act on the same set again (mirrors how bulk
      // K/M/R leaves the selection in place).
      const ids = Array.from(loupeSelect.selected).filter(id => {
        const img = sortedImages.find(i => i.id === id)
        return img && img.decision
      })
      await Promise.all(ids.map(id => onUndoImage?.(id)))
      return
    }
    if (!focusedImage || !focusedImage.decision) return
    onUndoImage?.(focusedImage.id)
  }, [focusedImage, onUndo, onUndoImage, loupeSelect, sortedImages])
  useHotkeys('u',      undoFocused, { enabled: !!group }, [undoFocused, group])
  useHotkeys('meta+z', undoFocused, { enabled: !!group }, [undoFocused, group])

  useHotkeys('s', () => setMode(m => m === MODE_SURVEY ? MODE_LOUPE : MODE_SURVEY),
    { enabled: !!group }, [group])

  useHotkeys('z', () => setZoomOn(z => !z),
    { enabled: !!group }, [group])

  useHotkeys('enter', () => {
    if (!group || !focusedImage) return
    onOpenDetail?.(focusedImage.id, group)
  }, { enabled: !!group }, [group, focusedImage, onOpenDetail])

  // Space mirrors Enter so muscle memory from grid view (Space → DetailView)
  // works the same way inside the loupe.
  useHotkeys('space', (e) => {
    if (!group || !focusedImage) return
    e.preventDefault()
    onOpenDetail?.(focusedImage.id, group)
  }, { enabled: !!group }, [group, focusedImage, onOpenDetail])

  useHotkeys('escape', () => {
    if (pickTargetMode) { setPickTargetMode(false); return }
    if (loupeSelect.isSelectMode) { loupeSelect.exit(); return }
    onClose()
  }, { enabled: !!group }, [pickTargetMode, loupeSelect, onClose])

  // ── Batch actions ────────────────────────────────────────────────────────

  const nonHero = useMemo(
    () => sortedImages.filter(img => img.id !== effectiveHeroId),
    [sortedImages, effectiveHeroId],
  )

  const runBatch = useCallback(async (decision) => {
    if (!group) return
    const ids = nonHero.map(img => img.id)
    if (ids.length === 0) return

    // Keep the hero first, then apply the bulk decision to the rest. The two
    // calls together resolve the whole group, so onAllDecided closes the
    // loupe and advances the grid cursor.
    const heroImg = sortedImages.find(img => img.id === effectiveHeroId)
    if (effectiveHeroId != null && heroImg?.decision !== 'keep') {
      await onBulk([effectiveHeroId], 'keep')
    }
    await onBulk(ids, decision)

    onAllDecided?.()
  }, [group, nonHero, onBulk, sortedImages, effectiveHeroId, onAllDecided])

  // ── Finish group ─────────────────────────────────────────────────────────
  // "Respect my Keeps, reject everything else." Unlike runBatch (which forces
  // a single AI hero), this honours every photo the user manually marked Keep
  // and rejects the rest — undecided AND Maybe alike. Closes + advances via
  // onAllDecided once the sweep lands.
  const keptImages = useMemo(
    () => sortedImages.filter(img => img.decision === 'keep'),
    [sortedImages],
  )
  const nonKeptIds = useMemo(
    () => sortedImages.filter(img => img.decision !== 'keep').map(img => img.id),
    [sortedImages],
  )

  // Sweep all non-Keep photos to reject. When there are zero Keeps (the
  // no-keeps confirm path), nonKeptIds is the whole group, so this rejects
  // everything — which is exactly what "Reject whole group" means.
  const sweepRejectRest = useCallback(async () => {
    if (!group) return
    if (nonKeptIds.length > 0) {
      await onBulk(nonKeptIds, 'reject')
    }
    setFinishWarnOpen(false)
    onAllDecided?.()
  }, [group, nonKeptIds, onBulk, onAllDecided])

  const finishGroup = useCallback(() => {
    if (!group) return
    // Zero Keeps → don't silently reject the whole group. Surface a confirm.
    if (keptImages.length === 0) {
      setFinishWarnOpen(true)
      return
    }
    setFinishWarnOpen(false)
    sweepRejectRest()
  }, [group, keptImages, sweepRejectRest])

  useHotkeys('c', finishGroup, { enabled: !!group }, [finishGroup, group])

  // ── Pan helpers ──────────────────────────────────────────────────────────
  // Drag: mousedown records start; mousemove translates delta into origin
  // movement; mouseup decides whether to treat as a click (< 4px threshold).
  // Wheel: two-finger trackpad produces deltaX/deltaY → nudge origin.
  // Both are no-ops when zoomOn is false.

  const handlePanMouseDown = useCallback((img, e) => {
    if (!zoomOn) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    dragState.current = {
      startX: e.clientX,
      startY: e.clientY,
      originAtStart: { ...zoomOrigin },
      containerW: rect.width,
      containerH: rect.height,
      moved: false,
      img,
    }
    document.body.style.cursor = 'grabbing'
  }, [zoomOn, zoomOrigin])

  const handlePanMouseMove = useCallback((e) => {
    if (!dragState.current || !zoomOn) return
    const dx = e.clientX - dragState.current.startX
    const dy = e.clientY - dragState.current.startY
    if (!dragState.current.moved && Math.hypot(dx, dy) < 4) return
    if (!dragState.current.moved) setIsDragging(true)
    dragState.current.moved = true

    // Use container dims stored at mousedown so global window mousemove
    // has the right reference even after the cursor leaves the tile.
    const w = dragState.current.containerW || 400
    const h = dragState.current.containerH || 400
    const ox = dragState.current.originAtStart.x - dx / w / (1 - 1 / ZOOM_SCALE)
    const oy = dragState.current.originAtStart.y - dy / h / (1 - 1 / ZOOM_SCALE)
    setZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [zoomOn])

  const handlePanMouseUp = useCallback((e) => {
    if (!dragState.current) return
    const wasDrag = dragState.current.moved
    const img = dragState.current.img
    dragState.current = null
    setIsDragging(false)
    document.body.style.cursor = ''
    if (!wasDrag && img) setFocusedId(img.id)
    e.stopPropagation()
  }, [])

  // Global listeners so dragging outside a tile works smoothly.
  // Also cleans up the grabbing cursor if zoom is turned off mid-drag.
  useEffect(() => {
    if (!zoomOn) {
      document.body.style.cursor = ''
      dragState.current = null
      setIsDragging(false)
      return
    }
    window.addEventListener('mousemove', handlePanMouseMove)
    window.addEventListener('mouseup', handlePanMouseUp)
    return () => {
      window.removeEventListener('mousemove', handlePanMouseMove)
      window.removeEventListener('mouseup', handlePanMouseUp)
    }
  }, [zoomOn, handlePanMouseMove, handlePanMouseUp])

  const handlePanWheel = useCallback((e) => {
    if (!zoomOn) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    const ox = zoomOrigin.x + e.deltaX / rect.width  / (1 - 1 / ZOOM_SCALE)
    const oy = zoomOrigin.y + e.deltaY / rect.height / (1 - 1 / ZOOM_SCALE)
    setZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [zoomOn, zoomOrigin])

  const handleTileClick = (img, e) => {
    if (dragState.current?.moved) return // already handled by mouseup
    // Modifier-aware selection mirroring the grid. Bare click in select
    // mode toggles; cmd+click toggles; shift+click extends range.
    if (e?.metaKey || e?.ctrlKey) {
      if (!loupeSelect.isSelectMode) loupeSelect.enter()
      loupeSelect.toggle(img.id)
      return
    }
    if (e?.shiftKey) {
      if (!loupeSelect.isSelectMode) loupeSelect.enter()
      const orderedIds = sortedImages.map(i => i.id)
      loupeSelect.extend(img.id, orderedIds)
      return
    }
    if (loupeSelect.isSelectMode) {
      loupeSelect.toggle(img.id)
      return
    }
    setFocusedId(img.id)
  }

  if (!group) return null

  // ── Zoom transform ───────────────────────────────────────────────────────
  const transformStyle = zoomOn ? {
    transform: `scale(${ZOOM_SCALE})`,
    transformOrigin: `${zoomOrigin.x * 100}% ${zoomOrigin.y * 100}%`,
    transition: isDragging ? 'none' : 'transform 0.15s ease-out',
  } : { transition: 'transform 0.15s ease-out' }

  // ── Drop targets shared between the rail tiles and the singletons zone ──
  // Decoded from the dataTransfer JSON written by the drag source (either an
  // ImageCard in this loupe or in the grid). Falls back to null on malformed
  // payload — the caller then treats the drop as a no-op.
  const _readDragPayload = (e) => {
    try {
      const raw = e.dataTransfer.getData('application/json')
      if (!raw) return null
      const payload = JSON.parse(raw)
      if (payload?.kind !== 'photos' || !Array.isArray(payload.image_ids)) return null
      return payload
    } catch { return null }
  }

  // Hovered-target id for the rail-tile drop-hover ring.
  const [railDropHoverGroupId, setRailDropHoverGroupId] = useState(null)
  // Hovered "Make singletons" zone state.
  const [singletonsDropHover, setSingletonsDropHover] = useState(false)

  return (
    <div
      className="fixed inset-0 z-50 bg-[#07080a] flex"
      onDragEnd={() => {
        // Always clears on dragend, even if drop landed outside any target.
        setIsDragActive(false)
        setIsDragOverRail(false)
        setRailDropHoverGroupId(null)
        setSingletonsDropHover(false)
      }}
    >

      {/* ── Left group rail ──────────────────────────────────────────── */}
      <aside
        style={{ width: effectiveRailWidth }}
        className="flex-shrink-0 bg-[#0d0e0f] border-r border-[#1a1b1d] flex flex-col transition-[width] duration-200 ease-out"
        onDragEnter={(e) => {
          // External drag arriving from the grid (or our own loupe body)
          // — signal isDragActive so a collapsed rail auto-expands.
          if (e.dataTransfer?.types?.includes('application/json')) {
            setIsDragActive(true)
            setIsDragOverRail(true)
          }
        }}
        onDragOver={(e) => {
          // Required so child onDrop fires. Also keeps isDragOverRail
          // sticky as the cursor moves between rail children.
          if (e.dataTransfer?.types?.includes('application/json')) {
            e.preventDefault()
            if (!isDragOverRail) setIsDragOverRail(true)
          }
        }}
        onDragLeave={(e) => {
          // Only clear when the pointer actually leaves the rail element
          // (not when it moves over a child). relatedTarget being null
          // OR outside the rail container both indicate "left the rail."
          if (!e.currentTarget.contains(e.relatedTarget)) {
            setIsDragOverRail(false)
          }
        }}
      >
        {railCollapsed && !isDragOverRail ? (
          <button
            onClick={() => setRailCollapsed(false)}
            className="w-full h-full flex flex-col items-center pt-4 gap-2 text-[#6a6b6c] hover:text-[#cecece] hover:bg-[rgba(255,255,255,0.02)] transition-colors"
            title="Expand group list"
            aria-label="Expand group list"
          >
            <ChevronRight size={14} />
            <span className="text-[10px] tracking-wider [writing-mode:vertical-rl] [transform:rotate(180deg)] select-none">
              Groups
            </span>
          </button>
        ) : (
          <>
            <div className="flex items-center justify-between px-2.5 py-2 border-b border-[#1a1b1d]">
              <span className="text-[11px] uppercase tracking-wider text-[#9c9c9d] select-none">
                Groups ({Array.isArray(allGroups) ? allGroups.length : 0})
              </span>
              <button
                onClick={() => setRailCollapsed(true)}
                className="p-1 rounded text-[#6a6b6c] hover:text-[#cecece] hover:bg-[rgba(255,255,255,0.05)]"
                title="Collapse"
                aria-label="Collapse group list"
              >
                <ChevronLeft size={13} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-2">
              {Array.isArray(allGroups) && allGroups.map(g => (
                <RailGroupTile
                  key={`rail-${g.best_image_id}`}
                  group={g}
                  isOpen={g.best_image_id === group.best_image_id}
                  isPickTarget={pickTargetMode && g.best_image_id !== group.best_image_id}
                  isDropHover={railDropHoverGroupId === g.best_image_id}
                  onClick={() => {
                    if (pickTargetMode) {
                      if (g.best_image_id === group.best_image_id) return
                      const ids = Array.from(loupeSelect.selected)
                      setPickTargetMode(false)
                      onSetManualGroup?.({
                        image_ids: ids,
                        mode: 'join_group',
                        target_image_id: g.best_image_id,
                      })
                      loupeSelect.exit()
                      return
                    }
                    if (g.best_image_id !== group.best_image_id) {
                      onSelectGroup?.(g.best_image_id)
                    }
                  }}
                  onDragOver={(e) => {
                    if (!e.dataTransfer?.types?.includes('application/json')) return
                    e.preventDefault()
                    e.dataTransfer.dropEffect = 'move'
                    if (railDropHoverGroupId !== g.best_image_id) {
                      setRailDropHoverGroupId(g.best_image_id)
                    }
                  }}
                  onDragLeave={() => {
                    if (railDropHoverGroupId === g.best_image_id) {
                      setRailDropHoverGroupId(null)
                    }
                  }}
                  onDrop={(e) => {
                    e.preventDefault()
                    setRailDropHoverGroupId(null)
                    const payload = _readDragPayload(e)
                    if (!payload) return
                    // Drop on the open group's own tile is a no-op — photos
                    // are already members of this group.
                    if (g.best_image_id === group.best_image_id) return
                    onSetManualGroup?.({
                      image_ids: payload.image_ids,
                      mode: 'join_group',
                      target_image_id: g.best_image_id,
                    })
                    loupeSelect.exit()
                  }}
                />
              ))}
              {/* "Make singletons" drop zone — only when we have a selection. */}
              {loupeSelect.isSelectMode && loupeSelect.size > 0 && (
                <div
                  className={`mt-3 px-3 py-4 rounded-lg border-2 border-dashed text-center text-[11px] transition-colors
                    ${singletonsDropHover
                      ? 'border-[#C97B7B] bg-[rgba(201,123,123,0.10)] text-[#C97B7B]'
                      : 'border-[rgba(201,123,123,0.40)] text-[rgba(201,123,123,0.85)]'}`}
                  onDragOver={(e) => {
                    if (!e.dataTransfer?.types?.includes('application/json')) return
                    e.preventDefault()
                    e.dataTransfer.dropEffect = 'move'
                    if (!singletonsDropHover) setSingletonsDropHover(true)
                  }}
                  onDragLeave={() => setSingletonsDropHover(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setSingletonsDropHover(false)
                    const payload = _readDragPayload(e)
                    if (!payload) return
                    onSetManualGroup?.({
                      image_ids: payload.image_ids,
                      mode: 'singletons',
                    })
                    loupeSelect.exit()
                  }}
                >
                  Drop here to remove from group
                </div>
              )}
            </div>
          </>
        )}
        {/* Resize handle — only meaningful when expanded. 4px wide, on the
            rail's right edge. */}
        {!railCollapsed && (
          <div
            onMouseDown={startRailResize}
            className="absolute top-0 bottom-0 w-1 cursor-col-resize hover:bg-[rgba(91,184,212,0.30)]"
            style={{ left: effectiveRailWidth - 2 }}
            aria-hidden="true"
          />
        )}
      </aside>

      {/* ── Main body — original loupe content shifted right by the rail ── */}
      <div className="flex-1 flex flex-col min-w-0">

      {/* ── Loupe contextual select-mode bar ────────────────────────── */}
      {(loupeSelect.isSelectMode || pickTargetMode) && (
        <div className="flex items-center gap-3 px-4 py-2 bg-[rgba(91,184,212,0.08)] border-b border-[rgba(91,184,212,0.25)] text-xs">
          {pickTargetMode ? (
            <>
              <span className="text-[#5BB8D4] font-semibold">
                Click a group in the rail to move {loupeSelect.size} photo{loupeSelect.size === 1 ? '' : 's'} into it
              </span>
              <span className="text-[#9c9c9d]">· Esc to cancel</span>
              <div className="flex-1" />
              <button
                onClick={() => setPickTargetMode(false)}
                className="px-2 py-1 rounded text-[#cecece] hover:bg-[rgba(255,255,255,0.05)]"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <span className="text-[#cecece] font-semibold tabular-nums">
                {loupeSelect.size} selected
              </span>
              <span className="text-[#9c9c9d]">· K / M / R to decide · Shift+click for range · Cmd+click to toggle · Esc to exit</span>
              <div className="flex-1" />
              <button
                onClick={() => {
                  const ids = Array.from(loupeSelect.selected)
                  if (ids.length === 0) return
                  onSetManualGroup?.({ image_ids: ids, mode: 'singletons' })
                  loupeSelect.exit()
                }}
                disabled={loupeSelect.size === 0}
                className="px-2.5 py-1 rounded bg-[rgba(201,123,123,0.10)] text-[#C97B7B] border border-[rgba(201,123,123,0.30)] hover:bg-[rgba(201,123,123,0.18)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                title="Remove the selected photos from this group; they become standalone tiles in the grid"
              >
                Remove from group
              </button>
              <button
                onClick={() => {
                  const ids = Array.from(loupeSelect.selected)
                  if (ids.length < 2) return
                  onSetManualGroup?.({ image_ids: ids, mode: 'new_group' })
                  loupeSelect.exit()
                }}
                disabled={loupeSelect.size < 2}
                className="px-2.5 py-1 rounded bg-[#1a1b1d] text-[#cecece] border border-[rgba(255,255,255,0.10)] hover:bg-[#202123] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                title="Split the selected photos into their own new group"
              >
                Split into new group
              </button>
              <button
                onClick={() => {
                  if (loupeSelect.size === 0) return
                  setPickTargetMode(true)
                }}
                disabled={loupeSelect.size === 0}
                className="px-2.5 py-1 rounded bg-[#1a1b1d] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)] hover:bg-[#202123] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                title="Click 'Move into group…' then click a group in the rail"
              >
                Move into group…
              </button>
              <button
                onClick={() => loupeSelect.exit()}
                className="px-2 py-1 rounded text-[#cecece] hover:bg-[rgba(255,255,255,0.05)]"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Top bar — back + context + batch culling actions ──────────── */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[#1a1b1d]">
        <button
          onClick={onClose}
          title="Back to grid (Esc)"
          className="px-2 py-1 rounded-lg text-xs transition-opacity border whitespace-nowrap inline-flex items-center gap-1.5 text-[#cecece] border-transparent hover:opacity-70"
        >
          <ArrowLeft size={13} /> Back
        </button>
        <span className="text-xs text-[#6a6b6c] flex-shrink-0 select-none">
          {group.size} photos{group.threshold != null ? ` · ≥${(group.threshold * 100).toFixed(0)}%` : ''}
        </span>
        <BurstRankStatus
          status={burstStatus}
          result={burstResult}
          images={sortedImages}
        />
        {nonHero.length > 0 && (
          <>
            <div className="w-px h-4 bg-[#2a2b2d]" />
            <button
              onClick={() => runBatch('maybe')}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs bg-[rgba(232,184,74,0.12)] text-[#E8B84A] border border-[rgba(232,184,74,0.30)] hover:opacity-70 transition-opacity whitespace-nowrap"
              title={`Keep best, mark the other ${nonHero.length} as Maybe`}
            >
              Keep best · <DecisionWord kind="maybe">Maybe</DecisionWord> rest
            </button>
            <button
              onClick={() => runBatch('reject')}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs bg-[rgba(201,123,123,0.12)] text-[#C97B7B] border border-[rgba(201,123,123,0.30)] hover:opacity-70 transition-opacity whitespace-nowrap"
              title={`Reject the other ${nonHero.length} photo${nonHero.length === 1 ? '' : 's'}`}
            >
              Keep best · <DecisionWord kind="reject">Reject</DecisionWord> rest
            </button>
          </>
        )}

        {/* Finish group — honour the user's manual Keeps, reject the rest (C) */}
        <div className="w-px h-4 bg-[#2a2b2d]" />
        {finishWarnOpen ? (
          <span className="inline-flex items-center gap-2 text-xs whitespace-nowrap">
            <span className="text-[#6a6b6c]">Nothing kept —</span>
            <button
              onClick={sweepRejectRest}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[rgba(201,123,123,0.12)] text-[#C97B7B] border border-[rgba(201,123,123,0.30)] hover:opacity-70 transition-opacity"
              title={`Reject all ${group.size} photos in this group`}
            >
              <DecisionWord kind="reject">Reject</DecisionWord> whole group
            </button>
            <button
              onClick={() => setFinishWarnOpen(false)}
              className="px-2 py-1 rounded-lg text-[#cecece] hover:opacity-70 transition-opacity"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            onClick={finishGroup}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)] hover:opacity-70 transition-opacity whitespace-nowrap"
            title={
              keptImages.length > 0
                ? `Finish group — reject the other ${nonKeptIds.length} photo${nonKeptIds.length === 1 ? '' : 's'} (C)`
                : 'Finish group (C)'
            }
          >
            <Check size={13} /> Finish group
          </button>
        )}
      </div>

      {/* ── Body ──────────────────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {mode === MODE_SURVEY ? (
          <SurveyGrid
            images={sortedImages}
            heroId={effectiveHeroId}
            heroReason={effectiveHeroReason}
            focusedId={focusedId}
            evaluatedSet={evaluatedSet}
            wasFiltered={wasFiltered}
            onTileClick={handleTileClick}
            onTileDoubleClick={(img) => {
              if (loupeSelect.isSelectMode) return
              setFocusedId(img.id)
              onOpenDetail?.(img.id, group)
            }}
            onTileMouseDown={handlePanMouseDown}
            onWheel={handlePanWheel}
            transformStyle={transformStyle}
            zoomOn={zoomOn}
            tileSize={tileSize}
            modelInfo={modelInfo}
            selectedSet={loupeSelect.selected}
            isSelectMode={loupeSelect.isSelectMode}
            onDragStartTile={(img, e) => {
              const ids = Array.from(loupeSelect.selected)
              if (ids.length === 0) return
              e.dataTransfer.effectAllowed = 'move'
              e.dataTransfer.setData('application/json', JSON.stringify({ kind: 'photos', image_ids: ids }))
              e.dataTransfer.setData('text/plain', ids.join(','))
              setIsDragActive(true)
            }}
            onDragEndTile={() => {
              setIsDragActive(false)
              setIsDragOverRail(false)
            }}
          />
        ) : (
          <LoupePane
            images={sortedImages}
            heroId={effectiveHeroId}
            focusedImage={focusedImage}
            evaluatedSet={evaluatedSet}
            wasFiltered={wasFiltered}
            onPickFilmstrip={(img, e) => handleTileClick(img, e)}
            onMouseDown={(img, e) => handlePanMouseDown(img, e)}
            onWheel={handlePanWheel}
            transformStyle={transformStyle}
            zoomOn={zoomOn}
            thumbSize={loupeStripThumb}
            modelInfo={modelInfo}
            selectedSet={loupeSelect.selected}
            isSelectMode={loupeSelect.isSelectMode}
            onDragStartTile={(img, e) => {
              const ids = Array.from(loupeSelect.selected)
              if (ids.length === 0) return
              e.dataTransfer.effectAllowed = 'move'
              e.dataTransfer.setData('application/json', JSON.stringify({ kind: 'photos', image_ids: ids }))
              e.dataTransfer.setData('text/plain', ids.join(','))
              setIsDragActive(true)
            }}
            onDragEndTile={() => {
              setIsDragActive(false)
              setIsDragOverRail(false)
            }}
            filmstripCollapsed={loupeFilmstripCollapsed}
            onToggleFilmstripCollapsed={onToggleLoupeFilmstripCollapsed}
            onStartFilmstripResize={startLoupeStripResize}
          />
        )}
      </div>

      {/* ── Floating bottom pill ──────────────────────────────────────────
          Mirrors the main grid's bottom pill: a single ViewPill (layout +
          contextual size) + a Zoom toggle. "Open details" was removed
          because Enter / Space already opens DetailView and the
          discoverability cost wasn't pulling its weight.
          In Filmstrip mode, lift the pill above the strip so they don't
          overlap — same trick as the main-grid sticky filmstrip. */}
      <div
        style={{
          // In loupe mode: lift the pill above the filmstrip's toolbar.
          // When the strip body is collapsed, the toolbar alone is what we
          // sit on top of; expanded, we add the strip body's height.
          bottom: mode === MODE_LOUPE
            ? `${(loupeFilmstripCollapsed ? 0 : stripHeight(loupeStripThumb, FILMSTRIP_CHROME_BARE)) + 4}px`
            : '8px',
        }}
        className="fixed left-1/2 -translate-x-1/2 z-[60] flex items-center gap-1 px-2 py-1.5 bg-[#111214] border border-[#2a2b2d] rounded-2xl shadow-[0_4px_24px_rgba(0,0,0,0.6)]"
      >

        <ViewPill
          layout={mode === MODE_SURVEY ? 'grid' : 'filmstrip'}
          onSelectLayout={(id) => { setMode(id === 'grid' ? MODE_SURVEY : MODE_LOUPE); setViewOpen(false) }}
          sizeOptionsByLayout={{
            grid: [
              { label: 'Small',   value: 'S' },
              { label: 'Medium',  value: 'M' },
              { label: 'Large',   value: 'L' },
            ],
            // Loupe (filmstrip) mode: no size dropdown — the user drags the
            // top edge of the filmstrip toolbar to resize instead.
            filmstrip: [],
          }}
          sizeByLayout={{ grid: tileSize }}
          onSelectSize={(layoutId, value) => {
            if (layoutId === 'grid') setAndSaveTileSize(value)
            setViewOpen(false)
          }}
          sizeLabelByLayout={{ grid: 'Tile size' }}
          open={viewOpen}
          onOpen={() => setViewOpen(true)}
          onClose={() => setViewOpen(false)}
        />

        <div className="w-px h-4 bg-[#2a2b2d] mx-1" />

        {/* Select — explicit entry point into multi-select mode.
            Cmd+click / Shift+click on a tile is the faster path; this is
            the discoverable affordance. */}
        <button
          onClick={() => loupeSelect.isSelectMode ? loupeSelect.exit() : loupeSelect.enter()}
          className={`px-2 py-1 rounded-lg text-xs transition-opacity border whitespace-nowrap inline-flex items-center gap-1.5 ${loupeSelect.isSelectMode ? 'bg-[#1a1b1d] text-[#5BB8D4] border-[rgba(91,184,212,0.30)]' : 'text-[#cecece] border-transparent hover:opacity-70'}`}
          title="Multi-select photos to remove, split, or move into another group"
          aria-label="Select"
        >
          <MousePointerSquareDashed size={13} />
          Select
        </button>

        <div className="w-px h-4 bg-[#2a2b2d] mx-1" />

        {/* Zoom — distinct interaction (drag-to-pan in zoom), Z key */}
        <button
          onClick={() => setZoomOn(z => !z)}
          className={`px-2 py-1 rounded-lg text-xs transition-opacity border whitespace-nowrap inline-flex items-center gap-1.5 ${zoomOn ? 'bg-[#1a1b1d] text-[#5BB8D4] border-[rgba(91,184,212,0.30)]' : 'text-[#cecece] border-transparent hover:opacity-70'}`}
          title="Synchronized zoom · Z"
          aria-label="Synchronized zoom"
        >
          <ZoomIn size={13} />
          Zoom
        </button>

      </div>

      </div>  {/* end of body wrapper */}
    </div>
  )
}

// ── Survey n-up ─────────────────────────────────────────────────────────────
// minTile drives column width; rowHeight gives each tile a fixed height so
// large groups scroll naturally instead of squashing all tiles into one viewport.
// Range widened so S/M/L produce visibly different layouts: S ≈ contact-sheet,
// L ≈ near-loupe-size single column on most viewports.
const TILE_SIZE_MAP = { S: 130, M: 280, L: 540 }
const ROW_HEIGHT_MAP = { S: 120, M: 240, L: 460 }

function SurveyGrid({ images, heroId, heroReason, focusedId, evaluatedSet, wasFiltered, onTileClick, onTileDoubleClick, onTileMouseDown, onWheel, transformStyle, zoomOn, tileSize, modelInfo, selectedSet, isSelectMode, onDragStartTile, onDragEndTile }) {
  const minTile  = TILE_SIZE_MAP[tileSize] ?? (images.length <= 3 ? 480 : images.length <= 6 ? 340 : 260)
  const rowHeight = ROW_HEIGHT_MAP[tileSize] ?? (images.length <= 3 ? 420 : images.length <= 6 ? 290 : 220)

  return (
    <div className={`h-full ${zoomOn ? 'overflow-hidden' : 'overflow-auto scrollbar-hide'} p-4 pb-16`}>
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${minTile}px, 1fr))`, gridAutoRows: `${rowHeight}px` }}
      >
        {images.map(img => (
          <SurveyTile
            key={img.id}
            img={img}
            isHero={img.id === heroId}
            heroReason={img.id === heroId ? heroReason : null}
            isFocused={img.id === focusedId}
            // notLlmEvaluated: only meaningful when the burst was pre-filtered.
            // For un-filtered bursts (≤12), every tile was evaluated, so we
            // never want to render the muted dot.
            notLlmEvaluated={wasFiltered && evaluatedSet ? !evaluatedSet.has(img.id) : false}
            onClick={(e) => onTileClick(img, e)}
            onDoubleClick={() => onTileDoubleClick?.(img)}
            onMouseDown={(e) => onTileMouseDown(img, e)}
            onWheel={onWheel}
            transformStyle={transformStyle}
            zoomOn={zoomOn}
            modelInfo={modelInfo}
            isMultiSelected={!!selectedSet?.has?.(img.id)}
            isSelectMode={!!isSelectMode}
            draggable={!!selectedSet?.has?.(img.id)}
            onDragStart={(e) => onDragStartTile?.(img, e)}
            onDragEnd={onDragEndTile}
          />
        ))}
      </div>
    </div>
  )
}

function SurveyTile({ img, isHero, heroReason, isFocused, notLlmEvaluated, onClick, onDoubleClick, onMouseDown, onWheel, transformStyle, zoomOn, modelInfo, isMultiSelected = false, isSelectMode = false, draggable = false, onDragStart, onDragEnd }) {
  // Ring priority (most specific wins):
  //   1. multi-select / focused → cyan (active user gesture)
  //   2. decision K/M/R → green/amber/coral; ring-2 if this is also the
  //      hero so BEST stays prominent without competing colors
  //   3. hero (no decision yet) → ring-2 amber
  //   4. default → faint white hover
  const ring = (() => {
    if (isMultiSelected || isFocused) return 'ring-1 ring-[#5BB8D4]'
    const dec = img.decision
    if (dec === 'keep')   return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(125,184,154,0.55)] hover:ring-[rgba(125,184,154,0.75)]`
    if (dec === 'maybe')  return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(232,184,74,0.55)] hover:ring-[rgba(232,184,74,0.75)]`
    if (dec === 'reject') return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(201,123,123,0.55)] hover:ring-[rgba(201,123,123,0.75)]`
    if (isHero) return 'ring-2 ring-[rgba(232,184,74,0.55)]'
    return 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  })()
  const dimmed = isSelectMode && !isMultiSelected ? 'opacity-70' : ''
  const dragCursor = draggable ? 'cursor-grab active:cursor-grabbing' : (zoomOn ? 'cursor-grab' : 'cursor-pointer')

  return (
    <div
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      draggable={draggable}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      className={`relative bg-[#101111] rounded-lg overflow-hidden flex flex-col transition-all ${dragCursor} ${ring} ${dimmed || (img.decision === 'reject' ? 'opacity-[0.55]' : '')}`}
    >
      <div
        className="flex-1 min-h-0 bg-[#07080a] flex items-center justify-center overflow-hidden relative"
        onMouseDown={onMouseDown}
        onWheel={onWheel}
      >
        <img
          src={`${API}/previews/${img.id}`}
          alt={img.filename}
          className="max-h-full max-w-full object-contain select-none pointer-events-none"
          style={transformStyle}
          draggable={false}
        />
        {isHero && (
          <span
            className="absolute top-2 left-2 flex flex-col gap-0.5 items-start max-w-[80%]"
            title={heroReason ? `Best in group: ${heroReason}` : "AI's top pick for this group"}
          >
            <span className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider leading-none bg-[rgba(232,184,74,0.92)] text-[#1a1300] shadow-md flex items-center gap-1">
              <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                <path d="M8 1.5 L9.9 5.85 L14.5 6.4 L11.05 9.55 L11.95 14.1 L8 11.85 L4.05 14.1 L4.95 9.55 L1.5 6.4 L6.1 5.85 Z" />
              </svg>
              Best
            </span>
            {heroReason && (
              <span className="px-1.5 py-0.5 rounded text-[9px] leading-tight bg-[rgba(0,0,0,0.65)] text-[#E8B84A] max-w-full truncate">
                {heroReason}
              </span>
            )}
          </span>
        )}
        {notLlmEvaluated && (
          // Muted dot in the opposite corner from the hero badge so the two
          // never overlap. Native title tooltip is enough here — the chip in
          // the top bar already carries the full explanation of what was
          // filtered and why.
          <span
            className="absolute top-2 right-2 w-2 h-2 rounded-full bg-[rgba(255,255,255,0.28)] shadow"
            title="Not LLM-evaluated — this photo wasn't in the top-12 the vision model ranked. It keeps its score-based standing."
            aria-label="Not LLM-evaluated"
          />
        )}
        {/* Multi-select check overlay — bottom-left so it never collides
            with the Best/hero badge in the top-left. */}
        {isSelectMode && (
          <div
            className={`absolute bottom-2 left-2 w-5 h-5 rounded-full flex items-center justify-center transition-colors pointer-events-none
              ${isMultiSelected
                ? 'bg-[#5BB8D4] text-[#07080a]'
                : 'bg-[rgba(7,8,10,0.65)] ring-1 ring-[rgba(255,255,255,0.30)] text-transparent'}`}
            aria-hidden="true"
          >
            <Check size={12} strokeWidth={3} />
          </div>
        )}
      </div>
      <div className="px-2 py-1.5 flex items-center gap-1.5 bg-[#101111] border-t border-[rgba(255,255,255,0.04)]">
        <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
        {img.decision && <DecisionBadge decision={img.decision} />}
        {(() => {
          const fmt = fileFormat(img.filename)
          return fmt && (
            <span
              className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-semibold uppercase leading-none ${formatBadgeTone(fmt)}`}
              title="File format — RAW vs JPEG/HIF can score differently due to in-camera processing"
            >
              {fmt}
            </span>
          )
        })()}
        <span className="ml-auto text-[10px] font-mono text-[#9c9c9d] truncate" title={img.filename}>
          {img.filename}
        </span>
      </div>
    </div>
  )
}

// ── Loupe + filmstrip ───────────────────────────────────────────────────────
function LoupePane({ images, heroId, focusedImage, evaluatedSet, wasFiltered, onPickFilmstrip, onMouseDown, onWheel, transformStyle, zoomOn, thumbSize = 112, modelInfo, selectedSet, isSelectMode, onDragStartTile, onDragEndTile, filmstripCollapsed = false, onToggleFilmstripCollapsed = null, onStartFilmstripResize = null }) {
  if (!focusedImage) return null
  const thumbW = thumbSize
  const thumbH = Math.round(thumbSize * THUMB_ASPECT)
  return (
    <div className="h-full flex flex-col">
      {/* Big preview */}
      <div
        className={`flex-1 min-h-0 bg-[#07080a] flex items-center justify-center overflow-hidden p-4 ${zoomOn ? 'cursor-grab' : ''}`}
        onMouseDown={(e) => onMouseDown(focusedImage, e)}
        onWheel={onWheel}
        onClick={() => onPickFilmstrip(focusedImage)}
      >
        <img
          src={`${API}/previews/${focusedImage.id}`}
          alt={focusedImage.filename}
          className="max-h-full max-w-full object-contain select-none pointer-events-none"
          style={transformStyle}
          draggable={false}
        />
      </div>
      {/* Filmstrip — shared primitive owns toolbar + collapse + resize +
          thumb row layout. Per-thumb chrome (hero ring, multi-select,
          drag-and-drop, file-format badge) is supplied via renderThumb. */}
      <Filmstrip
        items={images}
        focusedIndex={images.findIndex(img => img.id === focusedImage.id)}
        trackFocusedScroll
        collapsed={filmstripCollapsed}
        onToggleCollapsed={onToggleFilmstripCollapsed}
        onStartResize={onStartFilmstripResize}
        className="flex-shrink-0 border-t border-[rgba(255,255,255,0.06)] bg-[#101111]"
        renderThumb={(img, idx) => {
          const isFocused = img.id === focusedImage.id
          const isHero    = img.id === heroId
          const isMultiSelected = !!selectedSet?.has?.(img.id)
          const notLlmEvaluated = wasFiltered && evaluatedSet ? !evaluatedSet.has(img.id) : false
          // Same ring priority as SurveyTile — keep these in sync.
          const ring = (() => {
            if (isMultiSelected || isFocused) return 'ring-1 ring-[#5BB8D4]'
            const dec = img.decision
            if (dec === 'keep')   return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(125,184,154,0.55)] hover:ring-[rgba(125,184,154,0.75)]`
            if (dec === 'maybe')  return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(232,184,74,0.55)] hover:ring-[rgba(232,184,74,0.75)]`
            if (dec === 'reject') return `${isHero ? 'ring-2' : 'ring-1'} ring-[rgba(201,123,123,0.55)] hover:ring-[rgba(201,123,123,0.75)]`
            if (isHero) return 'ring-2 ring-[rgba(232,184,74,0.55)]'
            return 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.20)]'
          })()
          const dimmed = isSelectMode && !isMultiSelected ? 'opacity-70' : ''
          const isDraggable = isMultiSelected
          return (
            <button
              key={img.id}
              type="button"
              data-filmstrip-idx={idx}
              onClick={(e) => onPickFilmstrip(img, e)}
              style={{ width: `${thumbW}px` }}
              draggable={isDraggable}
              onDragStart={isDraggable ? (e) => onDragStartTile?.(img, e) : undefined}
              onDragEnd={isDraggable ? onDragEndTile : undefined}
              className={`relative flex-shrink-0 rounded-md overflow-hidden bg-[#161718] transition-all ${isDraggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'} ${ring} ${dimmed || (img.decision === 'reject' && !isFocused ? 'opacity-[0.45]' : '')}`}
              title={isHero ? `${img.filename} — Best in group` : img.filename}
            >
              <div
                style={{ height: `${thumbH}px` }}
                className="bg-[#07080a] flex items-center justify-center overflow-hidden relative"
              >
                <img
                  src={`${API}/previews/${img.id}`}
                  alt={img.filename}
                  className="max-h-full max-w-full object-contain"
                />
                {isHero && (
                  <span
                    className="absolute top-1 left-1 w-4 h-4 rounded-full bg-[rgba(232,184,74,0.92)] text-[#1a1300] shadow flex items-center justify-center"
                    aria-label="AI pick"
                  >
                    <svg width="8" height="8" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
                      <path d="M8 1.5 L9.9 5.85 L14.5 6.4 L11.05 9.55 L11.95 14.1 L8 11.85 L4.05 14.1 L4.95 9.55 L1.5 6.4 L6.1 5.85 Z" />
                    </svg>
                  </span>
                )}
                {notLlmEvaluated && (
                  <span
                    className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-[rgba(255,255,255,0.32)] shadow"
                    title="Not LLM-evaluated"
                    aria-label="Not LLM-evaluated"
                  />
                )}
                {isSelectMode && (
                  <div
                    className={`absolute bottom-1 left-1 w-4 h-4 rounded-full flex items-center justify-center transition-colors pointer-events-none
                      ${isMultiSelected
                        ? 'bg-[#5BB8D4] text-[#07080a]'
                        : 'bg-[rgba(7,8,10,0.65)] ring-1 ring-[rgba(255,255,255,0.30)] text-transparent'}`}
                    aria-hidden="true"
                  >
                    <Check size={10} strokeWidth={3} />
                  </div>
                )}
              </div>
              <div className="px-1.5 py-1 flex items-center gap-1">
                <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
                {img.decision && <DecisionBadge decision={img.decision} />}
                {(() => {
                  const fmt = fileFormat(img.filename)
                  return fmt && (
                    <span
                      className={`px-1 py-0.5 rounded text-[8px] font-mono font-semibold uppercase leading-none ml-auto ${formatBadgeTone(fmt)}`}
                      title="File format"
                    >
                      {fmt}
                    </span>
                  )
                })()}
              </div>
            </button>
          )
        }}
      />
    </div>
  )
}

// ── Burst-rank status chip ──────────────────────────────────────────────────
// Renders next to the photo-count chip in the top bar. Quiet by design —
// we only want to draw the eye when there's a useful state to surface:
//   loading                     → "AI ranking…" with the warm-amber tone of the pick badge
//   ranked (no filter)          → silent (the amber ring on the hero tells the story)
// RailGroupTile — compact GroupTile variant for the left rail. We don't
// reuse the main GroupTile because the rail wants a denser layout (no
// stacked-paper edges, no decision badges, no filter context) and the
// drop-target highlight is its own state. Thumbnail + count badge + a
// cyan ring when this is the currently-open group is enough information.
function RailGroupTile({ group, isOpen, isPickTarget, isDropHover, onClick, onDragOver, onDragLeave, onDrop }) {
  const hero = group.images.find(img => img.id === group.best_image_id) || group.images[0]
  // Center the open tile in the rail when this becomes the active group —
  // both on initial mount of the loupe and on subsequent switches. Without
  // this the user can land on a group whose rail tile is scrolled offscreen.
  const tileRef = useRef(null)
  useEffect(() => {
    if (!isOpen || !tileRef.current) return
    tileRef.current.scrollIntoView({ block: 'center', behavior: 'auto' })
  }, [isOpen])
  if (!hero) return null
  let ring
  if (isDropHover)    ring = 'ring-[3px] ring-[#5BB8D4]'
  else if (isOpen)    ring = 'ring-1 ring-[#5BB8D4]'
  else if (isPickTarget) ring = 'ring-1 ring-[rgba(91,184,212,0.55)] hover:ring-[#5BB8D4]'
  else                ring = 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  return (
    <button
      ref={tileRef}
      type="button"
      onClick={onClick}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`relative block w-full rounded-md overflow-hidden bg-[#161718] transition-all ${ring}`}
      title={`${group.size} photos · click to open${isPickTarget ? ' · or drop selection here' : ''}`}
    >
      <div className="aspect-[4/3] flex items-center justify-center bg-[#07080a]">
        <img
          src={`${API}/previews/${hero.id}`}
          alt={hero.filename}
          loading="lazy"
          className="max-h-full max-w-full object-contain pointer-events-none"
        />
      </div>
      <span className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-[rgba(7,8,10,0.75)] text-[10px] text-[#cecece] tabular-nums">
        {group.size}
      </span>
    </button>
  )
}


//   ranked (top-N filtered)     → amber chip "AI ranked top N of M" with InfoTooltip
//                                  + click-to-inspect popover listing evaluated filenames
//   no_vision_model             → one-line nudge with `ollama pull qwen2.5vl:7b` hint
//   too_few                     → silent (group is out of bounds; nothing went wrong)
//   near_duplicates             → neutral "Near-duplicate frames" chip explaining
//                                  that AI dedup found nothing to compare; the
//                                  score-based pick applies. Distinct from "error"
//                                  in tone (this is a deliberate outcome, not a
//                                  failure) and from "too_few" (which is silent).
//   error                       → small coral hint, no retry button (user can reopen)
function BurstRankStatus({ status, result, images }) {

  const [inspectOpen, setInspectOpen] = useState(false)
  // Cache the latest /lm-status so we can show the right CTA in the
  // no_vision_model branch (Ollama-missing → install link, Ollama-running
  // → pull qwen). Only fetched when actually needed.
  const [lmStatus, setLmStatus] = useState(null)

  // Close inspect popover on outside click (same data-dropdown pattern used
  // by the View dropdown earlier — fixed-positioned backdrops fail when an
  // ancestor creates a stacking context; see memory `feedback_dropdown_outside_click`).
  useEffect(() => {
    if (!inspectOpen) return
    const handler = (e) => {
      if (!e.target.closest('[data-dropdown="true"]')) setInspectOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [inspectOpen])

  // Lazy /lm-status fetch when we hit a no_vision_model state, so we can
  // distinguish "Ollama isn't installed" from "Ollama is installed but no
  // vision model" — they need different CTAs.
  useEffect(() => {
    if (status !== 'no_vision_model' || lmStatus) return
    let cancelled = false
    fetch(`${API}/lm-status`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (!cancelled && data) setLmStatus(data) })
      .catch(() => { /* harmless — chip falls back to generic pull CTA */ })
    return () => { cancelled = true }
  }, [status, lmStatus])

  if (status === 'loading') {
    // Rainbow-bordered pill while qwen is processing. The conic-gradient
    // border (`.ai-border-sm`) is the app's brand cue for "AI is doing
    // work" — same pattern as PersonalModelBanner and the semantic-search
    // input — and is much more present than the previous 1.5px amber dot.
    // The "AI" text picks up the matching rainbow gradient via
    // `.ai-text-rainbow`; "ranking…" stays neutral so it reads cleanly.
    // Stroke spins 2× faster than the default `.ai-border-sm` (7s vs 14s) so
    // the chip reads as actively working rather than ambiently branded — this
    // is the only place we want urgency in the rainbow.
    return (
      <span
        className="relative ai-border-sm ai-border-fast inline-block rounded text-xs select-none"
        title="AI burst ranking in progress — comparing all photos at once"
      >
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-[#07080a]">
          <span className="ai-text-rainbow font-semibold">AI</span>
          <span className="text-[#cecece]">ranking in progress...</span>
        </span>
      </span>
    )
  }
  if (status === 'no_vision_model') {
    // Branch on whether Ollama itself is installed. If not, the pull button
    // is a lie (it would silently poll for 30 min then time out) — point at
    // the install flow instead. Default (lmStatus not yet fetched, or Ollama
    // running with no vision model) → the existing pull CTA, now with a
    // proper explanatory label.
    if (lmStatus?.status === 'not_installed') {
      return <InstallOllamaCTA compact />
    }
    return (
      <span
        className="inline-flex items-center gap-2 px-2 py-1 rounded-md bg-[rgba(232,184,74,0.10)] border border-[rgba(232,184,74,0.30)] select-none"
        title="AI burst ranking needs a vision model that can see all photos at once. qwen2.5vl:7b is ~6 GB, downloads once."
      >
        <span className="w-1.5 h-1.5 rounded-full bg-[#E8B84A] shrink-0" />
        <span className="text-xs text-[#E8B84A] whitespace-nowrap">
          AI ranking needs vision model
        </span>
        <PullVisionModelButton compact />
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span className="text-xs text-[#C97B7B] select-none" title="AI burst rank failed — falling back to score-based pick. Close + reopen to retry">
        AI rank unavailable
      </span>
    )
  }
  if (status === 'near_duplicates') {
    // Neutral, explanatory tone — this is a deliberate AI outcome, not a
    // failure. No coral, no amber. Same "≈" glyph as the grid chip for
    // visual consistency across surfaces.
    return (
      <span
        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs text-[#cecece] bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.08)] select-none"
        title="The AI determined these frames are visually near-identical (cosine ≥ 0.97). No per-photo ranking is meaningful here — the score-based pick (best technical/aesthetic) applies."
      >
        <span className="text-[#9c9c9d] font-semibold">≈</span>
        <span>Near-duplicate frames — using score-based pick</span>
      </span>
    )
  }

  // Ranked + pre-filtered → show the "top N of M" chip with a click-to-inspect
  // popover and an InfoTooltip explaining how the filter works. For ranked
  // bursts that fit in one call (≤ _MAX_MEMBERS), filtered_from === evaluated
  // length, so we stay silent — the amber ring on the hero tells the story.
  if (status === 'ranked' && result) {
    const evalIds = Array.isArray(result.evaluated_ids) ? result.evaluated_ids : []
    const total   = result.filtered_from || evalIds.length
    if (evalIds.length > 0 && total > evalIds.length) {
      const byId = images ? Object.fromEntries(images.map(im => [im.id, im])) : {}
      const evaluatedItems = evalIds
        .map(id => byId[id])
        .filter(Boolean)
      return (
        <span
          data-dropdown="true"
          className="relative inline-flex items-center gap-1 text-xs text-[#E8B84A] select-none"
        >
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setInspectOpen(v => !v) }}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-[rgba(232,184,74,0.30)] bg-[rgba(232,184,74,0.10)] hover:opacity-80 transition-opacity"
            title="Click to see which photos the AI evaluated"
          >
            AI ranked top {evalIds.length} of {total}
          </button>
          <InfoTooltip>
            The vision model evaluates a maximum of {evalIds.length} photos at once. For larger bursts the app pre-selects the most promising candidates by face sharpness, eyes-open, frame sharpness, IQA, aesthetic, and overall score — then the model ranks those. The other {total - evalIds.length} photos keep their score-based standing.
          </InfoTooltip>

          {inspectOpen && (
            <div
              data-dropdown="true"
              className="absolute top-full left-0 mt-1.5 z-50 min-w-[240px] max-w-[360px] rounded-lg bg-[#101111] border border-[rgba(255,255,255,0.10)] shadow-xl overflow-hidden"
            >
              <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-[#9c9c9d] border-b border-[rgba(255,255,255,0.06)]">
                Photos the AI evaluated ({evaluatedItems.length})
              </div>
              <div className="max-h-[260px] overflow-y-auto py-1">
                {evaluatedItems.map(im => (
                  <div
                    key={im.id}
                    className="px-3 py-1 text-xs text-[#cecece] font-mono truncate"
                    title={im.filename}
                  >
                    {im.filename}
                  </div>
                ))}
              </div>
            </div>
          )}
        </span>
      )
    }
  }

  // For ranked (unfiltered) / too_few the absence of a chip IS the signal.
  return null
}

// ── Tiny key-cap hint used in the footer ────────────────────────────────────
function Hint({ k, children }) {
  return (
    <span className="inline-flex items-center gap-1">
      <kbd className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 bg-gradient-to-b from-[#181818] to-[#0d0d0d] rounded-[3px] text-[10px] font-semibold text-[#cecece] border border-[rgba(255,255,255,0.06)] leading-none">
        {k}
      </kbd>
      <span>{children}</span>
    </span>
  )
}
