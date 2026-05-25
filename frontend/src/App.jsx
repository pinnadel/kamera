import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { createPortal } from 'react-dom'

// Maps modelInfo to the current growth/startup tier key — mirrors the tier
// definitions in PersonalModelBanner. Used to detect tier advances so the
// banner auto-re-surfaces after a milestone even if previously dismissed.
// Tracks `decided_count` (durable decisions) so the re-surface fires on the
// same edge the banner uses to flip its pill label — `training_size` lags by
// up to RETRAIN_DELTA samples.
function computeTierKey(modelInfo) {
  if (!modelInfo) return null
  const status = modelInfo.model_status || (modelInfo.ready ? 'ready' : 'untrained')
  if (status === 'ready') {
    const t = modelInfo.decided_count ?? 0
    if (t < 100) return 'calibrating'
    if (t < 200) return 'knows-your-eye'
    if (t < 500) return 'your-curator'
    return 'deeply-attuned'
  }
  return status // untrained / learning / underperforming
}

// Key cap styled to match the rest of the app (see ShortcutsModal). Tinted by
// the decision state color so the empty state previews the cull palette.
function KeyCap({ children, tone = 'neutral' }) {
  const tones = {
    keep:    'border-[rgba(125,184,154,0.40)] text-[#7DB89A]',
    maybe:   'border-[rgba(232,184,74,0.40)] text-[#E8B84A]',
    reject:  'border-[rgba(201,123,123,0.40)] text-[#C97B7B]',
    neutral: 'border-[rgba(255,255,255,0.10)] text-[#cecece]',
  }
  return (
    <kbd className={`inline-flex items-center justify-center w-7 h-7 rounded-[6px] border bg-[#161718] font-mono text-[13px] leading-none ${tones[tone]}`}>
      {children}
    </kbd>
  )
}
import {
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  CircleDot,
  Columns2,
  ExternalLink,
  Folder,
  FolderInput as FolderInputIcon,
  FolderTree,
  Grid2x2,
  HelpCircle,
  MessageSquare,
  Info,
  Layers,
  LayoutDashboard,
  MousePointerSquareDashed,
  Plus,
  Power,
  RotateCcw,
  Search,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Sparkles,
  Square,
  Trash2,
  WandSparkles,
  X,
} from 'lucide-react'
import { MoveIntoGroupModal } from './modals/MoveIntoGroupModal'
import { API } from './api'
import { BTN_ICON, BTN_PRIMARY, BTN_SECONDARY } from './ui/buttons'
import { formatEta, formatDuration } from './ui/format'
import { Spinner, DecisionWord, Toggle } from './ui/primitives'
import { DownloadToast, ToastStack } from './ui/toasts'
import { ShortcutsModal } from './modals/ShortcutsModal'
import { FEEDBACK_URL } from './version'
import { ConfirmModal } from './modals/ConfirmModal'
import { AutoCullModal } from './modals/AutoCullModal'
import { SettingsModal } from './modals/SettingsModal'
import { ImageCard } from './views/ImageCard'
import { DetailView } from './views/DetailView'
import { GroupTile } from './views/GroupTile'
import { GroupLoupe } from './views/GroupLoupe'
import { CompareView } from './views/CompareView'
import { TrainingModeView } from './views/TrainingModeView'
import { PairwiseTrainingView } from './views/PairwiseTrainingView'
import { DashboardView } from './views/DashboardView'
import { TabBar } from './views/TabBar'
import { TabFoldersForm } from './views/TabFoldersForm'
import { PersonalModelBanner } from './views/PersonalModelBanner'
import { BannerStates } from './views/BannerStates'
import { makeNewTab, tabLabel } from './tabs'
import { buildTrainingQueue } from './training'
import { usePolling } from './hooks/usePolling'
import { useTabs } from './hooks/useTabs'
import { useKeyboard } from './hooks/useKeyboard'
import { useGroups } from './hooks/useGroups'
import { useMultiSelect } from './hooks/useMultiSelect'
import { useUndoStack } from './hooks/useUndoStack'
import { useSettings } from './hooks/useSettings'
import { useHideOnScroll } from './hooks/useHideOnScroll'
import { useSort } from './hooks/useSort'
import { compareImages as sortComparator } from './sortMetrics'
import { SortPill } from './ui/SortPill'
import { FilterPill } from './ui/FilterPill'
import { ViewPill, GRID_SIZE_OPTIONS } from './ui/ViewPill'

// Main grid's ViewPill is grid-only; DetailView is now the only filmstrip
// surface, so the layout picker would just be a one-row no-op without this.
const GRID_ONLY_LAYOUT_OPTIONS = [{ id: 'grid', label: 'Grid', Icon: Grid2x2 }]
import { FILMSTRIP_CHROME_WITH_BADGES, pillBottomAboveStrip, stripHeight } from './ui/filmstripMetrics'
import { filterPredicate, getActiveFilterLabel } from './filterUtils'
import { ViewModePill } from './ViewModePill'

