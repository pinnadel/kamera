// Keyboard shortcuts for App.jsx — grid mode only.
//
// All useHotkeys calls live here. The hook is purely additive (side-effects
// only) and returns nothing. Every dependency that a handler needs is passed
// in as a parameter — no global state is read directly.
//
// Composite-widget model: the grid is one focus region. Hotkeys here only
// fire when DOM focus is inside the grid (`gridRef.current.contains(active)`).
// Tab moves between regions (search → grid → pill bar → settings); arrow
// keys + K/M/R + Space/Enter act on the grid. Tabbing out hands keyboard
// control off to the surface that received focus; clicking a photo/group
// re-engages grid mode.
//
// Selection model: each grid cell carries a roving tabIndex (one cell at
// tabIndex=0, others at tabIndex=-1). Arrow keys walk `displayGridItems` —
// landing on a photo sets `selectedIdx`, landing on a group sets
// `selectedGroupId`. K/M/R only act on photos. Enter or Space on a focused
// group opens the GroupLoupe; Space on a focused photo toggles DetailView —
// unless 2+ photos are staged for comparison, in which case Space opens the
// Compare overlay (matches the floating "Compare N" button).
//
// When GroupLoupe is open, this hook backs off entirely — the loupe owns
// K/M/X/arrows/Enter/S/Z/Esc via its own useHotkeys calls (see GroupLoupe.jsx).

import { useHotkeys } from 'react-hotkeys-hook'

