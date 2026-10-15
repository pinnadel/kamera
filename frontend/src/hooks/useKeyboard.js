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
// landing on a photo sets `selectedIdx`, landing on an undecided group sets
// `selectedGroupId`, landing on a collapsed burst in a decision view
// (Keeps/Maybes/Rejects) sets `selectedDecidedGroupKey`. K/M/R only act on
// photos — neither group kind has a single decision to apply. Enter or Space
// on a focused undecided group opens GroupLoupe; on a focused decided group it
// opens DetailView carrying the burst (already-decided members, nothing to
// rank); Space on a focused photo toggles DetailView — unless 2+ photos are staged for
// comparison, in which case Space opens the Compare overlay (matches the
// floating "Compare N" button).
//
// When GroupLoupe is open, this hook backs off entirely — the loupe owns
// K/M/X/arrows/Enter/S/Z/Esc via its own useHotkeys calls (see GroupLoupe.jsx).
//
// Same for the group hover-scrub panel (`scrubOpen`): while it's on screen it
// owns arrows / K/M/R / U / Space / Enter / Esc against the frame IT is
// showing (ui/GroupScrubPreview.jsx). Hovering a group tile is an explicit
// "I mean this burst" — the grid's own cursor, which may be parked anywhere,
// must not answer the same keypress as well.

import { useHotkeys } from 'react-hotkeys-hook'