export default function App() {
  const [tabs, setTabs]               = useState(() => [makeNewTab()])
  const [activeTabId, setActiveTabId] = useState(null)
  const [detailOpen, setDetailOpen]       = useState(false)
  const [modelStatus, setModelStatus]     = useState({ loading: false, models: [] })
  const [activeView, setActiveView]       = useState('grid')
  const [trainingQueue, setTrainingQueue] = useState([])
  const [trainingIdx, setTrainingIdx]     = useState(0)
  const [settingsOpen, setSettingsOpen]   = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [uiScale, setUiScale] = useState(() => localStorage.getItem('pca.uiScale') || 'M')
  useEffect(() => {
    document.documentElement.dataset.uiScale = uiScale
    localStorage.setItem('pca.uiScale', uiScale)
  }, [uiScale])
  // When ON, the header `+` button auto-opens the OS folder picker. When OFF
  // (default), `+` just navigates to the empty New-analysis page where the
  // user can click "Open folder" themselves.
  const [autoOpenFinder, setAutoOpenFinder] = useState(
    () => localStorage.getItem('pca.autoOpenFinderOnNew') === '1'
  )
  useEffect(() => {
    localStorage.setItem('pca.autoOpenFinderOnNew', autoOpenFinder ? '1' : '0')
  }, [autoOpenFinder])
  // Direction the grid focus advances after a K / M / R decision. 'forward'
  // (default) moves to the next photo in grid order — matches top-to-bottom,
  // left-to-right culling. 'backward' moves to the previous photo — for users
  // who scan bottom-to-top. Arrow keys are unaffected.
  const [advanceDir, setAdvanceDir] = useState(
    () => localStorage.getItem('pca.advanceDir') === 'backward' ? 'backward' : 'forward'
  )
  useEffect(() => {
    localStorage.setItem('pca.advanceDir', advanceDir)
  }, [advanceDir])
  const [toasts, setToasts]               = useState([])
  const [decisionFilter, setDecisionFilter] = useState(null)
  // searchInput is the immediately-controlled input value. searchQuery is
  // the debounced value that flows into filtering and HighlightedText. With
  // 741 cards each running a HighlightedText loop, every keystroke previously
  // ran 741 string-search loops; debouncing collapses bursts of typing into
  // a single 150ms-delayed update.
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  useEffect(() => {
    const id = setTimeout(() => setSearchQuery(searchInput), 150)
    return () => clearTimeout(id)
  }, [searchInput])
  // Semantic search state — only active when searchMode === 'semantic'.
  const [searchMode, setSearchMode] = useState('filename')  // 'filename' | 'semantic'
  const [semanticResults, setSemanticResults] = useState(null) // [{image_id, score}] | null
  const [semanticLoading, setSemanticLoading] = useState(false)
  // Sort state lives in localStorage (key `pca.sort`) so it survives reloads
  // and is shared across tabs. `useSort` exposes the comparator-friendly
  // sortField/sortDir + the L2 metric visibility list.
  const { sortField, sortDir, setSortField, toggleSortDir, visibleMetrics } = useSort()
  const [sortOpen,  setSortOpen]            = useState(false)
  const [filterOpen, setFilterOpen]         = useState(false)
  const [tabSettingsOpen, setTabSettingsOpen] = useState(false)
  // `filter` is a discriminated-union object (see filterUtils.js): null,
  // {type:'date', from, to}, {type:'portraits'|'landscape'|'group'}, or
  // {type:'camera', value}. In-memory only — no persistence.
  const [filter, setFilter] = useState(null)
  // Search starts collapsed (icon-only). Click search icon → expand inline
  // with a width transition. Stays open while typed content remains; collapses
  // on outside-click only when the input is empty.
  const [searchExpanded, setSearchExpanded] = useState(false)
  const searchInputRef = useRef(null)
  // Two-step confirmation when enabling Watch live takes it away from another
  // tab. true = show inline "Switch?" row; cleared on confirm/cancel/menu close.
  const [watchSwitchConfirm, setWatchSwitchConfirm] = useState(false)
  const [autoCullOpen, setAutoCullOpen]     = useState(false)
  const [bulkConfirm, setBulkConfirm]       = useState(null)
  const [bulkRunning, setBulkRunning]       = useState(false)
  // Stores the tier key at which the user last dismissed the banner (e.g.
  // 'calibrating'). The banner re-surfaces automatically when the model
  // advances to a new tier — old boolean 'true'/'false' values are treated as
  // null so users get a clean slate on first run of this format.
  const [bannerDismissedAtTier, setBannerDismissedAtTier] = useState(() => {
    const stored = localStorage.getItem('pca.personalModelBannerDismissed')
    if (!stored || stored === 'true' || stored === 'false') return null
    return stored
  })
  const [bannerDismissConfirm, setBannerDismissConfirm] = useState(false)
  const [bannerStatesOpen, setBannerStatesOpen] = useState(false)
  useEffect(() => {
    const onKey = (e) => { if (e.shiftKey && e.key === 'B') setBannerStatesOpen(v => !v) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  // View pill state. `gridLayout` decides whether the main view is a
  // thumbnail grid or a sticky DetailView-style filmstrip (big preview + thumb
  // strip below). `userCols` drives the grid column count; `stripThumb`
  // drives the filmstrip thumb width in px. All three persist to localStorage
  // so the user's last view sticks across sessions / tabs.
  // Filmstrip layout was removed from the grid's ViewPill — DetailView is the
  // only filmstrip surface now. Any persisted 'filmstrip' value coerces to
  // 'grid' so returning users land in the supported layout.
  const [gridLayout, setGridLayout] = useState('grid')
  useEffect(() => { localStorage.setItem('pca.gridLayout', gridLayout) }, [gridLayout])

  // DetailView's filmstrip collapse — lifted here so the floating pill that
  // hovers above the filmstrip toolbar can react to the collapse toggle.
  const [detailFilmstripCollapsed, setDetailFilmstripCollapsed] = useState(() => {
    try { return localStorage.getItem('pca.detailFilmstripCollapsed') === '1' }
    catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('pca.detailFilmstripCollapsed', detailFilmstripCollapsed ? '1' : '0') } catch { /* quota / disabled */ }
  }, [detailFilmstripCollapsed])
  const [userCols, setUserCols] = useState(() => {
    const raw = parseInt(localStorage.getItem('pca.gridCols') || '', 10)
    return Number.isFinite(raw) && GRID_SIZE_OPTIONS.some(o => o.value === raw) ? raw : 6
  })
  useEffect(() => {
    if (userCols != null) localStorage.setItem('pca.gridCols', String(userCols))
  }, [userCols])
  // Filmstrip thumbnail width. User resizes by dragging the top edge of the
  // filmstrip toolbar (DetailView / GroupLoupe); previously a 4-step dropdown.
  // Clamp keeps the persisted value sane across sessions.
  const STRIP_THUMB_MIN = 80
  const STRIP_THUMB_MAX = 260
  const [stripThumb, setStripThumb] = useState(() => {
    const raw = parseInt(localStorage.getItem('pca.stripThumb') || '', 10)
    if (!Number.isFinite(raw)) return 120
    return Math.max(STRIP_THUMB_MIN, Math.min(STRIP_THUMB_MAX, raw))
  })
  useEffect(() => { localStorage.setItem('pca.stripThumb', String(stripThumb)) }, [stripThumb])

  // Last expanded thumb size — when the user drags the strip below MIN it
  // collapses, but we restore this on re-expand so the operation feels
  // reversible. Survives session via localStorage.
  const [stripThumbAtExpand, setStripThumbAtExpand] = useState(() => {
    const raw = parseInt(localStorage.getItem('pca.stripThumbAtExpand') || '', 10)
    if (!Number.isFinite(raw)) return 120
    return Math.max(STRIP_THUMB_MIN, Math.min(STRIP_THUMB_MAX, raw))
  })
  useEffect(() => { localStorage.setItem('pca.stripThumbAtExpand', String(stripThumbAtExpand)) }, [stripThumbAtExpand])

  // Drag-to-resize the filmstrip. Wired to a 1.5 px handle on the toolbar's
  // top edge. Vertical drag: up = bigger thumbs, down = smaller. Dragging
  // below MIN collapses; dragging back up during the SAME drag re-expands
  // at MIN once the cursor's pulled back far enough. We track collapsed
  // state in a local variable inside the drag rather than reading React
  // state from the closure — the closure value is frozen at mousedown, so
  // it'd lie about the current state once we cross MIN during the drag.
  const startStripResize = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    const startY = e.clientY
    const wasCollapsed = detailFilmstripCollapsed
    // Anchor for the drag math: if collapsed, treat the strip as 0-height
    // (any downward move is a no-op; upward moves grow from MIN).
    const startSize = wasCollapsed ? 0 : stripThumb
    // Local mirror of the collapse state during this drag. We toggle it
    // alongside setDetailFilmstripCollapsed so the next move tick can see
    // the up-to-date value without a re-render.
    let isCollapsed = wasCollapsed
    let savedRestoreTarget = false
    const onMove = (ev) => {
      // Dragging up (negative dy) grows the strip.
      const dy = ev.clientY - startY
      const next = startSize - dy
      if (next < STRIP_THUMB_MIN) {
        // Save the pre-drag size as the restore target the FIRST time we
        // cross into collapse during this drag (only meaningful if we
        // started expanded — if wasCollapsed there's nothing to save).
        if (!isCollapsed && !savedRestoreTarget && startSize >= STRIP_THUMB_MIN) {
          setStripThumbAtExpand(startSize)
          savedRestoreTarget = true
        }
        if (!isCollapsed) {
          setDetailFilmstripCollapsed(true)
          isCollapsed = true
        }
      } else {
        const clamped = Math.min(STRIP_THUMB_MAX, next)
        if (isCollapsed) {
          setDetailFilmstripCollapsed(false)
          isCollapsed = false
        }
        setStripThumb(clamped)
      }
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      // Swallow the click that fires right after mouseup. Without this, the
      // click bubbles to DetailView's outer overlay (onClick={onClose}) and
      // closes the panel — particularly noticeable when the cursor ends up
      // below the strip after a drag-to-collapse.
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
  }, [stripThumb, detailFilmstripCollapsed])

  // Restore the previous expanded size whenever the user toggles back from
  // collapsed via the chevron.
  const onToggleFilmstripCollapsed = useCallback(() => {
    setDetailFilmstripCollapsed(prev => {
      if (prev) {
        // Re-expand: restore the last good size.
        setStripThumb(stripThumbAtExpand)
        return false
      }
      // Collapse: remember the current size for the next expand.
      setStripThumbAtExpand(stripThumb)
      return true
    })
  }, [stripThumb, stripThumbAtExpand])
  const [viewOpen, setViewOpen] = useState(false)
  // When set, DetailView is opened scoped to a similarity group (filmstrip
  // pinned to the bottom, arrow keys cycle within the group). Cleared on
  // close so DetailView for plain grid photos reverts to global navigation.
  const [detailGroupContext, setDetailGroupContext] = useState(null)
  // Portal target for the bottom toolbar pill — DetailView assigns this via
  // setBottomToolbarSlot(el) when its FilmstripToolbar mounts, so the pill
  // can render *inside* the toolbar (Luminar-style) instead of floating.
  // When null (no DetailView open), the pill renders as a floating fixed div.
  const [bottomToolbarSlot, setBottomToolbarSlot] = useState(null)
  const [compareImages, setCompareImages] = useState([])  // max 4
  const [compareOpen, setCompareOpen]     = useState(false)
  const gridRef = useRef(null)
  // Suppress the launch-time scrollIntoView. selectedIdx defaults to 0 on a
  // restored tab even though the user hasn't actually picked anything, and the
  // grid is sorted — so images[0] is usually nowhere near the visual top.
  // Without this gate the page autoscrolls to a "random" deep tile on open.
  // Flipped to true by the first real cursor movement (arrow nav, click, etc.).
  const userHasNavigatedRef = useRef(false)

  // Reveal-on-scroll-up header. Hides the App bar + TabBar (and the filter
  // bar nested inside) when scrolling down, slides them back into view on
  // any upward scroll so the user can reach "+ New analysis" / settings
  // without scrolling to the top.
  const headerHidden = useHideOnScroll()

  // Close any open pill dropdown on outside click. The pill bar is its own
  // stacking context (transform on a z-50 element), so a `fixed inset-0`
  // backdrop can't capture clicks elsewhere. Document-level mousedown
  // sidesteps that — every menu carries data-dropdown="true".
  useEffect(() => {
    if (!sortOpen && !filterOpen && !viewOpen && !tabSettingsOpen && !searchExpanded) return
    const onDown = (e) => {
      if (e.target.closest('[data-dropdown="true"]')) return
      setSortOpen(false); setFilterOpen(false); setViewOpen(false); setTabSettingsOpen(false)
      setWatchSwitchConfirm(false)
      // Search collapses on outside click only when the input is empty —
      // otherwise an active filter would be hidden by an idle click.
      if (searchExpanded && !searchInput) setSearchExpanded(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [sortOpen, filterOpen, viewOpen, tabSettingsOpen, searchExpanded, searchInput])

  // Closing the menu (or switching tabs) clears any pending Watch live confirm.
  useEffect(() => { if (!tabSettingsOpen) setWatchSwitchConfirm(false) }, [tabSettingsOpen])

  // Tab-scoped popovers should not survive a tab switch.
  useEffect(() => { setTabSettingsOpen(false) }, [activeTabId])

  // ── Unified toast system ──────────────────────────────────────────────────
  // Auto-dismiss now lives in ToastStack (per-toast timer) so hover can pause
  // it. App.jsx just owns the queue + the persistent flag.
  const addToast = useCallback((toast) => {
    const id = Date.now() + Math.random()
    setToasts(prev => [...prev, { id, duration: 4000, ...toast }])
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  // ── Tab management (useTabs) ──────────────────────────────────────────────
  const {
    loading,
    error,
    updateActiveTab,
    setImages,
    setSelectedIdx,
    setSelectedGroupId,
    setAnalyzing,
    setAnalyzeResult,
    setProgress,
    setResultDismissed,
    loadImages,
    runAnalysisForTab,
    stopAnalysis,
    stopping,
    errorsExpanded,
    setErrorsExpanded,
    skippedExpanded,
    setSkippedExpanded,
    toggleWatchLive,
    isLiveActive,
    startNewAnalysis,
    overwriteRequest,
    setOverwriteRequest,
    confirmOverwrite,
    busyRequest,
    setBusyRequest,
    closeTabRequest,
    setCloseTabRequest,
    requestCloseTab,
    confirmCloseTab,
    reorderTabs,
    subfolderRequest,
    resolveSubfolderRequest,
    dismissSubfolderRequest,
  } = useTabs({ tabs, setTabs, activeTabId, setActiveTabId, addToast })

  // ── Derive active-tab fields so existing call-sites keep working ──────────
  const activeTab = useMemo(
    () => tabs.find(t => t.id === activeTabId) || tabs[0] || null,
    [tabs, activeTabId]
  )

  const images          = activeTab?.images          ?? []
  const selectedIdx     = activeTab?.selectedIdx     ?? 0
  const selectedGroupId = activeTab?.selectedGroupId ?? null
  const analyzing       = activeTab?.status === 'analyzing'
  const progress        = activeTab?.progress        ?? null
  const analyzeResult   = activeTab?.analyzeResult   ?? null
  const resultDismissed = activeTab?.resultDismissed ?? false

  // ── Semantic search fetch (depends on activeTab being defined above) ─────────
  useEffect(() => {
    if (searchMode !== 'semantic') { setSemanticResults(null); setSemanticLoading(false); return }
    const q = searchQuery.trim()
    if (!q) { setSemanticResults(null); setSemanticLoading(false); return }
    const folder = activeTab?.folderPath
    const params = new URLSearchParams({ q, limit: '100' })
    if (folder) params.set('source_folder', folder)
    let cancelled = false
    setSemanticLoading(true)
    const id = setTimeout(() => {
      fetch(`${API}/search?${params}`)
        .then(r => r.json())
        .then(data => { if (!cancelled) setSemanticResults(data.results ?? []) })
        .catch(() => { if (!cancelled) setSemanticResults([]) })
        .finally(() => { if (!cancelled) setSemanticLoading(false) })
    }, 250)
    return () => { cancelled = true; clearTimeout(id); setSemanticLoading(false) }
  }, [searchMode, searchQuery, activeTab?.folderPath])

  // ── Quit (production launcher only) ──────────────────────────────────────
  const [quitting, setQuitting] = useState(false)
  async function quitApp() {
    setQuitting(true)
    try { await fetch(`${API}/quit`, { method: 'POST' }) } catch (_) {}
    // Give the server 1s to shut down, then close the tab.
    setTimeout(() => window.close(), 1000)
  }

  // ── Settings + personal model (useSettings) ───────────────────────────────
  const {
    settings,
    setSettings,
    loadSettings,
    modelInfo,
    loadModelInfo,
    trainModel,
    trainingModel,
    autoGenerate,
    setAutoGenerate,
  } = useSettings({ addToast, loadImages })

  const currentTierKey = computeTierKey(modelInfo)
  const bannerDismissed = bannerDismissedAtTier !== null && bannerDismissedAtTier === currentTierKey

  // ── Similarity groups (useGroups) ─────────────────────────────────────────
  const {
    threshold,
    setThreshold,
    groupsLoading,
    enrichedGroups,
    loadGroups,
    sendGroupDecision: _sendGroupDecision,
    loupeGroup,
    enterLoupe,
    exitLoupe,
    groupMode,
    setGroupMode,
    faceThreshold,
    setFaceThreshold,
    timeGapSeconds,
    setTimeGapSeconds,
    peoplePendingReanalysis,
    setLoupeAnchorId,
    loadGroupsAndPrerank,
  } = useGroups({ images, activeTab, addToast, loadModelInfo })

  // ── Multi-select for manual group composition ─────────────────────────────
  // The grid and the loupe each instantiate their own useMultiSelect copy
  // (the loupe's lives inside GroupLoupe.jsx). Selecting photos in one view
  // should never bleed into the other.
  const gridMultiSelect = useMultiSelect()
  // App-global undo stack (depth 5). All decisions + group mutations push
  // here; U / Cmd+Z anywhere pops the top entry and replays the inverse.
  const undoStack = useUndoStack()
  // Visual feedback: which GroupTile is currently being hovered as a
  // drop target. Cleared on dragleave / dragend / drop.
  const [dropHoverGroupId, setDropHoverGroupId] = useState(null)

  // Esc exits multi-select while the grid is the active surface. Gated on
  // `!loupeOpen` so it doesn't fight the loupe's own Esc handling. The
  // loupe's multi-select is scoped to its own useMultiSelect instance and
  // handles its own Esc.
  useEffect(() => {
    if (!gridMultiSelect.isSelectMode) return
    const handler = (e) => {
      if (e.key !== 'Escape') return
      if (loupeGroup || detailOpen) return
      gridMultiSelect.exit()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [gridMultiSelect, loupeGroup, detailOpen])

  // POST /set-manual-group — the workhorse for every manual move (drag,
  // selection toolbar, loupe rail, modals). All five modes route through
  // here. On success the grid + loupe rail re-fetch groups so the new
  // composition becomes visible, AND a snapshot of prior assignments lands
  // on the undo stack (mode === 'restore_assignments' is skipped — it IS
  // an undo replay and pushing it back on would loop).
  const setManualGroup = useCallback(async ({ image_ids, mode, target_image_id = null, assignments = null }) => {
    if (mode === 'restore_assignments') {
      // Replay path used by runUndo. Pushes nothing.
      try {
        const res = await fetch(`${API}/set-manual-group`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: [], mode, assignments }),
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          addToast({ type: 'error', message: data.detail || `Undo failed (${res.status})`, duration: 6000 })
          return false
        }
        loadGroups()
        return true
      } catch (err) {
        addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 6000 })
        return false
      }
    }

    if (!image_ids || image_ids.length === 0) return
    // Snapshot prior manual_group_id for every affected photo BEFORE the
    // mutation. For join_group the target also gets included since it may
    // have been the anchor donor or null prior to the join. The list comes
    // from local `images` state — /images now returns manual_group_id, so
    // this is accurate without an extra round-trip.
    const affectedIds = mode === 'join_group' && target_image_id != null
      ? [...new Set([...image_ids, target_image_id])]
      : image_ids
    const priorById = new Map(
      affectedIds.map(id => {
        const img = images.find(i => i.id === id)
        return [id, img?.manual_group_id ?? null]
      })
    )

    try {
      const res = await fetch(`${API}/set-manual-group`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids, mode, target_image_id }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        addToast({ type: 'error', message: data.detail || `Move failed (${res.status})`, duration: 6000 })
        return
      }
      const data = await res.json()
      // Update local images state so the snapshot stays consistent with the
      // server. The new manual_group_id for affected photos comes from the
      // response when known, otherwise leave it to the next /images poll.
      if (data.manual_group_id !== undefined) {
        setImages(prev => prev.map(img => (
          priorById.has(img.id)
            ? { ...img, manual_group_id: data.manual_group_id ?? img.manual_group_id }
            : img
        )))
      }
      undoStack.push({
        kind: 'group',
        label: mode,
        assignments: Array.from(priorById.entries()).map(([id, prev_manual_group_id]) => ({
          image_id: id, manual_group_id: prev_manual_group_id,
        })),
      })
      const n = data.updated ?? image_ids.length
      const verb = mode === 'singletons' ? 'removed from group' :
                   mode === 'new_group'  ? 'grouped together' :
                   mode === 'join_group' ? 'moved into group' :
                   'reset to auto-cluster'
      addToast({ type: 'success', message: `${n} photo${n === 1 ? '' : 's'} ${verb}`, duration: 3500 })
      // Use the prerank-enqueuing variant so freshly-formed manual groups
      // get AI-ranked in the background without the user having to open
      // them first. enqueue_groups dedupes against the burst_rankings
      // cache so re-posting unchanged groups is a no-op. The undo replay
      // path above stays on bare loadGroups() — replays restore prior
      // state that was likely already ranked.
      loadGroupsAndPrerank()
    } catch (err) {
      addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 6000 })
    }
  }, [addToast, loadGroupsAndPrerank, images, setImages, undoStack])

  // Promote a photo to a similarity group's BEST. POSTs the override to the
  // settings table via /group-hero, then reloads groups so the amber badge +
  // ring + grid hero thumbnail all reflect the new pick. Used by the loupe's
  // "B" hotkey.
  //
  // Anchor on the new hero before reloading: loupeGroupId is keyed by the
  // group's best_image_id, which is exactly what we just changed. Without
  // the anchor, loadGroups returns a group whose new best_image_id no longer
  // matches loupeGroupId, loupeGroup resolves to null, and the safety effect
  // in useGroups closes the loupe. The anchor lets the re-anchor effect
  // re-bind loupeGroupId to the same group's new hero id, keeping the loupe
  // open on the photo the user just promoted.
  const setGroupHero = useCallback(async ({ group_image_ids, hero_image_id }) => {
    try {
      const res = await fetch(`${API}/group-hero`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_image_ids, hero_image_id }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        addToast({ type: 'error', message: data.detail || `Set best failed (${res.status})`, duration: 6000 })
        return
      }
      setLoupeAnchorId(hero_image_id)
      loadGroups()
    } catch (err) {
      addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 6000 })
    }
  }, [addToast, loadGroups, setLoupeAnchorId])

  // ── Polling (usePolling) ──────────────────────────────────────────────────
  // Placed *after* useGroups so we can hand it `loadGroupsAndPrerank` as
  // the batch-complete callback. Two things happen on batch completion:
  //   1. /similarity-groups is fetched so the grid actually shows the
  //      newly-clustered bursts (otherwise the user has to refresh).
  //   2. /prerank-groups is POSTed so the backend can warm the burst-
  //      ranking cache in the background — by the time the user opens
  //      a loupe, the amber ring is already there (no 60-90 s spinner).
  // Threshold/slider changes still use the bare `loadGroups`, so dragging
  // a slider doesn't keep re-enqueueing prerank work.
  usePolling({
    tabs,
    setTabs,
    setModelStatus,
    onBatchComplete: loadGroupsAndPrerank,
    // When the prerank worker advances, re-fetch /similarity-groups (NOT
    // /prerank-groups — we don't want to re-enqueue, just refresh per-tile
    // states). loadGroups also runs on initial mount and threshold changes;
    // this just adds "after the worker finished one item" as a new trigger.
    onPrerankAdvance: loadGroups,
  })

  // Cancel any in-flight prerank when the user switches tabs/folders so
  // the worker doesn't waste cycles on a folder they just left. Fire-
  // and-forget; the worker checks the cancel event between items.
  useEffect(() => {
    if (!activeTabId) return
    fetch(`${API}/prerank-cancel`, { method: 'POST' }).catch(() => {})
  }, [activeTabId])

  // Wrap sendGroupDecision so the local images array is patched after the
  // network call (useGroups returns the new_path / decision for us to apply),
  // and the action lands on the undo stack so U can pop it from any surface.
  const sendGroupDecision = useCallback(async (imageId, decision) => {
    const prevImg = images.find(i => i.id === imageId)
    const prevDecision = prevImg?.decision ?? null
    const result = await _sendGroupDecision(imageId, decision)
    if (result) {
      setImages(prev => prev.map(img =>
        img.id === imageId ? { ...img, decision: result.decision, file_path: result.new_path } : img
      ))
      undoStack.push({
        kind: 'decision',
        label: decision,
        items: [{ id: imageId, prev: prevDecision, next: decision }],
        timestamp: Date.now(),
      })
      // Same group-resolve check as sendDecision — when a K/M/R inside the
      // loupe finishes off the last undecided photo, auto-close + advance
      // instead of leaving the loupe parked on a fully-decided group.
      maybeResolveActiveGroupRef.current?.([imageId])
    }
  }, [_sendGroupDecision, setImages, images, undoStack])

  // ── Session stats ─────────────────────────────────────────────────────────
  const avgKeptScore = useMemo(() => {
    const kept = images.filter(img => img.decision === 'keep' && img.overall_score != null)
    if (kept.length === 0) return null
    return kept.reduce((sum, img) => sum + img.overall_score, 0) / kept.length
  }, [images])

  const sortedImages = useMemo(() => {
    const sorted = [...images]
    sorted.sort((a, b) => sortComparator(a, b, sortField, sortDir))
    return sorted
  }, [images, sortField, sortDir])

  // ── Grid items ────────────────────────────────────────────────────────────
  const gridItems = useMemo(() => {
    const groupByImageId = new Map()
    enrichedGroups.forEach(group => group.images.forEach(img => groupByImageId.set(img.id, group)))
    const seen = new Set()
    const items = []
    for (const image of sortedImages) {
      const group = groupByImageId.get(image.id)
      if (!group) {
        items.push({ type: 'image', image })
      } else if (!seen.has(group.best_image_id)) {
        seen.add(group.best_image_id)
        items.push({ type: 'group', group })
      }
    }
    return items
  }, [sortedImages, enrichedGroups])

  // Groups ordered to match the grid: a group's rank is its position in
  // gridItems (which already reflects the global sort). Any group not
  // represented in gridItems (defensive — shouldn't normally happen) is
  // appended at the end in enrichedGroups order. Drives the GroupLoupe
  // left rail so opening the loupe doesn't reshuffle groups under the user.
  const sortedRailGroups = useMemo(() => {
    const rankByGroupId = new Map()
    gridItems.forEach((item, idx) => {
      if (item.type === 'group' && !rankByGroupId.has(item.group.best_image_id)) {
        rankByGroupId.set(item.group.best_image_id, idx)
      }
    })
    return [...enrichedGroups].sort((a, b) => {
      const ra = rankByGroupId.has(a.best_image_id) ? rankByGroupId.get(a.best_image_id) : Number.MAX_SAFE_INTEGER
      const rb = rankByGroupId.has(b.best_image_id) ? rankByGroupId.get(b.best_image_id) : Number.MAX_SAFE_INTEGER
      return ra - rb
    })
  }, [gridItems, enrichedGroups])

  const decisionCounts = useMemo(() => ({
    all:       images.length,
    undecided: images.filter(img => !img.decision && img.analysis_status === 'done').length,
    keep:      images.filter(img => img.decision === 'keep').length,
    maybe:     images.filter(img => img.decision === 'maybe').length,
    reject:    images.filter(img => img.decision === 'reject').length,
  }), [images])

  const displayGridItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()

    let filtered = gridItems
    // Combined filter pass: groups are kept as groups when ANY member
    // matches, annotated with a filterContext so GroupTile can render the
    // partial-match dim + "X of Y kept" footer + mini-badge. Previously
    // groups were collapsed to their hero photo, which hid any group whose
    // hero wasn't the decided/matching photo (the original bug: marking a
    // non-hero as Maybe/Reject made the group disappear from those filter
    // views entirely).
    const decisionMatches = decisionFilter
      ? (img => decisionFilter === 'undecided'
          ? (!img.decision && img.analysis_status === 'done')
          : img.decision === decisionFilter)
      : null
    const compositionMatches = filter ? filterPredicate(filter) : null
    const compositionLabel   = filter ? getActiveFilterLabel(filter) : null
    if (decisionMatches || compositionMatches) {
      const passesAll = img =>
        (!decisionMatches    || decisionMatches(img)) &&
        (!compositionMatches || compositionMatches(img))
      filtered = filtered.flatMap(item => {
        if (item.type !== 'group') {
          return passesAll(item.image) ? [item] : []
        }
        const matchingCount = item.group.images.reduce(
          (n, img) => n + (passesAll(img) ? 1 : 0), 0,
        )
        if (matchingCount === 0) return []
        return [{
          ...item,
          filterContext: {
            decision: decisionFilter || null,
            label: compositionLabel,
            matchingCount,
            total: item.group.size,
          },
        }]
      })
    }
    if (searchMode === 'semantic' && semanticResults !== null) {
      // Semantic mode: filter to matched images ranked by relevance score.
      // Groups are dissolved — search is about visual content, not burst sets.
      const scoreMap = new Map(semanticResults.map(r => [r.image_id, r.score]))
      const matched = []
      for (const item of filtered) {
        if (item.type === 'image') {
          const score = scoreMap.get(item.image.id)
          if (score !== undefined) matched.push({ item, score })
        } else {
          // For groups, surface the highest-scoring member image as a standalone card.
          let bestScore = -1; let bestImg = null
          for (const img of item.group.images) {
            const s = scoreMap.get(img.id) ?? -1
            if (s > bestScore) { bestScore = s; bestImg = img }
          }
          if (bestImg !== null) matched.push({ item: { type: 'image', image: bestImg }, score: bestScore })
        }
      }
      matched.sort((a, b) => b.score - a.score)
      return matched.map(m => m.item)
    }
    if (q) {
      filtered = filtered.filter(item => {
        if (item.type === 'image') {
          return (item.image.filename || '').toLowerCase().includes(q)
        }
        return item.group.images.some(img => (img.filename || '').toLowerCase().includes(q))
      })
    }
    return filtered
  }, [gridItems, decisionFilter, filter, searchQuery, searchMode, semanticResults])

  // Index of the currently-focused grid cell — drives scrollIntoView and the
  // GroupTile's selection ring. Computed against the filtered/visible list so
  // it lines up with the data-grid-idx attribute we render below.
  const selectedGridIdx = useMemo(() => {
    if (selectedGroupId != null) {
      return displayGridItems.findIndex(item =>
        item.type === 'group' && item.group.best_image_id === selectedGroupId
      )
    }
    return displayGridItems.findIndex(item =>
      item.type === 'image' && item.image.id === images[selectedIdx]?.id
    )
  }, [displayGridItems, images, selectedIdx, selectedGroupId])

  // If the focused group leaves the visible list (filter change, group
  // dissolves after re-clustering, etc.), drop the group cursor so the user
  // isn't stuck pointing at nothing.
  useEffect(() => {
    if (selectedGroupId == null) return
    const stillVisible = displayGridItems.some(item =>
      item.type === 'group' && item.group.best_image_id === selectedGroupId
    )
    if (!stillVisible) setSelectedGroupId(null)
  }, [displayGridItems, selectedGroupId, setSelectedGroupId])

  // Scroll selected cell into view AND sync DOM focus to it, so:
  //   - Tab into the grid lands on the highlighted cell.
  //   - Tab out of the grid resumes from the highlighted cell.
  //   - Arrow nav keeps the browser's focus ring (suppressed via outline-none)
  //     and our cyan ring on the same element.
  // Only steal focus when it's already inside the grid — never from search,
  // settings, modals, or any other surface. preventScroll avoids the browser
  // double-scrolling on top of our explicit scrollIntoView.
  useEffect(() => {
    if (!gridRef.current || selectedGridIdx < 0) return
    if (!userHasNavigatedRef.current) return
    const el = gridRef.current.querySelector(`[data-grid-idx="${selectedGridIdx}"]`)
    if (!el) return
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    // Reclaim focus when it's already inside the grid OR when nothing owns it
    // (document.body) — the latter happens after GroupLoupe / DetailView
    // unmount via closeLoupeAndAdvance: focus falls to <body>, and without
    // this the next K/M/R is ignored by gridHasFocus() until the user clicks
    // back into the grid. We deliberately don't steal focus from search,
    // settings, or any other surface that holds a real focused element.
    const active = document.activeElement
    const focusUnowned = !active || active === document.body
    if (gridRef.current.contains(active) || focusUnowned) {
      el.focus({ preventScroll: true })
    }
  }, [selectedIdx, selectedGridIdx])

  // Flip the navigation-gate on the first real user interaction with the grid
  // (pointer down on a tile or any cursor-moving key while focus is in the
  // grid). After this, the scrollIntoView effect above is allowed to run.
  useEffect(() => {
    const el = gridRef.current
    if (!el) return
    const arm = () => { userHasNavigatedRef.current = true }
    const armOnKey = (e) => {
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key)) {
        arm()
      }
    }
    el.addEventListener('pointerdown', arm)
    window.addEventListener('keydown', armOnKey)
    return () => {
      el.removeEventListener('pointerdown', arm)
      window.removeEventListener('keydown', armOnKey)
    }
  }, [])

  // ── sendDecision (main grid) ──────────────────────────────────────────────
  const sendDecision = useCallback(async (decision) => {
    if (images.length === 0) return
    const image = images[selectedIdx]
    const prevDecision = image?.decision ?? null

    try {
      const res = await fetch(`${API}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: image.id, decision }),
      })
      const data = await res.json()

      if (!res.ok) {
        addToast({ type: 'error', message: data.detail || `Move failed (${res.status})`, duration: 8000 })
        return
      }

      setImages(prev =>
        prev.map((img, idx) =>
          idx === selectedIdx ? { ...img, decision, file_path: data.new_path } : img
        )
      )
      undoStack.push({
        kind: 'decision',
        label: decision,
        items: [{ id: image.id, prev: prevDecision, next: decision }],
        timestamp: Date.now(),
      })
      // A decision is a navigation event too — arm the scroll gate so the
      // grid follows the cursor onto the next undecided photo. Without this,
      // K/M/R from a cold launch would advance selection but leave the grid
      // parked, hiding the new selection below the fold.
      userHasNavigatedRef.current = true
      // Advance the grid cursor along the *visible* list (displayGridItems),
      // not the raw `images` array, so the next stop can be a group tile.
      // Falls back to ±1 in `images` if we can't locate the current photo
      // in the visible list (filter excluded it, race, etc.).
      const curIdx = displayGridItems.findIndex(it =>
        it.type === 'image' && it.image.id === image.id
      )
      const step = advanceDir === 'backward' ? -1 : +1
      const nextItem = curIdx >= 0 ? displayGridItems[curIdx + step] : null
      if (nextItem?.type === 'group') {
        setSelectedGroupId(nextItem.group.best_image_id)
      } else if (nextItem?.type === 'image') {
        setSelectedGroupId(null)
        setSelectedIdx(images.findIndex(img => img.id === nextItem.image.id))
      } else {
        // End of visible list — keep the photo cursor in range as before.
        setSelectedGroupId(null)
        setSelectedIdx(prev => advanceDir === 'backward'
          ? Math.max(prev - 1, 0)
          : Math.min(prev + 1, images.length - 1))
      }
      // If this decision resolved the active culling group (loupe is open,
      // DetailView is in group context, or grid-filmstrip is focused on a
      // group), close those surfaces and advance past the group. Runs after
      // the advance above so the no-op case (group not resolved) doesn't
      // interfere with normal next-photo cursor movement.
      maybeResolveActiveGroupRef.current?.([image.id])
      loadModelInfo()
    } catch (err) {
      addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 8000 })
    }
  }, [images, selectedIdx, advanceDir, loadModelInfo, addToast, setImages, setSelectedIdx, displayGridItems, setSelectedGroupId, undoStack])

  // ── undoImage — per-photo undo ────────────────────────────────────────────
  // Reverses the decision of a specific image. Called from grid (U-key on the
  // selected photo), GroupLoupe (U on the focused photo in the loupe), and
  // DetailView (U on the open photo). Backend reconstructs previous_path
  // from images.source_folder so the caller doesn't have to remember it.
  // Silent no-op when the photo has no decision (matches Cmd+Z conventions).
  const undoImage = useCallback(async (imageId) => {
    const img = images.find(i => i.id === imageId)
    if (!img || !img.decision) return  // nothing to undo

    try {
      const res = await fetch(`${API}/undo-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        // FastAPI/Pydantic validation errors return `detail` as an array of
        // {loc, msg, type} objects, not a string. Rendering that directly as
        // a React child blanks the screen. Coerce to a readable string.
        const detail = typeof data.detail === 'string'
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map(d => d?.msg || JSON.stringify(d)).join('; ')
            : `Undo failed (${res.status})`
        addToast({ type: 'error', message: detail, duration: 6000 })
        return
      }
      setImages(prev => prev.map(i =>
        i.id === imageId ? { ...i, decision: null, file_path: data.new_path } : i
      ))
      loadModelInfo()
    } catch (err) {
      addToast({ type: 'error', message: `Undo failed: ${err.message}`, duration: 6000 })
    }
  }, [images, addToast, setImages, loadModelInfo])

  // ── Training mode ─────────────────────────────────────────────────────────
  const enterTrainingMode = useCallback(() => {
    const queue = buildTrainingQueue(images, {
      keepThreshold:  settings?.keep_threshold  ?? 70,
      maybeThreshold: settings?.maybe_threshold ?? 45,
    })
    if (queue.filter(q => !q.isReshow).length === 0) return
    setTrainingQueue(queue)
    setTrainingIdx(0)
    setActiveView('train')
  }, [images, settings])

  const enterPairwiseMode = useCallback(() => {
    setActiveView('pairwise')
  }, [])

  const advanceTraining = useCallback(() => {
    setTrainingIdx(prev => {
      const next = prev + 1
      if (next >= trainingQueue.length) {
        setActiveView('grid')
        loadImages()
        loadModelInfo()
        return 0
      }
      return next
    })
    loadImages()
  }, [trainingQueue.length, loadImages, loadModelInfo])

  // ── Bulk decision (used by GroupLoupe batch actions) ──────────────────────
  // Single round-trip via /bulk-decision, then refreshes images so
  // file_path / decision badges reflect the moves on disk.
  const bulkDecide = useCallback(async (imageIds, decision) => {
    if (!imageIds || imageIds.length === 0) return
    // Snapshot prior decisions before mutating — needed for undo + amend-previous.
    const prevMap = new Map(
      imageIds.map(id => {
        const img = images.find(i => i.id === id)
        return [id, img?.decision ?? null]
      })
    )
    try {
      const res = await fetch(`${API}/bulk-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds, decision }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        addToast({ type: 'error', message: data.detail || `Bulk decision failed (${res.status})`, duration: 8000 })
        return
      }
      const data = await res.json().catch(() => ({}))
      const errorIds = new Set((data.errors || []).map(e => e.id))
      const successfulIds = imageIds.filter(id => !errorIds.has(id))
      if (successfulIds.length > 0) {
        undoStack.push({
          kind: 'decision',
          label: successfulIds.length === 1 ? decision : `bulk ${decision}`,
          items: successfulIds.map(id => ({
            id, prev: prevMap.get(id) ?? null, next: decision,
          })),
          timestamp: Date.now(),
        })
      }
      await loadImages()
      // Same group-resolve check as sendDecision. Runs *after* loadImages so
      // activeCullGroup.images reflects the just-applied decisions; without
      // this, the bulk "Keep best · Reject rest" path inside GroupLoupe or
      // DetailView would leave the loupe parked on a fully-decided group.
      maybeResolveActiveGroupRef.current?.(imageIds)
      loadModelInfo()
    } catch (err) {
      addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 8000 })
    }
  }, [images, loadImages, loadModelInfo, addToast, undoStack])

  // Open DetailView from inside the GroupLoupe. Sets the global selectedIdx
  // to the focused photo and stores a group context so DetailView's prev/next
  // cycle within the group instead of the global images array.
  const openDetailFromLoupe = useCallback((imageId, group) => {
    const idx = images.findIndex(img => img.id === imageId)
    if (idx >= 0) setSelectedIdx(idx)
    setSelectedGroupId(null)
    setDetailGroupContext(group)
    setDetailOpen(true)
  }, [images, setSelectedIdx, setSelectedGroupId])

  // Called when the last undecided photo in a group has just been decided —
  // either via GroupLoupe (K/M/R or batch), DetailView opened from the loupe,
  // or DetailView in grid-filmstrip mode focused on the group. Closes any
  // open loupe / detail surface that was scoped to this group, then advances
  // the grid cursor to the item *after* the just-resolved group so a fully
  // culled group hands focus on rather than parking on its tile.
  const closeLoupeAndAdvance = useCallback((groupBestId = null) => {
    const groupId = groupBestId ?? loupeGroup?.best_image_id
    // Stamp the resolved marker first so any ref-triggered deferred call for
    // the same group (from sendGroupDecision firing in parallel with
    // GroupLoupe's own sync onAllDecided) becomes a no-op.
    if (groupId != null) lastResolvedGroupRef.current = groupId
    // Tear down both surfaces — either may be open depending on entry path.
    exitLoupe()
    setDetailOpen(false)
    setDetailGroupContext(null)
    if (groupId == null) return
    // Loupe-driven cull is a navigation event — arm the scroll gate so the
    // grid scrolls to the next group when the loupe closes.
    userHasNavigatedRef.current = true
    const idx = displayGridItems.findIndex(it =>
      it.type === 'group' && it.group.best_image_id === groupId
    )
    if (idx === -1) { setSelectedGroupId(null); return }
    const nextItem = displayGridItems[idx + 1]
    if (!nextItem) {
      // End of list — drop the group cursor; selectedIdx stays where it was.
      setSelectedGroupId(null)
      return
    }
    if (nextItem.type === 'group') {
      setSelectedGroupId(nextItem.group.best_image_id)
    } else {
      setSelectedGroupId(null)
      const i = images.findIndex(img => img.id === nextItem.image.id)
      if (i >= 0) setSelectedIdx(i)
    }
  }, [loupeGroup, exitLoupe, displayGridItems, images, setSelectedIdx, setSelectedGroupId, setDetailOpen, setDetailGroupContext])

  // Group that's currently scoped to the user's culling attention — used by
  // sendDecision / bulkDecide to detect "this decision resolves the last
  // undecided photo in the group" and auto-close + advance. Three entry paths:
  //   · GroupLoupe open (loupeGroup set; DetailView may or may not be on top)
  //   · DetailView in group context (detailGroupContext set; loupeGroup also
  //     set because openDetailFromLoupe leaves loupeGroupId in place)
  //   · Grid-filmstrip mode with a group hero focused (selectedGroupId set)
  //
  // All three resolve to the same live group memo via displayGridItems so we
  // get up-to-date decision state, not a stale snapshot.
  const activeCullGroup = useMemo(() => {
    const groupId = loupeGroup?.best_image_id
      ?? detailGroupContext?.best_image_id
      ?? (gridLayout === 'filmstrip' ? selectedGroupId : null)
    if (groupId == null) return null
    const item = displayGridItems.find(
      it => it.type === 'group' && it.group.best_image_id === groupId
    )
    return item?.group ?? null
  }, [loupeGroup, detailGroupContext, gridLayout, selectedGroupId, displayGridItems])

  // Check whether the given set of image ids being decided *now* resolves
  // every photo in the active culling group (i.e. all other members already
  // had decisions). When true, fires closeLoupeAndAdvance scoped to that
  // group's best_image_id so the close-and-advance path runs even if the
  // group is no longer the one selectedGroupId points to after advance.
  //
  // Exposed via a ref so earlier-declared callbacks (sendDecision /
  // sendGroupDecision / bulkDecide live above this point in the file) can
  // invoke it without taking it as a dependency and triggering an
  // initialization-before-use error.
  const maybeResolveActiveGroupRef = useRef(() => {})
  // Suppress double-fires: GroupLoupe.decideFocused already calls
  // onAllDecided synchronously when K/M/R resolves the last photo, so the
  // ref-triggered path would re-advance immediately afterward. Tracking the
  // most recently resolved group id lets the ref fire only when nothing else
  // has handled the same group's resolution this tick.
  const lastResolvedGroupRef = useRef(null)
  useEffect(() => {
    maybeResolveActiveGroupRef.current = (justDecidedIds) => {
      const group = activeCullGroup
      if (!group) return
      const decidedSet = new Set(justDecidedIds)
      const allResolved = group.images.every(
        img => decidedSet.has(img.id) || !!img.decision
      )
      if (!allResolved) return
      const targetId = group.best_image_id
      if (lastResolvedGroupRef.current === targetId) return
      // Defer one tick — sendDecision / bulkDecide have already queued their
      // own selectedIdx / selectedGroupId updates above; running advance
      // synchronously would race them. setTimeout(0) lands us after React
      // commits the pending state, then we override selection cleanly. Re-
      // check the ref inside the timer so a sync onAllDecided from
      // GroupLoupe (which also stamps) suppresses the deferred work.
      setTimeout(() => {
        if (lastResolvedGroupRef.current === targetId) return
        closeLoupeAndAdvance(targetId)
      }, 0)
    }
  }, [activeCullGroup, closeLoupeAndAdvance])
  // Clear the "last resolved" memo whenever the active culling group changes
  // — re-entering a fully-decided group (e.g. via Undo bringing it back into
  // focus) shouldn't be permanently suppressed.
  useEffect(() => {
    const id = activeCullGroup?.best_image_id
    if (id !== lastResolvedGroupRef.current) lastResolvedGroupRef.current = null
  }, [activeCullGroup])

  // ── Compare set management ────────────────────────────────────────────────
  const MAX_COMPARE = 4
  const toggleCompare = useCallback((image) => {
    setCompareImages(prev => {
      const already = prev.find(img => img.id === image.id)
      if (already) return prev.filter(img => img.id !== image.id)
      if (prev.length >= MAX_COMPARE) return prev
      return [...prev, image]
    })
  }, [])

  // ── Multi-select bulk actions ─────────────────────────────────────────────
  // Modal for "Move into group…" picker. Replaces the old click-a-GroupTile
  // flow; drag-and-drop onto a GroupTile still works for the keyboard-free
  // case (handled inside the grid render via dataTransfer payloads).
  const [moveModalOpen, setMoveModalOpen] = useState(false)

  // Right-click context menu for a single tile: { imageId, x, y } or null.
  // Multi-selection is intentionally ignored — the menu acts on the photo
  // under the cursor only, not the whole selection (matches Finder behaviour).
  const [tileMenu, setTileMenu] = useState(null)

  // Photo-onto-photo drop hover. Independent of dropHoverGroupId because a
  // photo can be hovered while a GroupTile is also under the same drag, and
  // the visuals shouldn't fight.
  const [dropHoverPhotoId, setDropHoverPhotoId] = useState(null)
  useEffect(() => {
    if (!tileMenu) return
    const handler = () => setTileMenu(null)
    document.addEventListener('mousedown', handler)
    document.addEventListener('scroll', handler, true)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('scroll', handler, true)
    }
  }, [tileMenu])

  const bulkSendDecision = useCallback(async (decision) => {
    const ids = Array.from(gridMultiSelect.selected)
    if (ids.length === 0) return
    // Snapshot prior decisions before mutating — needed for undo stack.
    const prev = new Map(
      ids.map(id => {
        const img = images.find(i => i.id === id)
        return [id, img?.decision ?? null]
      })
    )
    const results = await Promise.allSettled(ids.map(id =>
      fetch(`${API}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: id, decision }),
      }).then(async r => {
        const data = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(data.detail || `${r.status}`)
        return { id, new_path: data.new_path }
      })
    ))
    const patches = results.filter(r => r.status === 'fulfilled').map(r => r.value)
    const failures = results.filter(r => r.status === 'rejected').length
    if (patches.length > 0) {
      const patchMap = new Map(patches.map(p => [p.id, p]))
      setImages(prevImgs => prevImgs.map(img => {
        const p = patchMap.get(img.id)
        return p ? { ...img, decision, file_path: p.new_path } : img
      }))
      undoStack.push({
        kind: 'decision',
        label: `bulk ${decision}`,
        items: patches.map(p => ({
          id: p.id, prev: prev.get(p.id) ?? null, next: decision,
        })),
        timestamp: Date.now(),
      })
    }
    loadModelInfo()
    if (failures > 0) {
      addToast({
        type: 'error',
        message: `${patches.length} succeeded, ${failures} failed`,
        duration: 5000,
      })
    }
  }, [gridMultiSelect.selected, images, setImages, loadModelInfo, addToast, undoStack])

  // runUndo — pops the top of the undo stack and replays the inverse.
  // Called by U / Cmd+Z from any surface (grid, loupe, detail, compare).
  // Returns true if an entry was consumed, false if the stack was empty.
  const runUndo = useCallback(async () => {
    const entry = undoStack.pop()
    if (!entry) return false

    if (entry.kind === 'decision') {
      // Replay each per-photo decision via /decision (or /undo-decision
      // when prev is null). One round-trip per photo; the failure mode is
      // the same as bulk K/M/R so we report partial success the same way.
      const results = await Promise.allSettled(entry.items.map(item => {
        if (item.prev === null) {
          return fetch(`${API}/undo-decision`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_id: item.id }),
          }).then(async r => {
            const data = await r.json().catch(() => ({}))
            if (!r.ok) throw new Error(data.detail || `${r.status}`)
            return { id: item.id, decision: null, new_path: data.new_path }
          })
        }
        return fetch(`${API}/decision`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_id: item.id, decision: item.prev }),
        }).then(async r => {
          const data = await r.json().catch(() => ({}))
          if (!r.ok) throw new Error(data.detail || `${r.status}`)
          return { id: item.id, decision: item.prev, new_path: data.new_path }
        })
      }))
      const patches = results.filter(r => r.status === 'fulfilled').map(r => r.value)
      if (patches.length > 0) {
        const patchMap = new Map(patches.map(p => [p.id, p]))
        setImages(prev => prev.map(img => {
          const p = patchMap.get(img.id)
          if (!p) return img
          return { ...img, decision: p.decision, file_path: p.new_path }
        }))
      }
      loadModelInfo()
      return true
    }

    if (entry.kind === 'group') {
      const ok = await setManualGroup({
        mode: 'restore_assignments',
        assignments: entry.assignments,
      })
      if (ok !== false) {
        // Local images patch — restore the manual_group_id field so the
        // next bulk action snapshots the right state without waiting for
        // /images to refresh.
        setImages(prev => prev.map(img => {
          const a = entry.assignments.find(x => x.image_id === img.id)
          return a ? { ...img, manual_group_id: a.manual_group_id } : img
        }))
        addToast({
          type: 'info',
          message: `Undid group action (${entry.label})`,
          duration: 3000,
        })
      }
      return true
    }

    return false
  }, [undoStack, setImages, loadModelInfo, addToast, setManualGroup])

  // amendLastDecision — "double-press" gesture for changing the previous photo's
  // decision without navigating back. When the previous K/M/R happened within
  // AMEND_WINDOW_MS (measured from the *keypress*, not the network round-trip),
  // re-apply the new decision to those exact image ids. The original `prev` is
  // preserved so a later Undo still rewinds to the pre-decision state. The
  // current photo / selection is untouched; the user just keeps moving.
  //
  // Race-safe: registerDecisionIntent() is called synchronously from every
  // K/M/R handler BEFORE the network call. amendLastDecision compares against
  // that intent timestamp (not the undo-stack entry, which lands ~200-500ms
  // later when the /decision response arrives). If the previous decision is
  // still in flight when the amend fires, we wait briefly for it to land on
  // the stack — without this, the amend can't read the correct items[] ids.
  //
  // Returns true if the amend was applied (caller should *not* run its normal
  // decision handler). Returns false when the window expired, no recent intent
  // exists, or the in-flight decision never landed.
  const AMEND_WINDOW_MS = 200
  const lastDecisionIntentRef = useRef(null)  // { timestamp } — set on every K/M/R press

  const registerDecisionIntent = useCallback(() => {
    lastDecisionIntentRef.current = { timestamp: Date.now() }
  }, [])

  const amendLastDecision = useCallback(async (decision) => {
    const intent = lastDecisionIntentRef.current
    if (!intent) return false
    if (Date.now() - intent.timestamp > AMEND_WINDOW_MS) return false

    // Wait for the in-flight decision to land on the undo stack. Each /decision
    // typically lands in <500ms; we poll briefly. This is the critical bit —
    // without it, peek() returns the previous unrelated entry (or null) when
    // K is pressed before R's stack push completes.
    const deadline = Date.now() + 800
    let top = undoStack.peek()
    while ((!top || typeof top.timestamp !== 'number' || top.timestamp < intent.timestamp)
      && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 25))
      top = undoStack.peek()
    }

    if (!top || top.kind !== 'decision') return false
    if (typeof top.timestamp !== 'number') return false
    if (top.timestamp < intent.timestamp) return false  // never landed

    const items = top.items || []
    if (items.length === 0) return false

    // Consume the intent so a third press doesn't double-amend.
    lastDecisionIntentRef.current = null

    // Re-apply per-photo via /decision (parallel, like bulkSendDecision). We
    // intentionally don't go through /bulk-decision because we need the
    // returned new_path for each photo to patch state without a full reload.
    const results = await Promise.allSettled(items.map(item =>
      fetch(`${API}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: item.id, decision }),
      }).then(async r => {
        const data = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(data.detail || `${r.status}`)
        return { id: item.id, new_path: data.new_path }
      })
    ))
    const patches = results.filter(r => r.status === 'fulfilled').map(r => r.value)
    if (patches.length === 0) {
      addToast({ type: 'error', message: 'Could not change previous decision', duration: 4000 })
      return true  // still consume the keypress — don't fall through and re-cull current
    }

    const patchMap = new Map(patches.map(p => [p.id, p]))
    setImages(prev => prev.map(img => {
      const p = patchMap.get(img.id)
      return p ? { ...img, decision, file_path: p.new_path } : img
    }))

    // Replace the stale entry: keep each item's *original* prev so Undo still
    // rewinds to the pre-decision state, not the intermediate one.
    undoStack.pop()
    undoStack.push({
      kind: 'decision',
      label: items.length === 1 ? decision : `bulk ${decision}`,
      items: items.map(item => ({ id: item.id, prev: item.prev, next: decision })),
      timestamp: Date.now(),
    })

    const word = decision === 'keep' ? 'Keep' : decision === 'maybe' ? 'Maybe' : 'Reject'
    const n = patches.length
    addToast({
      type: 'info',
      message: n === 1
        ? `Changed previous photo to ${word}`
        : `Changed previous ${n} photos to ${word}`,
      duration: 2500,
    })

    loadModelInfo()
    return true
  }, [undoStack, setImages, addToast, loadModelInfo])

  const bulkCompareFromSelection = useCallback(() => {
    const ids = Array.from(gridMultiSelect.selected)
    if (ids.length < 2 || ids.length > MAX_COMPARE) return
    const picks = ids.map(id => images.find(i => i.id === id)).filter(Boolean)
    if (picks.length < 2) return
    setCompareImages(picks)
    setCompareOpen(true)
  }, [gridMultiSelect.selected, images])

  const revealInFinder = useCallback(async (imageId) => {
    try {
      const res = await fetch(`${API}/reveal-in-finder`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        addToast({ type: 'error', message: data.detail || `Reveal failed (${res.status})`, duration: 4000 })
      }
    } catch (err) {
      addToast({ type: 'error', message: `Reveal failed: ${err.message}`, duration: 4000 })
    }
  }, [addToast])

  // ── Clear all analysis ────────────────────────────────────────────────────
  const resetDashboard = useCallback(async () => {
    try {
      const res = await fetch(`${API}/dashboard/reset`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      addToast({
        type: 'success',
        message: `Dashboard reset — cleared ${data.shooting_removed} shooting-log entries`,
      })
    } catch (err) {
      addToast({ type: 'error', message: `Reset failed: ${err.message}`, duration: 8000 })
    }
  }, [addToast])

  const resetPersonalModel = useCallback(async () => {
    try {
      const res = await fetch(`${API}/reset-personal-model`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      loadModelInfo()
      await loadImages().catch(() => {})
      localStorage.removeItem('pca.personalModelBannerDismissed')
      setBannerDismissedAtTier(null)
      addToast({
        type: 'success',
        message: `Personal model reset — cleared ${data.samples_removed} training samples and ${data.pairwise_removed} pairwise comparisons`,
      })
    } catch (err) {
      addToast({ type: 'error', message: `Reset failed: ${err.message}`, duration: 8000 })
    }
  }, [loadModelInfo, loadImages, addToast, setBannerDismissedAtTier])

  const clearAnalysis = useCallback(async () => {
    if (analyzing) return
    try {
      try { await fetch(`${API}/watch`, { method: 'DELETE' }) } catch {}
      const res = await fetch(`${API}/clear`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      const fresh = makeNewTab()
      setTabs([fresh])
      setActiveTabId(fresh.id)
      setSettingsOpen(false)
      addToast({ type: 'success', message: `Cleared ${data.images_removed} images and ${data.previews_removed} previews` })
    } catch (err) {
      addToast({ type: 'error', message: `Clear failed: ${err.message}`, duration: 8000 })
    }
  }, [analyzing, addToast, setTabs, setActiveTabId])

  // ── AutoCull handler ──────────────────────────────────────────────────────
  const handleAutoCullComplete = useCallback(async (result) => {
    setAutoCullOpen(false)
    await loadImages()
    loadModelInfo()
    setDecisionFilter('maybe')
    addToast({ type: 'info', message: `Auto-culled ${result.total} photos — ${result.counts?.maybe ?? 0} maybes to review` })
  }, [loadImages, loadModelInfo, addToast])

  // ── Bulk actions ──────────────────────────────────────────────────────────
  const rejectAllMaybes = useCallback(async () => {
    setBulkRunning(true)
    const ids = images.filter(img => img.decision === 'maybe').map(img => img.id)
    try {
      await fetch(`${API}/bulk-decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids, decision: 'reject' }),
      })
      await loadImages()
      loadModelInfo()
      setDecisionFilter('reject')
      addToast({ type: 'info', message: `${ids.length} photos moved to _Trash` })
    } catch (e) {
      console.error('Bulk reject failed:', e)
    } finally {
      setBulkRunning(false)
      setBulkConfirm(null)
    }
  }, [images, loadImages, loadModelInfo, addToast])

  const trashAllRejects = useCallback(async () => {
    setBulkRunning(true)
    try {
      const res = await fetch(`${API}/trash-rejects`, { method: 'POST' })
      const data = await res.json()
      await loadImages()
      setDecisionFilter(null)
      addToast({ type: 'info', message: `${data.trashed} file${data.trashed !== 1 ? 's' : ''} sent to Trash` })
    } catch (e) {
      console.error('Trash rejects failed:', e)
    } finally {
      setBulkRunning(false)
      setBulkConfirm(null)
    }
  }, [loadImages, addToast])

  // ── Keyboard shortcuts (useKeyboard) ──────────────────────────────────────
  const cols = useCallback(() => {
    if (!gridRef.current) return 1
    return getComputedStyle(gridRef.current).gridTemplateColumns.split(' ').length
  }, [])

  useKeyboard({
    activeView,
    detailOpen,
    setDetailOpen,
    images,
    selectedIdx,
    setSelectedIdx,
    selectedGroupId,
    setSelectedGroupId,
    // Walk the *visible* list — filters/search/decision pills change what the
    // user sees, and arrow nav has to match that or it feels broken.
    displayGridItems,
    enterLoupe,
    sendDecision,
    setUserCols,
    undoImage,
    addToast,
    cols,
    // Composite-widget gate: grid hotkeys (arrows, K/M/R, Space, Enter) only
    // fire when DOM focus is inside the grid. Tabbing out to settings/search
    // hands keyboard control off to that surface; clicking a photo or group
    // re-engages grid mode.
    gridRef,
    // Loupe locks out grid hotkeys — its own useHotkeys (in GroupLoupe.jsx)
    // owns K/M/X/arrows/Enter/S/Z/Esc while open.
    loupeOpen: !!loupeGroup,
    // Bulk multi-select hooks — when isSelectMode && size>0, K/M/R hit the
    // whole selection and U undoes the whole bulk action. Otherwise the
    // per-photo handlers fire as before.
    multiSelectActive: gridMultiSelect.isSelectMode && gridMultiSelect.size > 0,
    bulkSendDecision,
    runUndo,
    amendLastDecision,
    registerDecisionIntent,
  })

  // ── Render: loading ───────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen bg-[#07080a] flex items-center justify-center">
        <div className="flex items-center gap-3 text-[#9c9c9d]">
          <Spinner />
          <span className="text-sm">Loading…</span>
        </div>
      </div>
    )
  }

  // ── Render: backend down ──────────────────────────────────────────────────
  if (error) {
    return (
      <div className="min-h-screen bg-[#07080a] flex items-center justify-center p-8">
        <div className="max-w-sm w-full text-center space-y-4">
          <div className="w-12 h-12 rounded-full bg-[rgba(201,123,123,0.12)] border border-[rgba(201,123,123,0.30)] flex items-center justify-center mx-auto">
            <span className="text-[#C97B7B] text-xl font-bold">!</span>
          </div>
          <p className="text-sm font-medium text-[#f9f9f9]">Cannot connect to backend</p>
          <p className="text-xs text-[#cecece]">{error}</p>
          <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-lg p-3 text-left space-y-1">
            <p className="text-xs text-[#cecece]">To start the backend:</p>
            <code className="text-xs font-mono text-[#f0f0f0] block">cd kamera</code>
            <code className="text-xs font-mono text-[#f0f0f0] block">./start.sh</code>
          </div>
          <button
            onClick={() => window.location.reload()}
            className={BTN_SECONDARY}
          >
            <RotateCcw size={16} /> Retry
          </button>
        </div>
      </div>
    )
  }

  const selected = images.length > 0 ? images[selectedIdx] : null
  const undecidedCount = images.filter(img => img.analysis_status === 'done' && !img.decision).length

  return (
    <div className="min-h-screen bg-[#07080a]">

      <DownloadToast models={modelStatus.models.filter(m => m.state === 'downloading')} />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      {shortcutsOpen && <ShortcutsModal onClose={() => setShortcutsOpen(false)} />}
      {bannerStatesOpen && <BannerStates onClose={() => setBannerStatesOpen(false)} />}

      {tileMenu && createPortal(
        <div
          data-dropdown="true"
          className="fixed z-[70] min-w-[180px] rounded-md border border-[rgba(255,255,255,0.10)] bg-[#161718] shadow-xl py-1"
          style={{ left: tileMenu.x, top: tileMenu.y }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              revealInFinder(tileMenu.imageId)
              setTileMenu(null)
            }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-[#cecece] hover:bg-[rgba(255,255,255,0.05)] text-left"
          >
            <ExternalLink size={14} /> Reveal in Finder
          </button>
        </div>,
        document.body,
      )}

      {moveModalOpen && (
        <MoveIntoGroupModal
          groups={enrichedGroups}
          selectionCount={gridMultiSelect.size}
          onPick={(g) => {
            const ids = Array.from(gridMultiSelect.selected)
            if (ids.length === 0) return
            setManualGroup({
              image_ids: ids,
              mode: 'join_group',
              target_image_id: g.best_image_id,
            })
            setMoveModalOpen(false)
            gridMultiSelect.exit()
          }}
          onClose={() => setMoveModalOpen(false)}
        />
      )}

      {settingsOpen && (
        <SettingsModal
          settings={settings}
          onSave={async () => { await loadSettings(); await loadImages().catch(() => {}) }}
          onClose={() => setSettingsOpen(false)}
          onToast={addToast}
          onClear={images.length > 0 ? clearAnalysis : null}
          clearing={analyzing}
          onResetModel={resetPersonalModel}
          onResetDashboard={resetDashboard}
          autoGenerate={autoGenerate}
          onAutoGenerateChange={v => {
            setAutoGenerate(v)
            localStorage.setItem('pca.autoGenerateExplanation', String(v))
          }}
          modelInfo={modelInfo}
          onTrain={trainModel}
          training={trainingModel}
          onStartTraining={enterTrainingMode}
          undecidedCount={undecidedCount}
          bannerDismissed={bannerDismissed}
          onSetBannerDismissed={(hide) => {
            if (hide) {
              const tier = currentTierKey ?? 'unknown'
              localStorage.setItem('pca.personalModelBannerDismissed', tier)
              setBannerDismissedAtTier(tier)
            } else {
              localStorage.removeItem('pca.personalModelBannerDismissed')
              setBannerDismissedAtTier(null)
            }
          }}
          uiScale={uiScale}
          onUiScaleChange={setUiScale}
          advanceDir={advanceDir}
          onAdvanceDirChange={setAdvanceDir}
          groupThreshold={threshold}
          setGroupThreshold={setThreshold}
          groupTimeGapSeconds={timeGapSeconds}
          setGroupTimeGapSeconds={setTimeGapSeconds}
        />
      )}

      {autoCullOpen && (
        <AutoCullModal
          folderPath={activeTab?.folderPath || ''}
          onClose={() => setAutoCullOpen(false)}
          onComplete={handleAutoCullComplete}
          onToast={addToast}
        />
      )}

      {closeTabRequest && (() => {
        const tab = tabs.find(t => t.id === closeTabRequest.tabId)
        const name = tab ? tabLabel(tab) : 'this tab'
        const hasData = (tab?.images?.length ?? 0) > 0
        const liveSuffix = tab?.watchLive ? ' Watch live will stop.' : ''
        const body = hasData ? (
          <>
            This removes its analysis data from the database ({tab.images.length} photo{tab.images.length === 1 ? '' : 's'}).
            Files already moved to your{' '}
            <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
            <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
            <DecisionWord kind="reject">Reject</DecisionWord>
            {' '}folders are unaffected.{liveSuffix}
          </>
        ) : `This tab has no analyzed photos. Close it?${liveSuffix}`
        return (
          <ConfirmModal
            title={`Close "${name}"?`}
            body={body}
            confirmLabel="Close tab"
            confirmTone="danger"
            onCancel={() => setCloseTabRequest(null)}
            onConfirm={confirmCloseTab}
          />
        )
      })()}

      {overwriteRequest && (
        <ConfirmModal
          title="Re-analyze this folder?"
          body={
            <>
              "{overwriteRequest.folderPath}" is already open in another tab. Re-analyzing replaces all of that tab's analysis data.
              Files already moved to your{' '}
              <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
              <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
              <DecisionWord kind="reject">Reject</DecisionWord>
              {' '}folders are unaffected.
            </>
          }
          confirmLabel="Replace and re-analyze"
          confirmTone="danger"
          onCancel={() => setOverwriteRequest(null)}
          onConfirm={confirmOverwrite}
        />
      )}

      {bannerDismissConfirm && (
        <ConfirmModal
          title="Hide the personal model banner?"
          body="No problem — your decisions are still saved as training data in the background. When you want to train or check the model later, open Settings → Model. You can also bring this banner back from there."
          confirmLabel="Hide banner"
          confirmTone="info"
          onCancel={() => setBannerDismissConfirm(false)}
          onConfirm={() => {
            const tier = currentTierKey ?? 'unknown'
            localStorage.setItem('pca.personalModelBannerDismissed', tier)
            setBannerDismissedAtTier(tier)
            setBannerDismissConfirm(false)
          }}
        />
      )}

      {busyRequest && (() => {
        const tab = tabs.find(t => t.id === busyRequest.tabId)
        const name = tab ? tabLabel(tab) : 'another tab'
        return (
          <ConfirmModal
            title="Another analysis is in progress"
            body={`"${name}" is currently being analyzed. Wait for it to finish — or open that tab and click Stop — before starting a new analysis.`}
            confirmLabel="Switch to that tab"
            confirmTone="info"
            onCancel={() => setBusyRequest(null)}
            onConfirm={() => {
              setActiveTabId(busyRequest.tabId)
              setBusyRequest(null)
            }}
          />
        )
      })()}

      {/* Subfolder picker — surfaced when /has-subfolders detects nested
          photo directories. Two equal-weight choices (no Cancel button as the
          primary; the × dismisses) so the user picks consciously instead of
          falling into a default. */}
      {subfolderRequest && (
        <div className="fixed inset-0 z-[60] bg-[rgba(0,0,0,0.6)] flex items-center justify-center p-6" onClick={dismissSubfolderRequest}>
          <div
            className="bg-[#161718] border border-[#2a2b2d] rounded-xl shadow-2xl max-w-md w-full p-6 space-y-4"
            onClick={e => e.stopPropagation()}
          >
            <div className="space-y-1">
              <p className="text-base font-medium text-[#f0f0f0]">This folder has subfolders</p>
              <p className="text-sm text-[#9c9c9d] leading-relaxed">
                Found <strong className="text-[#cecece]">{subfolderRequest.count}</strong> subfolder{subfolderRequest.count === 1 ? '' : 's'} containing photos. Include them in the analysis?
              </p>
            </div>
            <div className="space-y-2 text-xs text-[#9c9c9d] leading-relaxed">
              <p>
                <strong className="text-[#cecece]">Root only</strong> — analyse just the chosen folder. Subfolders are skipped.
              </p>
              <p>
                <strong className="text-[#cecece]">Include subfolders</strong> — walk the tree. K/M/R destinations live inside each photo's own subfolder so the structure is preserved.
              </p>
            </div>
            <div className="flex flex-col gap-2 pt-1">
              <button
                onClick={() => resolveSubfolderRequest(true)}
                className="w-full inline-flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-sm font-medium bg-[rgba(91,184,212,0.15)] hover:opacity-70 text-[#5BB8D4] border border-[rgba(91,184,212,0.40)] transition-opacity"
              >
                <FolderTree size={16} /> Include subfolders
              </button>
              <button
                onClick={() => resolveSubfolderRequest(false)}
                className="w-full inline-flex items-center justify-center gap-2 py-2 px-3 rounded-lg text-sm text-[#cecece] border border-[rgba(255,255,255,0.10)] hover:opacity-70 transition-opacity"
              >
                <Folder size={16} /> Root folder only
              </button>
              <button
                onClick={dismissSubfolderRequest}
                className="w-full inline-flex items-center justify-center gap-1.5 py-1 px-3 text-xs text-[#9c9c9d] hover:text-[#cecece] transition-colors"
              >
                <X size={14} /> Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {(() => {
        // Sticky-filmstrip mode: when the user picks "Filmstrip" in the View
        // pill, DetailView mounts unconditionally for the active tab and
        // closing it switches the layout back to Grid (rather than just
        // hiding the panel). Otherwise it follows the explicit detailOpen
        // flag (opened by single-click / Enter from the grid or loupe).
        const stickyFilmstrip = !detailOpen
          && activeView === 'grid'
          && gridLayout === 'filmstrip'
          && images.length > 0
          && !loupeGroup
          && !compareOpen
        if (!detailOpen && !stickyFilmstrip) return null
        return (() => {
        // Three navigation modes:
        //   · group-loupe mode (detailGroupContext set): prev/next cycle
        //     within the group; filmstrip shows group members; closing
        //     reopens GroupLoupe (we never went back to the grid).
        //   · grid-filmstrip mode (no group context): prev/next walks
        //     displayGridItems atomically — groups count as one stop. The
        //     focused item can be a solo photo or a group; when it's a
        //     group, the panel renders the hero.
        const inGroup = !!detailGroupContext
        const groupImages = detailGroupContext?.images || []

        // Resolve the *effective* image the panel renders. In grid-filmstrip
        // mode, a group-focused state renders the group's AI hero.
        let panelImage = selected
        let filmstripIndex = -1
        let groupFocused = false
        let focusedGroup = null
        if (!inGroup) {
          // selectedGroupId stores the group's best_image_id (not group.id);
          // see App's selectedGridIdx memo for the same lookup.
          if (selectedGroupId != null) {
            const item = displayGridItems.find(it => it.type === 'group' && it.group.best_image_id === selectedGroupId)
            const group = item?.group
            const hero = group?.images.find(i => i.id === group.best_image_id) || group?.images[0]
            if (hero) panelImage = images.find(img => img.id === hero.id) || hero
            filmstripIndex = item ? displayGridItems.indexOf(item) : -1
            groupFocused = !!item
            focusedGroup = group || null
          } else if (selected) {
            filmstripIndex = displayGridItems.findIndex(it => it.type === 'image' && it.image.id === selected.id)
          }
        }
        if (!panelImage) return null

        const groupIdx = inGroup ? groupImages.findIndex(img => img.id === panelImage.id) : -1

        // ── Atomic step over displayGridItems for grid-filmstrip mode ──
        const stepFilmstrip = (delta) => {
          const next = displayGridItems[filmstripIndex + delta]
          if (!next) return
          if (next.type === 'image') {
            const i = images.findIndex(img => img.id === next.image.id)
            if (i >= 0) { setSelectedGroupId(null); setSelectedIdx(i) }
          } else {
            setSelectedGroupId(next.group.best_image_id)
          }
        }

        const onPrev = inGroup
          ? (groupIdx > 0 ? () => {
              const prev = groupImages[groupIdx - 1]
              const i = images.findIndex(img => img.id === prev.id)
              if (i >= 0) setSelectedIdx(i)
            } : null)
          : (filmstripIndex > 0 ? () => stepFilmstrip(-1) : null)
        const onNext = inGroup
          ? (groupIdx >= 0 && groupIdx < groupImages.length - 1 ? () => {
              const next = groupImages[groupIdx + 1]
              const i = images.findIndex(img => img.id === next.id)
              if (i >= 0) setSelectedIdx(i)
            } : null)
          : (filmstripIndex >= 0 && filmstripIndex < displayGridItems.length - 1 ? () => stepFilmstrip(1) : null)

        const pickFilmstripIndex = (idx) => {
          const item = displayGridItems[idx]
          if (!item) return
          if (item.type === 'image') {
            const i = images.findIndex(img => img.id === item.image.id)
            if (i >= 0) { setSelectedGroupId(null); setSelectedIdx(i) }
          } else {
            setSelectedGroupId(item.group.best_image_id)
          }
        }

        const openGroupFromFilmstrip = (group) => {
          // Close DetailView, focus the group, open GroupLoupe. The group's
          // best_image_id doubles as the focus key everywhere.
          setDetailOpen(false)
          setSelectedGroupId(group.best_image_id)
          enterLoupe?.(group.best_image_id)
        }

        return (
        <DetailView
          key={panelImage.id}
          image={panelImage}
          modelInfo={modelInfo}
          onClose={() => {
            // In sticky-filmstrip mode there's no explicit detailOpen flag to
            // flip; closing means "go back to thumbnail grid".
            if (stickyFilmstrip) { setGridLayout('grid'); setDetailGroupContext(null); return }
            setDetailOpen(false); setDetailGroupContext(null)
          }}
          onDecide={sendDecision}
          onUndoImage={undoImage}
          onUndo={runUndo}
          onAmend={amendLastDecision}
          onRegisterDecisionIntent={registerDecisionIntent}
          onPrev={onPrev}
          onNext={onNext}
          hasPrev={!!onPrev}
          hasNext={!!onNext}
          autoGenerate={autoGenerate}
          onExplanationGenerated={(id, text) =>
            setImages(prev => prev.map(img => img.id === id ? { ...img, explanation: text } : img))
          }
          groupContext={detailGroupContext}
          onPickGroupMember={inGroup ? (id) => {
            const i = images.findIndex(img => img.id === id)
            if (i >= 0) setSelectedIdx(i)
          } : null}
          gridFilmstrip={inGroup ? null : {
            items: displayGridItems,
            focusedIndex: filmstripIndex,
            onPickIndex: pickFilmstripIndex,
            onOpenGroup: openGroupFromFilmstrip,
            groupFocused,
            group: focusedGroup,
            thumbSize: stripThumb,
          }}
          onBulk={bulkDecide}
          addToast={addToast}
          filmstripCollapsed={detailFilmstripCollapsed}
          onToggleFilmstripCollapsed={onToggleFilmstripCollapsed}
          setFilmstripCollapsed={setDetailFilmstripCollapsed}
          onStartStripResize={startStripResize}
          setBottomToolbarSlot={setBottomToolbarSlot}
          suppressPanelResize={sortOpen || filterOpen || viewOpen || tabSettingsOpen}
        />
        )
        })()
      })()}

      {/* GroupLoupe overlay — opens when the user clicks a GroupTile in the
          grid. Owns its own hotkeys (K/M/X/arrows/Enter/S/Z/Esc) via
          react-hotkeys-hook so it never fights the grid's keyboard wiring. */}
      {loupeGroup && !detailOpen && (
        <GroupLoupe
          group={loupeGroup}
          onClose={exitLoupe}
          onAllDecided={closeLoupeAndAdvance}
          onDecide={sendGroupDecision}
          onUndoImage={undoImage}
          onUndo={runUndo}
          onAmend={amendLastDecision}
          onRegisterDecisionIntent={registerDecisionIntent}
          onBulk={bulkDecide}
          onOpenDetail={openDetailFromLoupe}
          setLoupeAnchorId={setLoupeAnchorId}
          groupMode={groupMode}
          sortField={sortField}
          sortDir={sortDir}
          modelInfo={modelInfo}
          allGroups={sortedRailGroups}
          onSelectGroup={(groupId) => enterLoupe(groupId)}
          onSetManualGroup={setManualGroup}
          onSetGroupHero={setGroupHero}
          onRankComplete={loadGroups}
        />
      )}

      {compareOpen && compareImages.length >= 2 && (
        <CompareView
          images={compareImages}
          onClose={() => { setCompareOpen(false); setCompareImages([]) }}
          onDecide={sendGroupDecision}
          onBulkDecide={bulkDecide}
          onUndoImage={undoImage}
          onUndo={runUndo}
          addToast={addToast}
          modelInfo={modelInfo}
        />
      )}

      {/* ── Sticky header (App bar + TabBar + Band 2 + filter bar) ──
          Sits at z-40 so modals/overlays at z-50 cover it cleanly. The
          scroll-to-hide affordance is suspended whenever multi-select is
          active so the selection toolbar (which lives in the filter row)
          stays reachable without scrolling back up. */}
      <div
        className="sticky top-0 z-40 bg-[#101111] transition-transform duration-200 ease-out will-change-transform"
        style={{ transform: (headerHidden && !gridMultiSelect.isSelectMode) ? 'translateY(-100%)' : 'translateY(0)' }}
      >

        {/* Band 1 — Unified app bar + tabs */}
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[#1e1f21]">
          {/* Logo */}
          <div className="flex items-center gap-2.5 flex-shrink-0">
            {/* Card-stack logo — matches app icon. Theme-aware via CSS vars in index.css */}
            <svg width="40" height="40" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" className="app-logo">
              <rect x="338" y="451" width="492" height="369" rx="51" fill="var(--logo-red)"/>
              <rect x="246" y="328" width="492" height="369" rx="51" fill="var(--logo-yellow)"/>
              <rect x="154" y="205" width="492" height="369" rx="51" fill="var(--logo-green)"/>
            </svg>
            <span className="font-semibold text-[#f0f0f0] leading-none" style={{ fontSize: '28px' }}>KaMeRa</span>
          </div>

          {/* Separator */}
          <div className="w-px h-4 bg-[rgba(255,255,255,0.08)] flex-shrink-0" />

          {/* Tabs inline — flex-1 fills remaining space */}
          <TabBar
            tabs={tabs}
            activeTabId={activeTabId}
            onSelect={(id) => {
              setActiveTabId(id)
              if (activeView !== 'grid') setActiveView('grid')
            }}
            onClose={requestCloseTab}
            onReorder={reorderTabs}
            onTrailingClick={(tabId) => {
              if (autoOpenFinder) {
                startNewAnalysis(tabId)
              } else {
                setActiveTabId(tabId)
              }
            }}
          />

          {/* Right actions */}
          <div className="flex items-center gap-0.5 flex-shrink-0">
            <button
              onClick={() => setActiveView(activeView === 'dashboard' ? 'grid' : 'dashboard')}
              className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs transition-opacity border whitespace-nowrap ${
                activeView === 'dashboard'
                  ? 'bg-[#1a1b1d] text-[#f0f0f0] border-[#2a2b2d]'
                  : 'text-[#cecece] border-transparent hover:opacity-70'
              }`}
              title="Dashboard — persistent stats across all sessions"
            >
              <LayoutDashboard size={15} /> Dashboard
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              className={BTN_ICON}
              title="Model settings"
              aria-label="Model settings"
            >
              <SettingsIcon size={18} />
            </button>
            <button
              onClick={() => setShortcutsOpen(true)}
              className={BTN_ICON}
              title="Help & keyboard shortcuts"
              aria-label="Help"
            >
              <HelpCircle size={18} />
            </button>
            <button
              onClick={() => window.open(FEEDBACK_URL, '_blank', 'noopener,noreferrer')}
              className={BTN_ICON}
              title="Send feedback"
              aria-label="Send feedback"
            >
              <MessageSquare size={18} />
            </button>
            <ViewModePill mode="desktop" compact />

            {/* Quit button — only shown when running via the launcher (port 8000).
                Hidden in dev mode (port 5173) where the terminal is already visible. */}
            {window.location.port === '8000' && (
              <button
                onClick={quitApp}
                disabled={quitting}
                className={`${BTN_ICON} hover:text-[#C97B7B] disabled:opacity-40`}
                title="Quit KaMeRa"
                aria-label="Quit"
              >
                <Power size={18} />
              </button>
            )}
          </div>
        </div>

        {/* Resume banner — surfaces when a prior batch left the folder
            with un-analyzed photos (laptop closed mid-batch, app crash,
            force-quit). Backend reconciles by walking the disk + the
            DB's analysis_status='done' set; if the delta is > 0 we show
            a one-click resume. analyze_folder already skips done files,
            so resume == "re-POST analyze," nothing fancier. Hidden while
            analyzing so the progress bar takes precedence. */}
        {!analyzing
          && activeTab
          && activeTab.status === 'ready'
          && activeTab.folderPath
          && (activeTab.unfinishedCount ?? 0) > 0 && (
          <div className="px-6 pt-3">
            <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-[rgba(232,184,74,0.10)] border border-[rgba(232,184,74,0.30)]">
              <div className="text-xs text-[#E8B84A]">
                <span className="font-semibold">{activeTab.unfinishedCount}</span> photo{activeTab.unfinishedCount === 1 ? '' : 's'} in this folder {activeTab.unfinishedCount === 1 ? 'is' : 'are'} not yet analyzed — a prior run was interrupted before finishing.
              </div>
              <button
                type="button"
                onClick={() => runAnalysisForTab(activeTab.id, activeTab.folderPath, activeTab.includeSubfolders)}
                className="px-2.5 py-1 rounded-md text-xs font-medium bg-[rgba(232,184,74,0.20)] text-[#E8B84A] border border-[rgba(232,184,74,0.40)] hover:opacity-80 transition-opacity whitespace-nowrap"
                title="Re-run analysis on this folder. Already-analyzed photos skip; only the unfinished ones get processed."
              >
                Resume analysis
              </button>
            </div>
          </div>
        )}

        {/* Band 2 — Contextual toolbar */}
        {((analyzing && progress && progress.total > 0)
          || (!analyzing && analyzeResult && !analyzeResult.error && !resultDismissed)
          || (analyzeResult?.error && !resultDismissed)
        ) && (
        <div className="px-6 py-3 space-y-2">

          {/* Progress bar + Stop button */}
          {analyzing && progress && progress.total > 0 && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs font-mono text-[#8a8a8a]">
                <span className="truncate">
                  {progress.current_file
                    ? <span className="text-[#f0f0f0]">{progress.current_file}</span>
                    : 'Starting…'}
                </span>
                <span className="flex items-center gap-3 text-[#9c9c9d]">
                  {formatEta(progress.eta_seconds) && (
                    <span>{formatEta(progress.eta_seconds)} remaining</span>
                  )}
                  <span className="text-[#8a8a8a]">{progress.pct}% &nbsp;{progress.done}/{progress.total}</span>
                  <button
                    onClick={stopAnalysis}
                    disabled={stopping}
                    className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[#C97B7B] border border-[rgba(201,123,123,0.40)] bg-transparent hover:opacity-70 transition-opacity disabled:opacity-60 disabled:cursor-default"
                    title={stopping ? "Finishing the current photo…" : "Stop analysis"}
                  >
                    <Square size={12} fill="currentColor" strokeWidth={0} />
                    {stopping ? 'Stopping…' : 'Stop'}
                  </button>
                </span>
              </div>
              <div className="w-full bg-[#1b1c1e] rounded-[3px] h-1.5">
                <div
                  className="bg-[#5BB8D4] h-1.5 rounded-[3px] transition-all duration-300"
                  style={{ width: `${progress.pct}%` }}
                />
              </div>
              <p className="text-[10px] text-[#9c9c9d]">
                {stopping
                  ? 'Stopping after the current photo finishes decoding…'
                  : 'Do not close this window while analysis is running.'}
              </p>
            </div>
          )}

          {/* Batch finished banner */}
          {!analyzing && analyzeResult && !analyzeResult.error && !resultDismissed && (() => {
            const skippedFiles  = Array.isArray(analyzeResult.skipped) ? analyzeResult.skipped : []
            const skippedCount  = Array.isArray(analyzeResult.skipped) ? analyzeResult.skipped.length : (analyzeResult.skipped ?? 0)
            const errorCount    = analyzeResult.errors?.length ?? 0
            const analyzedCount = analyzeResult.analyzed ?? 0
            const elapsedLabel  = formatDuration(analyzeResult.elapsed_seconds)
            return (
            <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-lg px-3 py-2">
              <div className="flex items-center gap-4">
                <span className="flex-1 flex items-center gap-3 text-xs font-mono">
                  <span className="flex items-center gap-1.5 text-[#7DB89A]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#7DB89A]" />
                    {analyzedCount} analyzed
                  </span>

                  {elapsedLabel && (
                    <span className="flex items-center gap-1.5 text-[#9c9c9d]" title="Total analysis time">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4]" />
                      {elapsedLabel}
                    </span>
                  )}

                  {skippedCount > 0 ? (
                    <button
                      onClick={() => setSkippedExpanded(e => !e)}
                      className="flex items-center gap-1.5 text-[#E8B84A] hover:opacity-70 transition-opacity"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-[#E8B84A]" />
                      {skippedCount} skipped
                      {skippedExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                    </button>
                  ) : null}

                  {errorCount > 0 && (
                    <button
                      onClick={() => setErrorsExpanded(e => !e)}
                      className="flex items-center gap-1.5 text-[#C97B7B] hover:opacity-70 transition-opacity"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-[#C97B7B]" />
                      {errorCount} error{errorCount !== 1 ? 's' : ''}
                      {errorsExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                    </button>
                  )}
                </span>
                <button
                  onClick={() => setResultDismissed(true)}
                  className="text-[#6a6b6c] hover:opacity-70 transition-opacity"
                  aria-label="Dismiss"
                >
                  <X size={16} />
                </button>
              </div>

              {skippedExpanded && skippedFiles.length > 0 && (
                <div className="mt-2 pt-2 border-t border-[rgba(255,255,255,0.05)] space-y-1 max-h-32 overflow-y-auto">
                  {skippedFiles.map((name, i) => (
                    <p key={i} className="text-xs font-mono">
                      <span className="text-[#8a8a8a]">{name}</span>
                      <span className="text-[#4a4a4a] mx-1">—</span>
                      <span className="text-[#9c9c9d]">already analyzed</span>
                    </p>
                  ))}
                  <p className="text-[10px] text-[#9c9c9d] pt-1">
                    Use "Clear analysis" in Settings to re-run these.
                  </p>
                </div>
              )}

              {errorsExpanded && errorCount > 0 && (
                <div className="mt-2 pt-2 border-t border-[rgba(255,255,255,0.05)] space-y-1 max-h-32 overflow-y-auto">
                  {analyzeResult.errors.map((e, i) => (
                    <p key={i} className="text-xs font-mono">
                      <span className="text-[#8a8a8a]">{e.file}</span>
                      <span className="text-[#4a4a4a] mx-1">—</span>
                      {e.step && e.step !== 'unknown' && (
                        <span className="text-[#E8B84A] mr-1">[{e.step}]</span>
                      )}
                      <span className="text-[#C97B7B]">{e.error}</span>
                    </p>
                  ))}
                  <a
                    href="#"
                    onClick={async ev => {
                      ev.preventDefault()
                      const r = await fetch(`${API}/debug/log-path`)
                      const { path } = await r.json()
                      window.open(`file://${path}`)
                    }}
                    className="text-[10px] text-[#9c9c9d] hover:opacity-70 underline inline-block pt-1"
                  >open full log →</a>
                </div>
              )}
            </div>
            )
          })()}
          {analyzeResult?.error && !resultDismissed && (
            <p className="text-xs text-[#C97B7B] font-mono">{analyzeResult.error}</p>
          )}

        </div>
        )}

      {/* Filter bar — segmented control + Auto-cull */}
      {images.length > 0 && (
        <div className="px-4 py-2 flex items-center gap-3 border-t border-[#1a1b1d]">
          {/* Segmented filter */}
          <div className="flex items-center bg-[#0d0e0f] rounded-lg p-0.5 border border-[rgba(255,255,255,0.04)] gap-px">
            {[
              { key: null,        label: 'All',       count: decisionCounts.all,       color: null,      Icon: Layers },
              { key: 'undecided', label: 'Undecided', count: decisionCounts.undecided, color: null,      Icon: Circle },
              { key: 'keep',      label: 'Keeps',     count: decisionCounts.keep,      color: '#7DB89A', Icon: Check },
              { key: 'maybe',     label: 'Maybes',    count: decisionCounts.maybe,     color: '#E8B84A', Icon: CircleDot },
              { key: 'reject',    label: 'Rejects',   count: decisionCounts.reject,    color: '#C97B7B', Icon: X },
            ].map(({ key, label, count, color, Icon }) => (
              <button
                key={key ?? 'all'}
                onClick={() => setDecisionFilter(prev => prev === key ? null : key)}
                className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[14px] font-medium transition-colors whitespace-nowrap ${
                  decisionFilter === key ? 'bg-[#1b1c1e]' : 'hover:bg-[rgba(255,255,255,0.03)]'
                }`}
                style={{ color: decisionFilter === key ? (color ?? '#f9f9f9') : (color ? `${color}80` : '#6a6b6c') }}
              >
                <Icon size={15} strokeWidth={2} />
                {label}
                {count > 0 && (
                  <span
                    className="font-mono text-[14px]"
                    style={{ color: decisionFilter === key ? (color ?? '#f9f9f9') : (color ? `${color}B3` : '#9c9c9d') }}
                  >
                    {count}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            {gridMultiSelect.isSelectMode ? (
              <>
                <span className="text-xs text-[#cecece] font-semibold tabular-nums mr-1">
                  {gridMultiSelect.size} selected
                </span>
                <span className="text-[11px] text-[#6a6b6c] mr-1 hidden lg:inline">
                  K / M / R to decide · Shift+click range · Esc to exit
                </span>
                {(() => {
                  const n = gridMultiSelect.size
                  const overCap = n > MAX_COMPARE
                  const disabled = n < 2 || overCap
                  return (
                    <button
                      onClick={bulkCompareFromSelection}
                      disabled={disabled}
                      title={
                        n < 2 ? 'Select at least 2 photos to compare'
                        : overCap ? `Compare supports up to ${MAX_COMPARE} photos at a time`
                        : `Compare ${n} photos`
                      }
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs border border-[rgba(91,184,212,0.30)] text-[#5BB8D4] bg-[rgba(91,184,212,0.06)] hover:bg-[rgba(91,184,212,0.12)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Columns2 size={14} /> Compare
                    </button>
                  )
                })()}
                <button
                  onClick={() => setMoveModalOpen(true)}
                  disabled={gridMultiSelect.size === 0}
                  title="Move the selection into an existing group"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs bg-[#1a1b1d] text-[#cecece] border border-[rgba(255,255,255,0.10)] hover:bg-[#202123] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <FolderInputIcon size={14} /> Move into group…
                </button>
                <button
                  onClick={() => {
                    const ids = Array.from(gridMultiSelect.selected)
                    if (ids.length < 2) return
                    setManualGroup({ image_ids: ids, mode: 'new_group' })
                    gridMultiSelect.exit()
                  }}
                  disabled={gridMultiSelect.size < 2}
                  title="Create a new group from the selected photos"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs bg-[#1a1b1d] text-[#cecece] border border-[rgba(255,255,255,0.10)] hover:bg-[#202123] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Plus size={14} /> New group
                </button>
                <button
                  onClick={() => gridMultiSelect.exit()}
                  className="px-2 py-1 rounded text-xs text-[#9c9c9d] hover:bg-[rgba(255,255,255,0.05)] transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : null}
          </div>
        </div>
      )}
      </div>

      {/* Content area */}
      <div className="px-6 pt-4">

        {/* Bulk action bar — undecided filter + All view */}
        {(decisionFilter === 'undecided' || decisionFilter === null) && decisionCounts.undecided > 0 && (
          <div className="mb-3 flex items-center gap-3 px-3 py-2 bg-[rgba(255,255,255,0.03)] border border-[rgba(255,255,255,0.07)] rounded-lg text-xs">
            <span className="text-[#9c9c9d]/70">
              {decisionCounts.undecided} {decisionCounts.undecided === 1 ? 'photo' : 'photos'} undecided
            </span>
            <span className="text-[#6a6b6c] flex items-center gap-1.5">
              Press <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[#9c9c9d]">K</kbd> <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[#9c9c9d]">M</kbd> <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[#9c9c9d]">R</kbd> to decide, or let the AI sort them
            </span>
            <button
              onClick={() => setAutoCullOpen(true)}
              className={`${BTN_PRIMARY} !py-1 !px-3 !text-xs`}
            >
              <WandSparkles size={15} /> Auto cull
            </button>
          </div>
        )}

        {/* Bulk action bar — maybe filter */}
        {decisionFilter === 'maybe' && decisionCounts.maybe > 0 && (
          <div className="mb-3 flex items-center gap-3 px-3 py-2 bg-[rgba(232,184,74,0.06)] border border-[rgba(232,184,74,0.20)] rounded-lg text-xs">
            <span className="text-[#E8B84A]/70">
              {decisionCounts.maybe} {decisionCounts.maybe === 1 ? 'photo' : 'photos'} in maybe
            </span>
            <span className="text-[#6a6b6c] flex items-center gap-1.5">
              Press <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[#9c9c9d]">K</kbd> to promote to <DecisionWord kind="keep">Keep</DecisionWord>, <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[#9c9c9d]">R</kbd> to <DecisionWord kind="reject">Reject</DecisionWord>
            </span>
            {bulkConfirm === 'reject-maybes' ? (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[#E8B84A]">Reject all {decisionCounts.maybe}?</span>
                <button onClick={rejectAllMaybes} disabled={bulkRunning}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[rgba(201,123,123,0.20)] border border-[rgba(255,255,255,0.10)] text-[#C97B7B] hover:opacity-70 transition-opacity disabled:opacity-50">
                  <Check size={14} />
                  {bulkRunning ? 'Moving…' : 'Confirm'}
                </button>
                <button onClick={() => setBulkConfirm(null)}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[#101111] border border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70 transition-opacity">
                  <X size={14} /> Cancel
                </button>
              </div>
            ) : (
              <button onClick={() => setBulkConfirm('reject-maybes')}
                className="ml-auto inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[rgba(232,184,74,0.12)] border border-[rgba(232,184,74,0.30)] text-[#E8B84A] hover:opacity-70 transition-opacity">
                <Trash2 size={14} /> Reject maybes
              </button>
            )}
          </div>
        )}

        {/* Bulk action bar — reject filter */}
        {decisionFilter === 'reject' && decisionCounts.reject > 0 && (
          <div className="mb-3 flex items-center gap-3 px-3 py-2 bg-[rgba(201,123,123,0.06)] border border-[rgba(201,123,123,0.15)] rounded-lg text-xs">
            <span className="text-[#C97B7B]/70">
              {decisionCounts.reject} {decisionCounts.reject === 1 ? 'rejected photo' : 'rejected photos'}
            </span>
            {bulkConfirm === 'trash-rejects' ? (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[#C97B7B]">Move {decisionCounts.reject} {decisionCounts.reject === 1 ? 'photo' : 'photos'} to Trash?</span>
                <button onClick={trashAllRejects} disabled={bulkRunning}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[rgba(201,123,123,0.20)] border border-[rgba(255,255,255,0.10)] text-[#C97B7B] hover:opacity-70 transition-opacity disabled:opacity-50">
                  <Check size={14} />
                  {bulkRunning ? 'Trashing…' : 'Confirm'}
                </button>
                <button onClick={() => setBulkConfirm(null)}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[#101111] border border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70 transition-opacity">
                  <X size={14} /> Cancel
                </button>
              </div>
            ) : (
              <button onClick={() => setBulkConfirm('trash-rejects')}
                className="ml-auto inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[rgba(201,123,123,0.12)] border border-[rgba(201,123,123,0.30)] text-[#C97B7B] hover:opacity-70 transition-opacity">
                <Trash2 size={14} /> Move to Trash
              </button>
            )}
          </div>
        )}

        {/* Personal-model status banner — visible in grid view until the
            model reaches the "ready" tier OR the user dismisses it. Hidden
            inside training mode (TrainingModeView already shows the full
            PersonalModelPanel). */}
        {activeView === 'grid' && images.length > 0 && !bannerDismissed && (decisionFilter === null || decisionFilter === 'undecided') && (
          <PersonalModelBanner
            modelInfo={modelInfo}
            onDismiss={() => setBannerDismissConfirm(true)}
          />
        )}

        {/* Main content: Dashboard, Training view, empty/onboarding, skeleton, or grid */}
        {activeView === 'dashboard' ? (
          <DashboardView />
        ) : activeView === 'train' ? (
          <TrainingModeView
            queue={trainingQueue}
            currentIdx={trainingIdx}
            onDecide={advanceTraining}
            onExit={() => { setActiveView('grid'); loadImages() }}
            modelInfo={modelInfo}
            onTrain={trainModel}
            training={trainingModel}
            onEnterPairwise={enterPairwiseMode}
          />
        ) : activeView === 'pairwise' ? (
          <PairwiseTrainingView
            images={images}
            sourceFolder={activeTab?.folderPath}
            onExit={() => { setActiveView('grid'); loadImages() }}
            modelInfo={modelInfo}
          />
        ) : analyzing && images.length === 0 ? (
          <div
            className={`grid gap-3 pb-8 ${userCols ? '' : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6'}`}
            style={userCols ? { gridTemplateColumns: `repeat(${userCols}, minmax(0, 1fr))` } : {}}
          >
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="space-y-2">
                <div className="aspect-[4/3] rounded-lg shimmer border border-[rgba(255,255,255,0.06)]" />
                <div className="h-3 w-3/4 rounded shimmer" />
                <div className="h-2 w-1/2 rounded shimmer" />
              </div>
            ))}
          </div>
        ) : activeTab?.watchLive && images.length === 0 ? (
          <div className="max-w-md mx-auto mt-24 text-center space-y-3">
            <div className="inline-flex items-center gap-2 text-sm text-[#5BB8D4]">
              <span className="w-2 h-2 rounded-full bg-[#5BB8D4] animate-pulse inline-block" />
              Watching for new photos…
            </div>
            <p className="text-xs text-[#6a6b6c] font-mono break-all">{activeTab.folderPath}</p>
            <p className="text-xs text-[#cecece] leading-relaxed pt-2">
              New photos in this folder will be analyzed automatically and appear here.
            </p>
          </div>
        ) : images.length === 0 ? (
          (() => {
            // Empty-state copy adapts to the personal-model tier so the user
            // sees what the system will actually do for the photos they're
            // about to analyze. Tiers (mirrors phase3_learning/personal_model.py):
            //   untrained        — < 30 decisions, model can't train yet
            //   learning         — trained, < 50 samples, auto-cull still uses thresholds
            //   underperforming  — ≥ 50 samples but worse than baseline
            //   ready            — ≥ 50 samples AND beats baseline → drives auto-cull
            const decided  = modelInfo?.decided_count ?? 0
            const minNeeded = modelInfo?.min_decisions ?? 30
            const status   = modelInfo?.model_status ?? (modelInfo?.ready ? 'ready' : 'untrained')
            const trained  = modelInfo?.training_size ?? 0

            // What does analysis produce right now? Always the technical +
            // perceptual signals; a ready model adds a personalised re-rank
            // on top, and the copy strengthens once the model is well-trained.
            const wellTrained = status === 'ready' && trained >= 200
            const analysisCopy = wellTrained
              ? <>Each photo is analysed for sharpness, exposure, perceptual quality, aesthetics, and faces — then re-ranked by your trained taste model.</>
              : status === 'ready'
                ? <>Each photo is analysed for sharpness, exposure, perceptual quality, aesthetics, and faces — then re-ranked by your taste model as it learns your eye.</>
                : <>Each photo is analysed for sharpness, exposure, perceptual quality, aesthetics, and faces.</>

            // Model-progress line, shown under a hairline. Concrete numbers,
            // not vague encouragement.
            let modelLine
            if (status === 'untrained') {
              const remaining = Math.max(0, minNeeded - decided)
              modelLine = remaining > 0 ? (
                <>
                  <span className="text-[#cecece] font-mono">{decided}</span>
                  <span className="text-[#6a6b6c]"> / {minNeeded}</span>{' '}
                  decisions made — <span className="text-[#cecece]">{remaining} more</span> until you can train a personal model.
                </>
              ) : (
                <>
                  <span className="text-[#cecece] font-mono">{decided}</span> decisions made — ready to train your first personal model.
                </>
              )
            } else if (status === 'learning') {
              const remaining = Math.max(0, 50 - trained)
              modelLine = (
                <>
                  Personal model trained on <span className="text-[#cecece] font-mono">{trained}</span> decisions, still learning. Auto-cull keeps using quality thresholds for{' '}
                  <span className="text-[#cecece]">{remaining} more</span> decisions, then switches to your taste.
                </>
              )
            } else if (status === 'underperforming') {
              modelLine = (
                <>
                  Personal model trained on <span className="text-[#cecece] font-mono">{trained}</span> decisions, but not yet beating the quality-threshold baseline. More decisions sharpen its taste — keep culling.
                </>
              )
            } else { // ready
              modelLine = (
                <>
                  Personal model is driving auto-cull, trained on <span className="text-[#cecece] font-mono">{trained}</span> decisions. The more you cull, the closer it gets to your taste.
                </>
              )
            }

            const openPicker = () => activeTab && startNewAnalysis(activeTab.id)
            return (
              <div className="flex flex-col items-center justify-center min-h-[70vh] py-16 select-none">
                {/* Folder mark — clickable; opens the OS folder picker. Surface-
                    200 disc with subtle inner stroke + a thin cyan focus ring to
                    echo the selection color. */}
                <button
                  type="button"
                  onClick={openPicker}
                  className="folder-cta relative mb-6 group focus:outline-none"
                  title="Open folder"
                  aria-label="Open folder"
                >
                  <div className="w-20 h-20 rounded-full bg-[#101111] flex items-center justify-center transition-all duration-200 group-hover:bg-[#16191c] group-hover:scale-[1.04]">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" className="text-[#9c9c9d] group-hover:text-[#5BB8D4] transition-colors duration-200">
                      <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
                    </svg>
                  </div>
                </button>

                {/* Headline + analysis copy (status-aware). The "Open folder"
                    fragment is the primary action — clicking it opens the OS
                    folder picker. Uses the same handler as the folder mark. */}
                <div className="text-center max-w-md mb-10 px-6 mt-2">
                  <h2 className="text-[15px] font-medium text-[#f9f9f9] mb-2 tracking-[-0.01em]">
                    <button
                      type="button"
                      onClick={openPicker}
                      className="text-[#5BB8D4] hover:opacity-70 transition-opacity underline-offset-4 hover:underline focus:outline-none focus-visible:underline"
                    >
                      Open folder
                    </button>
                    {' '}to begin
                  </h2>
                  <p className="text-[13px] text-[#9c9c9d] leading-relaxed">
                    Click the folder above{autoOpenFinder ? <> (or the <span className="text-[#cecece] font-medium">+</span> button in the header)</> : null} to pick a folder of photos — RAW, JPG, HIF, PNG, or TIFF.{' '}
                    {analysisCopy}
                  </p>
                </div>

                {/* Cull keys + factual footnotes. */}
                <div className="w-full max-w-md px-6 space-y-5">
                  <div>
                    <p className="label mb-3 text-center">
                      Cull with
                    </p>
                    <div className="rounded-xl bg-[#0f1011] border border-[rgba(255,255,255,0.04)] px-5 pt-4 pb-3.5">
                      <div className="flex items-center justify-center gap-7">
                      <div className="flex items-center gap-2">
                        <KeyCap tone="keep">K</KeyCap>
                        <DecisionWord kind="keep" className="text-[13px]">Keep</DecisionWord>
                      </div>
                      <div className="flex items-center gap-2">
                        <KeyCap tone="maybe">M</KeyCap>
                        <DecisionWord kind="maybe" className="text-[13px]">Maybe</DecisionWord>
                      </div>
                      <div className="flex items-center gap-2">
                        <KeyCap tone="reject">R</KeyCap>
                        <DecisionWord kind="reject" className="text-[13px]">Reject</DecisionWord>
                      </div>
                    </div>
                      <p className="mt-3.5 pt-3 border-t border-[rgba(255,255,255,0.04)] flex items-center justify-center gap-1.5 text-[12px] text-[#6a6b6c] leading-relaxed">
                      <Info size={12} className="flex-shrink-0" aria-hidden="true" />
                      <span>
                        Decisions immediately move files into{' '}
                        <span className="text-[#9c9c9d] font-mono text-[11px]">_Keeps</span>{' '}/{' '}
                        <span className="text-[#9c9c9d] font-mono text-[11px]">_Maybes</span>{' '}/{' '}
                        <span className="text-[#9c9c9d] font-mono text-[11px]">_Trash</span>.
                      </span>
                      </p>
                    </div>
                  </div>

                  <div className="pt-5 border-t border-[rgba(255,255,255,0.04)] space-y-3 text-center">
                    {/* Personal-model status — wrapped in the same animated
                        rainbow stroke as the PersonalModelBanner so the AI
                        affordance reads consistently across views. */}
                    <div className="ai-border">
                      <div className="ai-border-inner px-4 py-4 bg-[#0c0d0f]">
                        <div className="flex items-start gap-2.5 text-left">
                          <Sparkles size={16} className="text-[#5BB8D4] flex-shrink-0 mt-0.5" />
                          <p className="text-[13px] text-[#e6e6e6] leading-relaxed font-medium">
                            {modelLine}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Auto-open Finder toggle — purely client-side preference
                      (localStorage). When ON, the header `+` button opens the
                      OS folder picker directly; when OFF, `+` lands on this
                      page and the user clicks "Open folder" themselves. */}
                  <div className="pt-5 flex items-center justify-between gap-4">
                    <div className="text-left">
                      <p className="text-[12px] text-[#cecece] leading-relaxed">
                        Auto-open folder picker
                      </p>
                      <p className="text-[11px] text-[#6a6b6c] leading-relaxed">
                        When on, clicking <span className="text-[#cecece] font-medium">+</span> in the header opens the folder picker immediately.
                      </p>
                    </div>
                    <Toggle enabled={autoOpenFinder} onChange={setAutoOpenFinder} />
                  </div>
                </div>
              </div>
            )
          })()
        ) : (
          <div
            ref={gridRef}
            role="grid"
            className={`grid gap-3 pb-8 ${userCols ? '' : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6'}`}
            style={userCols ? { gridTemplateColumns: `repeat(${userCols}, minmax(0, 1fr))` } : {}}
            onClick={(e) => {
              // Click on empty grid background (not on a card) deselects.
              if (e.target === e.currentTarget) {
                setSelectedIdx(-1)
                setSelectedGroupId(null)
              }
            }}
          >
            {displayGridItems.length === 0 && searchMode === 'semantic' && semanticResults !== null && searchQuery && (
              <div className="col-span-full text-center text-[#9c9c9d] text-sm py-16">
                No photos matched <span className="text-[#cecece]">"{searchQuery}"</span> — try a broader description.
              </div>
            )}
            {displayGridItems.length === 0 && searchMode !== 'semantic' && decisionFilter && (
              <div className="col-span-full flex flex-col items-center gap-3 py-16">
                <span className="text-[#9c9c9d] text-sm">No photos in this filter.</span>
                <button
                  onClick={() => setDecisionFilter(null)}
                  className="text-sm text-[#5BB8D4] hover:text-[#7ECDE3] transition-colors"
                >
                  Clear filter
                </button>
              </div>
            )}
            {/* Leading skeleton for the photo currently being analyzed.
                Sits top-left regardless of sort order so the grid always
                shows visible activity while a batch runs. Complements the
                trailing skeletons further down (which only line up with
                date-asc sort). */}
            {analyzing
              && progress?.current_file
              && !decisionFilter
              && !searchQuery && (
                <div
                  key="analyzing-now"
                  role="presentation"
                  className="space-y-2"
                  title={`Analyzing ${progress.current_file}`}
                >
                  <div className="aspect-[4/3] rounded-lg shimmer border border-[rgba(255,255,255,0.06)]" />
                  <div className="text-xs font-mono text-[#9c9c9d] truncate">{progress.current_file}</div>
                  <div className="h-2 w-1/2 rounded shimmer" />
                </div>
              )}
            {displayGridItems.map((item, idx) =>
              item.type === 'group' ? (
                // Roving tabIndex: the selected cell is the grid's only Tab
                // stop. Tab into the grid lands on it; Tab again exits to the
                // next region. Other cells stay reachable via arrow keys
                // (which also call .focus() on the new cell to keep DOM focus
                // and the cyan-ring cursor in sync). outline-none suppresses
                // the browser default — GroupTile's own ring shows selection.
                <div
                  key={`group-${item.group.best_image_id}`}
                  data-grid-idx={idx}
                  role="gridcell"
                  tabIndex={selectedGridIdx === idx ? 0 : -1}
                  className="outline-none"
                  onFocus={() => {
                    if (selectedGroupId === item.group.best_image_id) return
                    setSelectedGroupId(item.group.best_image_id)
                  }}
                >
                  <GroupTile
                    group={item.group}
                    isSelected={selectedGroupId === item.group.best_image_id}
                    isSelectMode={gridMultiSelect.isSelectMode}
                    isDropHover={dropHoverGroupId === item.group.best_image_id}
                    onSelect={() => {
                      setSelectedGroupId(item.group.best_image_id)
                    }}
                    onOpen={() => {
                      setSelectedGroupId(item.group.best_image_id)
                      enterLoupe(item.group.best_image_id)
                    }}
                    filterContext={item.filterContext}
                    quickDecide={
                      // Enable on the Maybe filter only. Resolves to the
                      // subset of members currently holding 'maybe', so
                      // already-decided members in the same burst are not
                      // re-decided. Skip in select mode (multi-select owns
                      // batch operations there).
                      decisionFilter === 'maybe' && !gridMultiSelect.isSelectMode
                        ? (decision) => {
                            const ids = item.group.images
                              .filter(img => img.decision === 'maybe')
                              .map(img => img.id)
                            if (ids.length > 0) bulkDecide(ids, decision)
                          }
                        : null
                    }
                    onDragOver={(e) => {
                      // Accept any drag carrying a photo payload. We don't
                      // inspect the payload here to keep the visual fast;
                      // onDrop does the actual validation.
                      e.preventDefault()
                      e.dataTransfer.dropEffect = 'move'
                      if (dropHoverGroupId !== item.group.best_image_id) {
                        setDropHoverGroupId(item.group.best_image_id)
                      }
                    }}
                    onDragLeave={() => {
                      if (dropHoverGroupId === item.group.best_image_id) {
                        setDropHoverGroupId(null)
                      }
                    }}
                    onDrop={(e) => {
                      e.preventDefault()
                      setDropHoverGroupId(null)
                      try {
                        const raw = e.dataTransfer.getData('application/json')
                        if (!raw) return
                        const payload = JSON.parse(raw)
                        if (payload?.kind !== 'photos' || !Array.isArray(payload.image_ids) || payload.image_ids.length === 0) return
                        setManualGroup({
                          image_ids: payload.image_ids,
                          mode: 'join_group',
                          target_image_id: item.group.best_image_id,
                        })
                        gridMultiSelect.exit()
                      } catch { /* silently ignore malformed payloads */ }
                    }}
                  />
                </div>
              ) : (
                <div
                  key={item.image.id}
                  data-grid-idx={idx}
                  role="gridcell"
                  tabIndex={selectedGridIdx === idx ? 0 : -1}
                  className="outline-none"
                  onFocus={() => {
                    const i = images.findIndex(img => img.id === item.image.id)
                    if (selectedGroupId == null && selectedIdx === i) return
                    setSelectedGroupId(null)
                    if (i >= 0) setSelectedIdx(i)
                  }}
                  onClick={(e) => {
                    const imgId = item.image.id
                    // Modifier-aware click: cmd/ctrl+click toggles selection,
                    // shift+click extends range from the anchor (works as a
                    // toggle when no anchor exists yet), bare click in select
                    // mode toggles, bare click out of select mode = legacy
                    // single-focus behaviour.
                    //
                    // When a modifier+click *enters* multi-select from
                    // single-focus mode, seed the selection with the
                    // previously-focused photo so it's not lost. Matches
                    // Finder/Lightroom: "I had A focused, Cmd+clicked B" →
                    // selection is {A, B}.
                    if (e.metaKey || e.ctrlKey) {
                      e.preventDefault()
                      if (!gridMultiSelect.isSelectMode) {
                        gridMultiSelect.enter()
                        const focusedId = (selectedGroupId == null && images[selectedIdx]?.id)
                        if (focusedId && focusedId !== imgId) {
                          gridMultiSelect.toggle(focusedId)
                        }
                      }
                      gridMultiSelect.toggle(imgId)
                      return
                    }
                    if (e.shiftKey) {
                      e.preventDefault()
                      if (!gridMultiSelect.isSelectMode) {
                        gridMultiSelect.enter()
                        // Anchor the focused photo so the extend covers the
                        // range from "what I had focused" to "what I just
                        // shift-clicked", not just the shift-clicked tile.
                        const focusedId = (selectedGroupId == null && images[selectedIdx]?.id)
                        if (focusedId && focusedId !== imgId) {
                          gridMultiSelect.toggle(focusedId)
                        }
                      }
                      const orderedIds = displayGridItems
                        .map(it => it.type === 'image' ? it.image.id : null)
                        .filter(x => x != null)
                      gridMultiSelect.extend(imgId, orderedIds)
                      return
                    }
                    if (gridMultiSelect.isSelectMode) {
                      gridMultiSelect.toggle(imgId)
                      // Auto-exit when last selection is cleared so the user
                      // doesn't get stuck in an empty select mode.
                      if (gridMultiSelect.size === 1 && gridMultiSelect.selected.has(imgId)) {
                        // We just deselected the only item — leave select mode.
                        gridMultiSelect.exit()
                      }
                      return
                    }
                    setSelectedGroupId(null)
                    setSelectedIdx(images.findIndex(img => img.id === imgId))
                    // Make the focused tile the anchor for a subsequent
                    // shift+click range select, even though we're not in
                    // select mode yet.
                    gridMultiSelect.setAnchor(imgId)
                  }}
                  onDoubleClick={() => {
                    if (gridMultiSelect.isSelectMode) return
                    setSelectedGroupId(null)
                    setSelectedIdx(images.findIndex(img => img.id === item.image.id))
                    setDetailGroupContext(null)
                    setDetailOpen(true)
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault()
                    setTileMenu({ imageId: item.image.id, x: e.clientX, y: e.clientY })
                  }}
                  onDragOver={(e) => {
                    // Photos are drop targets too: dropping one photo on
                    // another creates a new manual group containing the
                    // dragged set + the target. preventDefault lets the
                    // drop fire.
                    e.preventDefault()
                    e.dataTransfer.dropEffect = 'move'
                    if (dropHoverPhotoId !== item.image.id) {
                      setDropHoverPhotoId(item.image.id)
                    }
                  }}
                  onDragLeave={() => {
                    if (dropHoverPhotoId === item.image.id) setDropHoverPhotoId(null)
                  }}
                  onDrop={(e) => {
                    e.preventDefault()
                    setDropHoverPhotoId(null)
                    try {
                      const raw = e.dataTransfer.getData('application/json')
                      if (!raw) return
                      const payload = JSON.parse(raw)
                      if (payload?.kind !== 'photos' || !Array.isArray(payload.image_ids) || payload.image_ids.length === 0) return
                      const targetId = item.image.id
                      const sourceIds = payload.image_ids.filter(id => id !== targetId)
                      if (sourceIds.length === 0) return  // drop-on-self no-op
                      const targetImg = images.find(i => i.id === targetId)
                      if (targetImg?.manual_group_id) {
                        // Target already in a manual group → inherit it.
                        setManualGroup({
                          image_ids: sourceIds,
                          mode: 'join_group',
                          target_image_id: targetId,
                        })
                      } else {
                        // Fresh group containing source(s) + target.
                        setManualGroup({
                          image_ids: [targetId, ...sourceIds],
                          mode: 'new_group',
                        })
                      }
                      gridMultiSelect.exit()
                    } catch { /* silently ignore malformed payloads */ }
                  }}
                >
                  <ImageCard
                    image={item.image}
                    isSelected={!gridMultiSelect.isSelectMode && selectedGroupId == null && item.image.id === images[selectedIdx]?.id}
                    isMultiSelected={gridMultiSelect.selected.has(item.image.id)}
                    isSelectMode={gridMultiSelect.isSelectMode}
                    isDropHover={dropHoverPhotoId === item.image.id}
                    searchQuery={searchQuery}
                    modelInfo={modelInfo}
                    quickDecide={
                      // Solo-tile Maybe quick-decide: same gating as the
                      // GroupTile path. bulkDecide handles undo + refresh +
                      // toast, so we wrap the single id in an array.
                      decisionFilter === 'maybe' && !gridMultiSelect.isSelectMode
                        ? (decision) => bulkDecide([item.image.id], decision)
                        : null
                    }
                    draggable
                    onDragStart={(e) => {
                      // Payload carries the entire current selection when
                      // the dragged photo is part of it; otherwise just the
                      // dragged photo itself. This makes drag-to-create-
                      // group work on any tile without requiring select mode.
                      const selected = gridMultiSelect.selected
                      const ids = selected.has(item.image.id)
                        ? Array.from(selected)
                        : [item.image.id]
                      e.dataTransfer.effectAllowed = 'move'
                      e.dataTransfer.setData('application/json', JSON.stringify({ kind: 'photos', image_ids: ids }))
                      e.dataTransfer.setData('text/plain', ids.join(','))
                    }}
                  />
                </div>
              )
            )}
            {/* Trailing skeletons for photos still being analyzed */}
            {analyzing
              && progress?.total > images.length
              && !decisionFilter
              && displayGridItems.length === images.length
              && Array.from({ length: Math.min(progress.total - images.length, 24) }).map((_, i) => (
                <div key={`skeleton-${i}`} className="space-y-2">
                  <div className="aspect-[4/3] rounded-lg shimmer border border-[rgba(255,255,255,0.06)]" />
                  <div className="h-3 w-3/4 rounded shimmer" />
                  <div className="h-2 w-1/2 rounded shimmer" />
                </div>
              ))}
          </div>
        )}

      </div>

      {/* Bottom toolbar — Sort, Filter, View, Tab settings, Search.
          Rendered in one of two hosts depending on DetailView state:
            · Floating pill on the grid surface (default)
            · DetailView's filmstrip toolbar (Luminar-style) via React portal
              into the slot exposed by setBottomToolbarSlot. When portalled,
              the floating chrome is dropped so the controls sit flat inside
              the toolbar. Dropdown/popover positioning still works because
              they use `absolute` against their nearest positioned ancestor —
              the toolbar gives them one. */}
      {images.length > 0
        && !loupeGroup
        && !compareOpen
        && activeView !== 'train'
        && activeView !== 'dashboard' && (() => {
        const inToolbar = detailOpen && bottomToolbarSlot
        const wrapperClass = inToolbar
          ? 'ml-auto flex items-center gap-1'
          : 'fixed left-1/2 -translate-x-1/2 z-[60] flex items-center gap-1 px-2 py-1.5 bg-[#111214] border border-[#2a2b2d] rounded-2xl shadow-[0_4px_24px_rgba(0,0,0,0.6)]'
        const wrapperStyle = inToolbar ? {} : { bottom: '8px' }
        const tree = (
        <div style={wrapperStyle} className={wrapperClass}>

          {/* Sort dropdown — 2-level menu, leading direction toggle. State
              persists in localStorage via useSort(); same instance drives the
              GroupLoupe photo order via props. */}
          {activeView === 'grid' && (
            <SortPill
              sortField={sortField}
              sortDir={sortDir}
              onSelectField={setSortField}
              onToggleDir={toggleSortDir}
              visibleMetrics={visibleMetrics}
              open={sortOpen}
              onOpen={() => { setViewOpen(false); setTabSettingsOpen(false); setFilterOpen(false); setSortOpen(true) }}
              onClose={() => setSortOpen(false)}
              orientation={detailOpen ? 'horizontal' : 'vertical'}
            />
          )}

          {/* Divider */}
          {activeView === 'grid' && <div className="w-px h-4 bg-[#2a2b2d] mx-1" />}

          {/* Filter pill — date / portraits / landscape / group / camera.
              In-memory state on App; predicate composed into displayGridItems. */}
          {activeView === 'grid' && (
            <FilterPill
              filter={filter}
              setFilter={setFilter}
              images={images}
              open={filterOpen}
              onOpen={() => { setSortOpen(false); setViewOpen(false); setTabSettingsOpen(false); setFilterOpen(true) }}
              onClose={() => setFilterOpen(false)}
            />
          )}

          {/* Divider between Filter and the next group. Shown in both grid and
              DetailView (in DetailView there's no View pill, so this divider
              separates the FilterPill from the Tab-settings icon). */}
          {activeView === 'grid' && <div className="w-px h-4 bg-[#2a2b2d] mx-1" />}

          {/* View pill — only on the main grid. In DetailView the strip's
              thumbnail size is set by directly dragging the strip's top edge,
              so a size dropdown there would be redundant. */}
          {activeView === 'grid' && !detailOpen && (
            <ViewPill
              layout={gridLayout}
              layoutOptions={GRID_ONLY_LAYOUT_OPTIONS}
              onSelectLayout={(id) => { setGridLayout(id); setViewOpen(false) }}
              sizeOptionsByLayout={{ grid: GRID_SIZE_OPTIONS }}
              sizeByLayout={{ grid: userCols ?? 6 }}
              onSelectSize={(_layoutId, value) => { setUserCols(value); setViewOpen(false) }}
              sizeLabelByLayout={{ grid: 'Tile size' }}
              open={viewOpen}
              onOpen={() => { setSortOpen(false); setFilterOpen(false); setTabSettingsOpen(false); setViewOpen(true) }}
              onClose={() => setViewOpen(false)}
            />
          )}

          {/* Divider */}
          {activeView === 'grid' && !detailOpen && <div className="w-px h-4 bg-[#2a2b2d] mx-1" />}

          {/* Select — explicit entry point into multi-select mode. Cmd/Shift+click
              on a tile is the faster path; this is the discoverable affordance,
              mirroring the GroupLoupe Select button. */}
          {activeView === 'grid' && !detailOpen && (
            <>
              <button
                onClick={() => gridMultiSelect.isSelectMode ? gridMultiSelect.exit() : gridMultiSelect.enter()}
                className={`px-2 py-1 rounded-lg text-xs transition-opacity border whitespace-nowrap inline-flex items-center gap-1.5 ${gridMultiSelect.isSelectMode ? 'bg-[#1a1b1d] text-[#5BB8D4] border-[rgba(91,184,212,0.30)]' : 'text-[#cecece] border-transparent hover:opacity-70'}`}
                title="Multi-select photos to act on as a batch"
                aria-label="Select"
              >
                <MousePointerSquareDashed size={13} />
                Select
              </button>
              <div className="w-px h-4 bg-[#2a2b2d] mx-1" />
            </>
          )}

          {/* Tab settings menu — Watch live + Folders + Groups subsection */}
          {activeTab?.status === 'ready' && (() => {
            const watchingTab = tabs.find(t => t.watchLive)
            const watchingElsewhere = watchingTab && watchingTab.id !== activeTab.id
            const canToggleHere = !!activeTab.folderPath
            return (
            <div className="relative" data-dropdown="true">
              <button
                onClick={() => { setSortOpen(false); setFilterOpen(false); setViewOpen(false); setTabSettingsOpen(v => !v) }}
                title="Tab settings"
                aria-label="Tab settings"
                className={`px-2 py-1 rounded-lg text-xs transition-colors border whitespace-nowrap inline-flex items-center ${tabSettingsOpen ? 'bg-[#1a1b1d] text-[#f0f0f0] border-[#2a2b2d]' : 'text-[#cecece] border-transparent hover:bg-[rgba(255,255,255,0.06)] hover:text-[#f0f0f0]'}`}
              >
                <SlidersHorizontal size={15} />
              </button>
              {tabSettingsOpen && (
                // right-0 anchors the popover's right edge to the trigger's
                // right edge so the 400px panel extends leftward — keeps it
                // on-screen for the rightmost pill button. max-w cap shrinks
                // it further on viewports < 416px.
                <div className="absolute right-0 bottom-full mb-1 z-[70] bg-[#111214] border border-[#2a2b2d] rounded-lg shadow-lg w-[400px] max-w-[calc(100vw-16px)] max-h-[80vh] overflow-y-auto">

                  {/* ── Watch live ── */}
                  <div className="px-4 py-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <p className="label mb-1">Watch live</p>
                        <p className="text-xs text-[#9c9c9d] leading-relaxed">
                          {!canToggleHere
                            ? 'Pick a folder first to enable live watching.'
                            : activeTab.watchLive
                              ? 'Auto-analyzing new photos as they appear in this folder.'
                              : watchingElsewhere
                                ? <>Currently on <span className="font-mono text-[#cecece]">"{tabLabel(watchingTab)}"</span> — toggle to switch here.</>
                                : 'Auto-analyze new photos as they appear in this folder.'}
                        </p>
                      </div>
                      <div className={`pt-0.5 ${!canToggleHere ? 'opacity-40 pointer-events-none' : ''}`}>
                        <Toggle
                          enabled={!!activeTab.watchLive}
                          onChange={(next) => {
                            if (!canToggleHere) return
                            if (!next)               { toggleWatchLive(activeTab.id); return }
                            if (watchingElsewhere)   { setWatchSwitchConfirm(true);   return }
                            toggleWatchLive(activeTab.id)
                          }}
                        />
                      </div>
                    </div>

                    {/* Inline confirmation when switching the active watcher. */}
                    {watchSwitchConfirm && watchingElsewhere && !activeTab.watchLive && (
                      <div className="mt-3 p-2.5 rounded-md bg-[rgba(91,184,212,0.08)] border border-[rgba(91,184,212,0.25)] space-y-2">
                        <p className="text-[11px] text-[#cecece] leading-snug">
                          Watch live is on <span className="font-mono text-[#5BB8D4]">"{tabLabel(watchingTab)}"</span>. Switch to this folder?
                        </p>
                        <div className="flex gap-1.5">
                          <button
                            onClick={() => { toggleWatchLive(activeTab.id); setWatchSwitchConfirm(false) }}
                            className="flex-1 inline-flex items-center justify-center gap-1.5 py-1 px-2 rounded text-[11px] font-semibold bg-[rgba(91,184,212,0.20)] hover:opacity-70 text-[#5BB8D4] border border-[rgba(91,184,212,0.40)] transition-opacity"
                          >
                            <Check size={14} /> Switch here
                          </button>
                          <button
                            onClick={() => setWatchSwitchConfirm(false)}
                            className="inline-flex items-center gap-1.5 py-1 px-2 rounded text-[11px] text-[#9c9c9d] border border-[rgba(255,255,255,0.10)] hover:opacity-70 transition-opacity"
                          >
                            <X size={13} /> Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* ── Folders ── */}
                  {activeTab?.folderPath && (
                    <div className="px-4 py-4 border-t border-[rgba(255,255,255,0.05)]">
                      <p className="label mb-1">Folders</p>
                      <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                        Each K / M / R press moves the file into a subfolder of this analysis. Click a row to pick a fixed location instead.
                      </p>
                      <TabFoldersForm
                        sourceFolder={activeTab.folderPath}
                        onToast={addToast}
                        autoLoad={tabSettingsOpen}
                      />
                    </div>
                  )}

                  {/* ── Groups ── */}
                  {enrichedGroups.length > 0 && (
                    <div className="px-4 py-4 border-t border-[rgba(255,255,255,0.05)]">
                      <p className="label mb-1">Groups</p>
                      <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                        Cluster by visual content (Bursts) or by who's in them (People). Open a group to fine-tune the clustering live.
                      </p>

                      {/* Bursts / People mode segmented */}
                      <div className="flex items-center gap-1.5">
                        {['bursts', 'people'].map(mode => (
                          <button
                            key={mode}
                            onClick={() => setGroupMode(mode)}
                            className={`px-3 py-1 rounded-md border text-xs transition-opacity capitalize ${
                              groupMode === mode
                                ? 'border-[#5BB8D4] text-[#5BB8D4]'
                                : 'border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70'
                            }`}
                          >{mode}</button>
                        ))}
                      </div>

                      {/* Re-analyse nudge: rows analysed before the FaceNet
                          identity model existed have NULL face_embedding and
                          can't appear in People mode until reprocessed. */}
                      {groupMode === 'people' && peoplePendingReanalysis > 0 && (
                        <p className="text-[11px] text-[#E8B84A] leading-relaxed mt-3">
                          {peoplePendingReanalysis} photo{peoplePendingReanalysis === 1 ? '' : 's'} with faces need re-analysis to appear here. Run analysis again on this folder to enable People mode for them.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
            )
          })()}

          {/* Search — hidden in DetailView (the filmstrip surface) since
              filename/scene search isn't useful while drilling into one photo.
              Collapses to an icon by default, expands inline with a width
              transition. */}
          {!detailOpen && (<>
          <div className="w-px h-4 bg-[#2a2b2d] mx-1" />
          <div data-dropdown="true" className="flex items-center">
            <div
              className={`flex items-center overflow-hidden transition-[width] duration-200 ease-out ${
                searchExpanded ? 'w-[260px]' : 'w-8'
              }`}
            >
              <button
                onClick={() => {
                  if (searchExpanded) {
                    setSearchExpanded(false)
                  } else {
                    setSearchExpanded(true)
                    // Defer focus until the width transition starts so the
                    // input is visible before the cursor lands.
                    requestAnimationFrame(() => searchInputRef.current?.focus())
                  }
                }}
                title={searchExpanded ? 'Hide search' : 'Search'}
                aria-label={searchExpanded ? 'Hide search' : 'Search'}
                aria-expanded={searchExpanded}
                className={`w-8 h-8 flex-shrink-0 rounded-lg inline-flex items-center justify-center transition-colors ${
                  searchExpanded || searchInput ? 'text-[#f0f0f0]' : 'text-[#cecece] hover:bg-[rgba(255,255,255,0.06)] hover:text-[#f0f0f0]'
                }`}
              >
                {semanticLoading ? <Spinner size={12} /> : <Search size={15} strokeWidth={1.75} />}
              </button>

              {/* Input + AI toggle. Always rendered so the width transition
                  is purely CSS, but visually + interaction-disabled when
                  collapsed via opacity + tabIndex. */}
              <div
                className={`relative flex items-center flex-1 min-w-0 ml-1 transition-opacity duration-150 ${
                  searchExpanded ? 'opacity-100 delay-75' : 'opacity-0 pointer-events-none'
                }`}
              >
                <div className={`relative flex-1 min-w-0 ${searchMode === 'semantic' ? 'ai-border-sm' : ''}`}>
                  <input
                    ref={searchInputRef}
                    type="text"
                    value={searchInput}
                    onChange={e => setSearchInput(e.target.value)}
                    tabIndex={searchExpanded ? 0 : -1}
                    placeholder={searchMode === 'semantic' ? 'Describe a scene…' : 'Search filenames…'}
                    className="w-full pl-2 pr-7 py-1 rounded text-xs bg-[#0d0e10] border border-[rgba(255,255,255,0.10)] text-[#cecece] placeholder:text-[#4a4a4a] focus:outline-none focus:border-[rgba(91,184,212,0.40)] transition-colors"
                    style={searchMode === 'semantic' ? { borderColor: 'transparent' } : {}}
                  />
                  {searchInput && (
                    <button
                      onClick={() => { setSearchInput(''); setSearchQuery(''); setSemanticResults(null) }}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 w-5 h-5 rounded flex items-center justify-center text-[#6a6b6c] hover:text-[#cecece] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
                      title="Clear search"
                      aria-label="Clear search"
                    ><X size={12} /></button>
                  )}
                </div>
                <button
                  onClick={() => {
                    const next = searchMode === 'filename' ? 'semantic' : 'filename'
                    setSearchMode(next)
                    setSemanticResults(null)
                  }}
                  tabIndex={searchExpanded ? 0 : -1}
                  title={searchMode === 'semantic' ? 'AI search active — click for filename search' : 'Switch to AI semantic search'}
                  className="ml-1 inline-flex items-center gap-1 px-1.5 py-1 rounded text-[10px] font-medium transition-colors"
                  style={searchMode === 'semantic'
                    ? { background: 'rgba(91,184,212,0.08)', border: '1px solid rgba(91,184,212,0.20)' }
                    : { color: '#6a6b6c', background: 'transparent', border: '1px solid transparent' }
                  }
                >
                  <Sparkles size={13} />
                  {searchMode === 'semantic'
                    ? <span className="ai-text-rainbow">AI</span>
                    : 'AI'
                  }
                </button>
              </div>
            </div>
          </div>
          </>)}

        </div>
        )
        return inToolbar ? createPortal(tree, bottomToolbarSlot) : tree
      })()}

    </div>
  )
}