export function useKeyboard({
  activeView,
  detailOpen,
  setDetailOpen,
  images,
  selectedIdx,
  setSelectedIdx,
  selectedGroupId,
  setSelectedGroupId,
  displayGridItems,
  enterLoupe,
  sendDecision,
  setUserCols,
  undoImage,
  addToast,
  cols,
  gridRef,
  loupeOpen,
  multiSelectActive,
  bulkSendDecision,
  runUndo,
  amendLastDecision,
  registerDecisionIntent,
  toggleFilenames,
}) {
  // ── Helpers ──────────────────────────────────────────────────────────────

  // True when DOM focus is inside the grid container — gates every grid
  // hotkey so the shortcuts behave like a true composite widget.
  const gridHasFocus = () =>
    !!gridRef?.current && gridRef.current.contains(document.activeElement)

  // Resolve the index of the currently-focused cell inside displayGridItems.
  // Returns -1 when the focused item isn't visible (filter excluded it, list
  // is empty, etc.) — callers fall back to landing on the first visible item.
  const findCurrentIdx = () => {
    if (selectedGroupId != null) {
      return displayGridItems.findIndex(
        item => item.type === 'group' && item.group.best_image_id === selectedGroupId
      )
    }
    const photo = images[selectedIdx]
    if (!photo) return -1
    return displayGridItems.findIndex(
      item => item.type === 'image' && item.image.id === photo.id
    )
  }

  // Apply selection to whatever item sits at `targetIdx`. Photo → set
  // selectedIdx + clear group cursor. Group → set selectedGroupId. Out-of-
  // bounds is a no-op (don't wrap; the user can see they're at an edge).
  const focusItemAt = (targetIdx) => {
    if (targetIdx < 0 || targetIdx >= displayGridItems.length) return
    const item = displayGridItems[targetIdx]
    if (item.type === 'group') {
      setSelectedGroupId(item.group.best_image_id)
    } else {
      setSelectedGroupId(null)
      const i = images.findIndex(img => img.id === item.image.id)
      if (i >= 0) setSelectedIdx(i)
    }
  }

  // ── Arrow navigation ─────────────────────────────────────────────────────

  useHotkeys('arrowRight', () => {
    if (loupeOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    focusItemAt(cur + 1)
  }, [loupeOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId])

  useHotkeys('arrowLeft', () => {
    if (loupeOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    focusItemAt(cur - 1)
  }, [loupeOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId])

  useHotkeys('arrowDown', (e) => {
    if (loupeOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    e.preventDefault()
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    const target = Math.min(cur + cols(), displayGridItems.length - 1)
    focusItemAt(target)
  }, [loupeOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, cols])

  useHotkeys('arrowUp', (e) => {
    if (loupeOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    e.preventDefault()
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    const target = Math.max(cur - cols(), 0)
    focusItemAt(target)
  }, [loupeOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, cols])

  // ── Decision keys (K / R / M) ────────────────────────────────────────────
  // No-op when a group tile is focused — per-photo decisions live inside the
  // loupe. Also gated on grid focus so K from the search bar etc. is inert.

  // Decision keys take the bulk path when a multi-selection is active —
  // K/M/R applies to every selected photo and the selection stays in place.
  // Selecting a *group* tile in single mode still vetoes (group has no
  // single decision); bulk mode ignores that gate since you can't select
  // groups, only photos.
  // DetailView owns K/M/R while open (see DetailView.jsx — decideFromDetail
  // adds group-focused vetoes the global handler doesn't have). Without this
  // gate both handlers fire on the same keypress → two POST /decision races
  // against the same image_id; the loser fails with "Source file not found"
  // because the winner already moved the file.
  // Decide-or-amend: within ~400ms of the last K/M/R, a second decision key
  // amends the previous photo(s) instead of acting on the current cursor.
  // amendLastDecision returns true when it consumed the press; false → fall
  // through to the normal decision path below. See App.jsx::amendLastDecision.
  //
  // registerDecisionIntent() is called SYNCHRONOUSLY before awaiting amend —
  // this stamps the keypress time so that if THIS press becomes the "first"
  // of a future double-press, the next press can amend us. Without this, the
  // intent would only be stamped after the network round-trip, racing with
  // the user's second keypress.
  const decideOrAmend = async (decision) => {
    const amendPromise = amendLastDecision ? amendLastDecision(decision) : Promise.resolve(false)
    registerDecisionIntent?.()
    const amended = await amendPromise
    if (amended) return
    if (multiSelectActive) { bulkSendDecision?.(decision); return }
    if (selectedGroupId != null) return
    sendDecision(decision)
  }

  useHotkeys('k', () => {
    if (loupeOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('keep')
  }, [loupeOpen, detailOpen, activeView, selectedGroupId, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  useHotkeys('r', () => {
    if (loupeOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('reject')
  }, [loupeOpen, detailOpen, activeView, selectedGroupId, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  useHotkeys('m', () => {
    if (loupeOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('maybe')
  }, [loupeOpen, detailOpen, activeView, selectedGroupId, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  // ── Open / detail / escape ───────────────────────────────────────────────
  // Space and Enter both open the focused thing: a group → GroupLoupe, a
  // photo → DetailView. Symmetric "open this cell" gesture.

  useHotkeys('space', (e) => {
    if (loupeOpen) return
    if (activeView !== 'grid') return
    if (!gridHasFocus()) return
    e.preventDefault()
    if (selectedGroupId != null) {
      enterLoupe?.(selectedGroupId)
      return
    }
    setDetailOpen(o => !o)
  }, [loupeOpen, activeView, selectedGroupId, enterLoupe])

  useHotkeys('enter', (e) => {
    if (loupeOpen) return
    if (activeView !== 'grid') return
    if (detailOpen) return
    if (!gridHasFocus()) return
    if (selectedGroupId == null) return
    e.preventDefault()
    enterLoupe?.(selectedGroupId)
  }, [loupeOpen, activeView, detailOpen, selectedGroupId, enterLoupe])

  useHotkeys('escape', () => {
    if (detailOpen) { setDetailOpen(false); return }
    if (loupeOpen) return
  }, [loupeOpen, detailOpen])

  // ── Undo (Cmd+Z / U) ─────────────────────────────────────────────────────
  //
  // Per-photo undo: reverses the decision of whichever photo is currently
  // selected in the grid. GroupLoupe and DetailView bind their own U/Cmd+Z
  // against the photo they have focused. Silent no-op when the selected
  // cell is a group (group has no single decision to revert) or when the
  // photo has no decision yet.

  // U / Cmd+Z pops the app-global undo stack (last K/M/R or group action,
  // single or bulk, from any surface). When the stack is empty, fall back
  // to per-photo undo for whatever's currently focused — preserves the
  // "I just decided this photo, take it back" gesture for users who
  // didn't realise there's a stack now.
  const runUndoGated = async () => {
    if (loupeOpen || activeView !== 'grid') return
    if (runUndo) {
      const handled = await runUndo()
      if (handled) return
    }
    if (selectedGroupId != null) return
    const img = images[selectedIdx]
    if (!img) return
    undoImage(img.id)
  }

  useHotkeys('meta+z', runUndoGated, [loupeOpen, activeView, selectedGroupId, images, selectedIdx, undoImage, runUndo])
  useHotkeys('u',      runUndoGated, [loupeOpen, activeView, selectedGroupId, images, selectedIdx, undoImage, runUndo])

  // ── Thumbnail size (= / -) ───────────────────────────────────────────────

  useHotkeys('=', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    setUserCols(c => Math.max(2, (c ?? 6) - 1))
  }, [loupeOpen, activeView])

  useHotkeys('-', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    setUserCols(c => Math.min(8, (c ?? 6) + 1))
  }, [loupeOpen, activeView])

  // ── Filename visibility (F) ──────────────────────────────────────────────
  // Toggles the filename row under every grid tile. Grid-only, like the
  // thumbnail-size keys. Not gated on grid focus: it's a view preference the
  // user expects to flip from anywhere in the grid view, same as = / -.
  useHotkeys('f', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    toggleFilenames?.()
  }, [loupeOpen, activeView, toggleFilenames])
}