export function useKeyboard({
  activeView,
  detailOpen,
  setDetailOpen,
  closeDetail,
  images,
  selectedIdx,
  setSelectedIdx,
  selectedGroupId,
  setSelectedGroupId,
  selectedDecidedGroupKey,
  setSelectedDecidedGroupKey,
  displayGridItems,
  enterLoupe,
  openDecidedGroupDetail,
  sendDecision,
  setUserCols,
  undoImage,
  addToast,
  cols,
  gridRef,
  loupeOpen,
  scrubOpen,
  multiSelectActive,
  bulkSendDecision,
  bulkSendUndo,
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
    if (selectedDecidedGroupKey != null) {
      return displayGridItems.findIndex(
        item => item.type === 'decidedGroup' && item.groupKey === selectedDecidedGroupKey
      )
    }
    const photo = images[selectedIdx]
    if (!photo) return -1
    return displayGridItems.findIndex(
      item => item.type === 'image' && item.image.id === photo.id
    )
  }

  // Apply selection to whatever item sits at `targetIdx`. Photo → set
  // selectedIdx + clear both group cursors. Undecided group → set
  // selectedGroupId. Collapsed decided group (Maybe/Reject) → set
  // selectedDecidedGroupKey. Out-of-bounds is a no-op (don't wrap; the user
  // can see they're at an edge).
  const focusItemAt = (targetIdx) => {
    if (targetIdx < 0 || targetIdx >= displayGridItems.length) return
    const item = displayGridItems[targetIdx]
    if (item.type === 'group') {
      setSelectedDecidedGroupKey(null)
      setSelectedGroupId(item.group.best_image_id)
    } else if (item.type === 'decidedGroup') {
      setSelectedGroupId(null)
      setSelectedDecidedGroupKey(item.groupKey)
    } else {
      setSelectedGroupId(null)
      setSelectedDecidedGroupKey(null)
      const i = images.findIndex(img => img.id === item.image.id)
      if (i >= 0) setSelectedIdx(i)
    }
  }

  // ── Arrow navigation ─────────────────────────────────────────────────────

  useHotkeys('arrowRight', () => {
    if (loupeOpen || scrubOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    focusItemAt(cur + 1)
  }, [loupeOpen, scrubOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, selectedDecidedGroupKey])

  useHotkeys('arrowLeft', () => {
    if (loupeOpen || scrubOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    focusItemAt(cur - 1)
  }, [loupeOpen, scrubOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, selectedDecidedGroupKey])

  useHotkeys('arrowDown', (e) => {
    if (loupeOpen || scrubOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    e.preventDefault()
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    const target = Math.min(cur + cols(), displayGridItems.length - 1)
    focusItemAt(target)
  }, [loupeOpen, scrubOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, selectedDecidedGroupKey, cols])

  useHotkeys('arrowUp', (e) => {
    if (loupeOpen || scrubOpen || activeView !== 'grid' || detailOpen) return
    if (!gridHasFocus()) return
    e.preventDefault()
    const cur = findCurrentIdx()
    if (cur < 0) { focusItemAt(0); return }
    const target = Math.max(cur - cols(), 0)
    focusItemAt(target)
  }, [loupeOpen, scrubOpen, activeView, detailOpen, displayGridItems, images, selectedIdx, selectedGroupId, selectedDecidedGroupKey, cols])

  // ── Decision keys (K / R / M) ────────────────────────────────────────────
  // No-op when a group tile (undecided OR a collapsed decided group) is
  // focused — neither has a single photo to decide on; re-culling members of
  // a decided group happens in the peek panel, DetailView or the bulk-action
  // bar, not K/M/R. Also gated on grid focus so K from the search bar etc. is inert.

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
    if (selectedGroupId != null || selectedDecidedGroupKey != null) return
    sendDecision(decision)
  }

  useHotkeys('k', () => {
    if (loupeOpen || scrubOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('keep')
  }, [loupeOpen, scrubOpen, detailOpen, activeView, selectedGroupId, selectedDecidedGroupKey, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  useHotkeys('r', () => {
    if (loupeOpen || scrubOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('reject')
  }, [loupeOpen, scrubOpen, detailOpen, activeView, selectedGroupId, selectedDecidedGroupKey, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  useHotkeys('m', () => {
    if (loupeOpen || scrubOpen || detailOpen || activeView !== 'grid') return
    if (!gridHasFocus()) return
    decideOrAmend('maybe')
  }, [loupeOpen, scrubOpen, detailOpen, activeView, selectedGroupId, selectedDecidedGroupKey, sendDecision, multiSelectActive, bulkSendDecision, amendLastDecision])

  // ── Open / detail / escape ───────────────────────────────────────────────
  // Space and Enter both open the focused thing: an undecided group →
  // GroupLoupe, a collapsed decided group → DetailView carrying the burst, a
  // photo → DetailView. Symmetric "open this cell" gesture.

  useHotkeys('space', (e) => {
    if (loupeOpen || scrubOpen) return
    if (activeView !== 'grid') return
    // Closing DetailView is NOT gated on grid focus — same as escape below.
    // DetailView is a full-screen surface, so whatever holds focus behind it
    // is irrelevant, and a scrub-opened one has a panel to hand back to.
    if (detailOpen) { e.preventDefault(); closeDetail(); return }
    if (!gridHasFocus()) return
    e.preventDefault()
    if (selectedGroupId != null) {
      enterLoupe?.(selectedGroupId)
      return
    }
    if (selectedDecidedGroupKey != null) {
      openDecidedGroupDetail?.(selectedDecidedGroupKey)
      return
    }
    setDetailOpen(true)
  }, [loupeOpen, scrubOpen, activeView, detailOpen, closeDetail, selectedGroupId, selectedDecidedGroupKey, enterLoupe, openDecidedGroupDetail])

  useHotkeys('enter', (e) => {
    if (loupeOpen || scrubOpen) return
    if (activeView !== 'grid') return
    if (detailOpen) return
    if (!gridHasFocus()) return
    if (selectedGroupId != null) {
      e.preventDefault()
      enterLoupe?.(selectedGroupId)
      return
    }
    if (selectedDecidedGroupKey != null) {
      e.preventDefault()
      openDecidedGroupDetail?.(selectedDecidedGroupKey)
    }
  }, [loupeOpen, scrubOpen, activeView, detailOpen, selectedGroupId, selectedDecidedGroupKey, enterLoupe, openDecidedGroupDetail])

  useHotkeys('escape', () => {
    if (scrubOpen) return
    if (detailOpen) { closeDetail(); return }
    if (loupeOpen) return
  }, [loupeOpen, scrubOpen, detailOpen, closeDetail])

  // ── Undo (Cmd+Z / U) ─────────────────────────────────────────────────────
  //
  // Per-photo undo: reverses the decision of whichever photo is currently
  // selected in the grid. GroupLoupe and DetailView bind their own U/Cmd+Z
  // against the photo they have focused. Silent no-op when the selected
  // cell is a group (group has no single decision to revert) or when the
  // photo has no decision yet.

  // Priority order mirrors decideOrAmend so U/Cmd+Z stays consistent with
  // K/M/R:
  //  1. Multi-selection active → revert EVERY selected photo to undecided.
  //     This is an explicit "these photos" instruction and must win over the
  //     stack, exactly as bulk K/M/R wins over the single-photo path.
  //  2. Otherwise pop the app-global undo stack (last K/M/R or group action).
  //  3. Stack empty → per-photo undo for whatever's currently focused,
  //     preserving the "I just decided this photo, take it back" gesture.
  const runUndoGated = async () => {
    if (loupeOpen || scrubOpen || activeView !== 'grid') return
    if (multiSelectActive) { bulkSendUndo?.(); return }
    if (runUndo) {
      const handled = await runUndo()
      if (handled) return
    }
    if (selectedGroupId != null || selectedDecidedGroupKey != null) return
    const img = images[selectedIdx]
    if (!img) return
    undoImage(img.id)
  }

  useHotkeys('meta+z', runUndoGated, [loupeOpen, scrubOpen, activeView, selectedGroupId, selectedDecidedGroupKey, images, selectedIdx, undoImage, runUndo, multiSelectActive, bulkSendUndo])
  useHotkeys('u',      runUndoGated, [loupeOpen, scrubOpen, activeView, selectedGroupId, selectedDecidedGroupKey, images, selectedIdx, undoImage, runUndo, multiSelectActive, bulkSendUndo])

  // ── Thumbnail size (= / -) ───────────────────────────────────────────────

  useHotkeys('=', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    setUserCols(c => Math.max(2, (c ?? 6) - 1))
  }, [loupeOpen, scrubOpen, activeView])

  useHotkeys('-', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    setUserCols(c => Math.min(8, (c ?? 6) + 1))
  }, [loupeOpen, scrubOpen, activeView])

  // ── Filename visibility (F) ──────────────────────────────────────────────
  // Toggles the filename row under every grid tile. Grid-only, like the
  // thumbnail-size keys. Not gated on grid focus: it's a view preference the
  // user expects to flip from anywhere in the grid view, same as = / -.
  useHotkeys('f', (e) => {
    if (loupeOpen || activeView !== 'grid') return
    e.preventDefault()
    toggleFilenames?.()
  }, [loupeOpen, scrubOpen, activeView, toggleFilenames])
}
