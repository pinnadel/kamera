// Tab management hook — owns all tab lifecycle logic for App.jsx.
//
// Covers:
//   - Derived-state setters (setImages, setSelectedIdx, etc.) that proxy to
//     the active tab so legacy call-sites remain unchanged.
//   - Folder-restore-on-launch effect (GET /folders → build initial tab list).
//   - Active-tab persistence (localStorage keyed by folder path, not uuid).
//   - Lazy image load when a ready tab hasn't been fetched yet.
//   - On-focus sync-folder scan to drop deleted files from the grid.
//   - runAnalysisForTab, stopAnalysis, handleNewAnalysis, handleCloseTab,
//     handleTabWatchLive, confirmOverwrite, confirmCloseTab, reorderTabs,
//     toggleWatchLive, startNewAnalysis.

import { useState, useEffect, useCallback, useRef } from 'react'
import { API } from '../api'
import { makeNewTab, makeReadyTab } from '../tabs'

export function useTabs({ tabs, setTabs, activeTabId, setActiveTabId, addToast }) {

  // ── Derived active-tab mutators ──────────────────────────────────────────
  // Each mirrors the original setState API (value or updater function) so
  // existing call-sites in App.jsx remain unchanged after the refactor.

  const updateActiveTab = useCallback((patch) => {
    setTabs(prev => prev.map(t => {
      if (t.id !== activeTabId) return t
      const next = typeof patch === 'function' ? patch(t) : patch
      return { ...t, ...next }
    }))
  }, [activeTabId, setTabs])

  const setImages = useCallback((valueOrFn) => {
    updateActiveTab(t => ({
      images: typeof valueOrFn === 'function' ? valueOrFn(t.images) : valueOrFn,
    }))
  }, [updateActiveTab])

  const setSelectedIdx = useCallback((valueOrFn) => {
    updateActiveTab(t => ({
      selectedIdx: typeof valueOrFn === 'function' ? valueOrFn(t.selectedIdx) : valueOrFn,
    }))
  }, [updateActiveTab])

  const setSelectedGroupId = useCallback((valueOrFn) => {
    updateActiveTab(t => ({
      selectedGroupId: typeof valueOrFn === 'function' ? valueOrFn(t.selectedGroupId) : valueOrFn,
    }))
  }, [updateActiveTab])

  const setAnalyzing = useCallback((value) => {
    // `analyzing` is derived from status; map it back so legacy callers work.
    updateActiveTab(t => ({
      status: value ? 'analyzing' : (t.images.length > 0 ? 'ready' : (t.folderPath ? 'ready' : 'empty')),
    }))
  }, [updateActiveTab])

  const setAnalyzeResult = useCallback((value) => {
    updateActiveTab({ analyzeResult: value })
  }, [updateActiveTab])

  const setProgress = useCallback((value) => {
    updateActiveTab({ progress: value })
  }, [updateActiveTab])

  const setResultDismissed = useCallback((value) => {
    updateActiveTab({ resultDismissed: value })
  }, [updateActiveTab])

  // ── loadImages ───────────────────────────────────────────────────────────
  // Existing callers invoke loadImages() with no args; we use the active
  // tab's folder. If the active tab is empty (no folder bound), we no-op.
  const loadImages = useCallback(() => {
    const tab = tabs.find(t => t.id === activeTabId)
    if (!tab || !tab.folderPath) {
      return Promise.resolve()
    }
    const url = `${API}/images?source_folder=${encodeURIComponent(tab.folderPath)}`
    return fetch(url)
      .then(res => {
        if (!res.ok) throw new Error(`Server returned ${res.status}`)
        return res.json()
      })
      .then(data => {
        setTabs(prev => prev.map(t =>
          t.id === tab.id ? { ...t, images: data, loaded: true } : t
        ))
      })
  }, [tabs, activeTabId, setTabs])

  // ── Folder-restore on launch ─────────────────────────────────────────────
  // GET /folders + /watch → build initial tab list. Runs once on mount.
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch(`${API}/folders`).then(r => { if (!r.ok) throw new Error(`Server returned ${r.status}`); return r.json() }),
      fetch(`${API}/watch`).then(r => r.ok ? r.json() : { folder: null }).catch(() => ({ folder: null })),
    ])
      .then(([foldersData, watchData]) => {
        if (cancelled) return
        const folders = foldersData.folders || []
        const liveFolder = watchData.folder || null
        const restored = folders.map(f => ({
          ...makeReadyTab(f.source_folder),
          watchLive: f.source_folder === liveFolder,
        }))
        if (liveFolder && !folders.some(f => f.source_folder === liveFolder)) {
          restored.unshift({ ...makeReadyTab(liveFolder), watchLive: true })
        }
        const trailing = makeNewTab()
        const initialTabs = [...restored, trailing]
        setTabs(initialTabs)
        let savedFolder = null
        try { savedFolder = localStorage.getItem('pca.activeFolderPath') } catch {}
        const chosen = restored.find(t => t.folderPath === savedFolder)
        setActiveTabId(chosen?.id || restored[0]?.id || trailing.id)
      })
      .catch(err => setError(`Could not reach backend: ${err.message}`))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Persist the active tab's folder path so the next launch can re-select it.
  // We key by folder path because uuids regenerate on every restore.
  // Pulling activeTab from tabs here (not as a prop) avoids a circular dep.
  useEffect(() => {
    const activeTab = tabs.find(t => t.id === activeTabId)
    if (activeTab?.folderPath) {
      try { localStorage.setItem('pca.activeFolderPath', activeTab.folderPath) } catch {}
    }
  }, [activeTabId, tabs])

  // Make sure activeTabId always points at a real tab.
  useEffect(() => {
    if (!tabs.length) return
    if (!tabs.find(t => t.id === activeTabId)) {
      setActiveTabId(tabs[0].id)
    }
  }, [tabs, activeTabId, setActiveTabId])

  // Lazy-load images when the active tab is ready but hasn't been fetched yet.
  const activeTab = tabs.find(t => t.id === activeTabId) || tabs[0] || null
  useEffect(() => {
    if (!activeTab) return
    if (activeTab.status !== 'ready') return
    if (activeTab.loaded) return
    loadImages().catch(() => {})
  }, [activeTab, loadImages])

  // On-focus rescan: drop rows whose file_path no longer exists on disk.
  // Skip live tabs — the watcher's on_deleted handler covers them.
  useEffect(() => {
    if (!activeTab) return
    if (activeTab.watchLive) return
    if (activeTab.status !== 'ready') return
    if (!activeTab.folderPath) return
    let cancelled = false
    fetch(`${API}/sync-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_folder: activeTab.folderPath }),
    })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        if (cancelled) return
        if (data.images_removed > 0) {
          loadImages().catch(() => {})
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [activeTabId, activeTab?.watchLive, activeTab?.status, activeTab?.folderPath, loadImages])

  // On-focus resume check: count files in the folder that aren't yet analyzed.
  // If > 0 the tab gets `unfinishedCount` stamped on it so the UI can surface
  // a "Resume analysis" banner. Existing analyze flow already skips files
  // whose analysis_status is 'done', so clicking Resume is just re-POSTing
  // /analyze-folder — no special backend path needed. The check is cheap
  // (one filesystem walk + one indexed SQL query) so we run it on every
  // tab focus, not just initial mount. Re-runs after analysis completion
  // so a freshly-finished folder clears its banner.
  useEffect(() => {
    if (!activeTab) return
    if (activeTab.status !== 'ready') return
    if (!activeTab.folderPath) return
    let cancelled = false
    const include = activeTab.includeSubfolders ? '&include_subfolders=true' : ''
    fetch(`${API}/folders/unfinished?folder_path=${encodeURIComponent(activeTab.folderPath)}${include}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => {
        if (cancelled) return
        setTabs(prev => prev.map(t =>
          t.id === activeTab.id
            ? { ...t, unfinishedCount: data.unfinished || 0, totalOnDisk: data.total_on_disk || 0 }
            : t
        ))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [activeTabId, activeTab?.status, activeTab?.folderPath, activeTab?.includeSubfolders, activeTab?.loaded, setTabs])

  // ── Stop analysis ────────────────────────────────────────────────────────
  const [stopping, setStopping] = useState(false)
  const analyzing = activeTab?.status === 'analyzing'

  const stopAnalysis = useCallback(async () => {
    setStopping(true)
    await fetch(`${API}/stop-analysis`, { method: 'POST' }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!analyzing && stopping) setStopping(false)
  }, [analyzing, stopping])

  // ── runAnalysisForTab ────────────────────────────────────────────────────
  // Shared by the fresh-analyze path and the overwrite-confirm path.
  const [errorsExpanded, setErrorsExpanded] = useState(false)
  const [skippedExpanded, setSkippedExpanded] = useState(false)

  const runAnalysisForTab = useCallback(async (tabId, path, includeSubfolders = false) => {
    setTabs(prev => {
      const idx = prev.findIndex(t => t.id === tabId)
      if (idx === -1) return prev
      const target = prev[idx]
      const updated = { ...target, folderPath: path, status: 'analyzing',
        analyzeResult: null, resultDismissed: false, progress: null,
        loaded: false, images: [], includeSubfolders }
      const wasTrailingEmpty = idx === prev.length - 1 && target.status === 'empty'
      if (wasTrailingEmpty) {
        // The "+" button now sits left of the tab strip. New analyses should
        // appear immediately to the right of "+" (position 0), with a fresh
        // empty placeholder kept at the end for the + button's onTrailingClick.
        const others = prev.filter(t => t.id !== tabId)
        return [updated, ...others, makeNewTab()]
      }
      return prev.map(t => t.id === tabId ? updated : t)
    })
    setErrorsExpanded(false)
    setSkippedExpanded(false)

    try {
      const res = await fetch(`${API}/analyze-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: path, include_subfolders: includeSubfolders }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      // The batch runs in a daemon thread on the backend; /analyze-folder now
      // returns immediately with { status: "started", ... } instead of blocking
      // until the batch finishes. Keep the tab in 'analyzing' state — the
      // /analyze-progress polling hook will transition it to 'ready' once
      // running becomes false.
      //
      // Legacy path: if the backend ever returns a completed result (e.g.
      // { status: "done", analyzed: 0, ... } for empty folders), transition to
      // 'ready' immediately as before so the UI doesn't stall.
      if (data.status === 'started') {
        // Stay in 'analyzing'; polling drives the transition to 'ready'.
        // Stash the skipped list from the immediate response so the result
        // banner can show it once the batch finishes.
        setTabs(prev => prev.map(t => t.id === tabId
          ? { ...t, _pendingSkipped: data.skipped || [] }
          : t))
      } else {
        // Empty-folder shortcut or legacy synchronous response.
        setTabs(prev => prev.map(t => t.id === tabId
          ? { ...t, status: 'ready', analyzeResult: data, progress: null, loaded: false }
          : t))
      }
    } catch (err) {
      setTabs(prev => prev.map(t => t.id === tabId
        ? { ...t, status: 'error', analyzeResult: { error: err.message }, progress: null }
        : t))
    }
  }, [setTabs])

  // ── toggleWatchLive ──────────────────────────────────────────────────────
  const toggleWatchLive = useCallback(async (tabId) => {
    const target = tabs.find(t => t.id === tabId)
    if (!target || !target.folderPath) return
    const turningOff = target.watchLive

    try {
      if (turningOff) {
        const res = await fetch(`${API}/watch`, { method: 'DELETE' })
        if (!res.ok) throw new Error(`Server error ${res.status}`)
      } else {
        const res = await fetch(`${API}/watch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder_path: target.folderPath }),
        })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      }
    } catch (err) {
      addToast({ type: 'error', message: `Watch live failed: ${err.message}`, duration: 8000 })
      return
    }

    setTabs(prev => prev.map(t => ({
      ...t,
      watchLive: !turningOff && t.id === tabId,
    })))
  }, [tabs, addToast, setTabs])

  // ── Modal state for tab interactions ────────────────────────────────────
  const [closeTabRequest, setCloseTabRequest]   = useState(null)
  const [overwriteRequest, setOverwriteRequest] = useState(null)
  const [busyRequest, setBusyRequest]           = useState(null)
  // Subfolder picker — set when /has-subfolders comes back > 0 for the
  // user's chosen folder. Cleared when the user picks an option.
  const [subfolderRequest, setSubfolderRequest] = useState(null)

  // ── startNewAnalysis ─────────────────────────────────────────────────────
  // Called when the user clicks "+ New analysis". Opens the macOS Finder
  // picker, runs busy/overwrite guards, then drops into runAnalysisForTab.
  const startNewAnalysis = useCallback(async (tabId) => {
    setActiveTabId(tabId)
    try {
      const res = await fetch(`${API}/pick-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_path: '', prompt: 'Start analysis' }),
      })
      const data = await res.json()
      // Strip trailing slashes — backend uses Python's Path() which normalizes
      // them away. Without this the tab's folderPath ('/foo/bar/') won't equal
      // the backend's source_folder ('/foo/bar') and the progress poll's tab
      // matcher silently fails.
      const path = (data?.path || '').trim().replace(/\/+$/, '')
      if (!path) return

      const busyTab = tabs.find(t => t.status === 'analyzing')
      if (busyTab && busyTab.id !== tabId) {
        setBusyRequest({ tabId: busyTab.id, folderPath: busyTab.folderPath })
        return
      }
      const sameFolderTab = tabs.find(t => t.id !== tabId && t.folderPath === path)
      if (sameFolderTab) {
        setOverwriteRequest({ folderPath: path, existingTabId: sameFolderTab.id })
        return
      }

      // Check for subfolders containing photos. If any exist, surface a
      // picker so the user can opt into recursive analysis. The /has-
      // subfolders endpoint is cheap (early exit on first match per subdir).
      try {
        const sfRes = await fetch(`${API}/has-subfolders?folder_path=${encodeURIComponent(path)}`)
        if (sfRes.ok) {
          const sfData = await sfRes.json()
          if (sfData.has_subfolders) {
            setSubfolderRequest({ tabId, folderPath: path, count: sfData.count })
            return
          }
        }
      } catch { /* fall through to root-only analysis if probe fails */ }

      runAnalysisForTab(tabId, path)
    } catch (e) {
      addToast({ type: 'error', message: `Folder picker failed: ${e.message}` })
    }
  }, [tabs, runAnalysisForTab, addToast, setActiveTabId])

  // ── Subfolder picker resolution ──────────────────────────────────────────
  const resolveSubfolderRequest = useCallback((includeSubfolders) => {
    const req = subfolderRequest
    if (!req) return
    setSubfolderRequest(null)
    runAnalysisForTab(req.tabId, req.folderPath, includeSubfolders)
  }, [subfolderRequest, runAnalysisForTab])

  const dismissSubfolderRequest = useCallback(() => setSubfolderRequest(null), [])

  // ── confirmOverwrite ─────────────────────────────────────────────────────
  const confirmOverwrite = useCallback(async () => {
    const req = overwriteRequest
    if (!req) return
    setOverwriteRequest(null)
    try {
      await fetch(`${API}/clear-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_folder: req.folderPath }),
      })
    } catch (err) {
      addToast({ type: 'error', message: `Could not clear folder: ${err.message}`, duration: 8000 })
      return
    }
    setTabs(prev => {
      const stillExists = prev.find(t => t.id === req.existingTabId)
      if (!stillExists) return prev
      return prev.map(t => t.id === req.existingTabId
        ? { ...t, status: 'ready', images: [], analyzeResult: null, resultDismissed: false, progress: null, loaded: false }
        : t)
    })
    setActiveTabId(req.existingTabId)
    setTabs(prev => prev.map(t => t.status === 'empty' ? { ...t, folderPath: null } : t))
    runAnalysisForTab(req.existingTabId, req.folderPath)
  }, [overwriteRequest, addToast, setTabs, setActiveTabId, runAnalysisForTab])

  // ── requestCloseTab / confirmCloseTab ────────────────────────────────────
  const requestCloseTab = useCallback((tabId) => {
    const tab = tabs.find(t => t.id === tabId)
    if (!tab) return
    if (tab.status === 'analyzing') {
      addToast({ type: 'info', message: 'Stop analysis before closing this tab.' })
      return
    }
    if (tab.status === 'empty' && !tab.folderPath) {
      setTabs(prev => prev.filter(t => t.id !== tabId))
      return
    }
    setCloseTabRequest({ tabId })
  }, [tabs, addToast, setTabs])

  const confirmCloseTab = useCallback(async () => {
    const req = closeTabRequest
    if (!req) return
    setCloseTabRequest(null)
    const tab = tabs.find(t => t.id === req.tabId)
    if (!tab) return
    if (tab.watchLive) {
      try {
        await fetch(`${API}/watch`, { method: 'DELETE' })
      } catch (err) {
        addToast({ type: 'error', message: `Could not stop watch: ${err.message}`, duration: 8000 })
        return
      }
    }
    if (tab.folderPath) {
      try {
        await fetch(`${API}/clear-folder`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_folder: tab.folderPath }),
        })
      } catch (err) {
        addToast({ type: 'error', message: `Could not clear folder: ${err.message}`, duration: 8000 })
        return
      }
    }
    setTabs(prev => {
      const remaining = prev.filter(t => t.id !== req.tabId)
      const last = remaining[remaining.length - 1]
      if (!last || last.status !== 'empty') remaining.push(makeNewTab())
      return remaining
    })
    if (activeTabId === req.tabId) {
      setTabs(prev => {
        const fallback = prev[Math.max(0, prev.length - 2)] || prev[prev.length - 1]
        if (fallback) setActiveTabId(fallback.id)
        return prev
      })
    }
  }, [closeTabRequest, tabs, activeTabId, addToast, setTabs, setActiveTabId])

  // ── reorderTabs ──────────────────────────────────────────────────────────
  const reorderTabs = useCallback((sourceId, targetId) => {
    setTabs(prev => {
      const arr = [...prev]
      const fromIdx = arr.findIndex(t => t.id === sourceId)
      const toIdx   = arr.findIndex(t => t.id === targetId)
      if (fromIdx < 0 || toIdx < 0) return prev
      const trailingIdx = arr.length - 1
      if (fromIdx === trailingIdx || toIdx === trailingIdx) return prev
      const [moved] = arr.splice(fromIdx, 1)
      arr.splice(toIdx, 0, moved)
      return arr
    })
  }, [setTabs])

  // ── Watch live poll for errors ───────────────────────────────────────────
  // While Watch live is on, toast any new per-file errors from the ring buffer.
  const isLiveActive = activeTab?.watchLive === true
  const lastErrorTsRef = useRef(null)

  useEffect(() => {
    if (!isLiveActive) {
      lastErrorTsRef.current = null
      return
    }
    let primed = false
    const tick = () => {
      fetch(`${API}/debug/last-errors`)
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(data => {
          const errs = data.errors || []
          if (!primed) {
            lastErrorTsRef.current = errs.length ? errs[errs.length - 1].ts : null
            primed = true
            return
          }
          const cutoff = lastErrorTsRef.current
          const fresh = cutoff ? errs.filter(e => e.ts > cutoff) : errs
          if (fresh.length === 0) return
          lastErrorTsRef.current = fresh[fresh.length - 1].ts
          fresh.forEach(e => {
            addToast({ type: 'error', message: `Couldn't analyze ${e.file}: ${e.error}`, duration: 8000 })
          })
        })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 5000)
    return () => clearInterval(id)
  }, [isLiveActive, addToast])

  // Watch live image poll — refresh grid every 5s while active
  useEffect(() => {
    if (!isLiveActive) return
    const id = setInterval(() => loadImages().catch(() => {}), 5000)
    return () => clearInterval(id)
  }, [isLiveActive, loadImages])

  return {
    // App bootstrap state
    loading,
    error,
    // Derived-state setters
    updateActiveTab,
    setImages,
    setSelectedIdx,
    setSelectedGroupId,
    setAnalyzing,
    setAnalyzeResult,
    setProgress,
    setResultDismissed,
    // Image loading
    loadImages,
    // Analysis lifecycle
    runAnalysisForTab,
    stopAnalysis,
    stopping,
    setStopping,
    // Result inspector expand state (owned here, used in App JSX)
    errorsExpanded,
    setErrorsExpanded,
    skippedExpanded,
    setSkippedExpanded,
    // Watch live
    toggleWatchLive,
    isLiveActive,
    // New analysis flow
    startNewAnalysis,
    // Overwrite / busy modals
    overwriteRequest,
    setOverwriteRequest,
    confirmOverwrite,
    busyRequest,
    setBusyRequest,
    // Subfolder picker
    subfolderRequest,
    resolveSubfolderRequest,
    dismissSubfolderRequest,
    // Close tab
    closeTabRequest,
    setCloseTabRequest,
    requestCloseTab,
    confirmCloseTab,
    // Reorder
    reorderTabs,
  }
}
