// useMultiSelect — selection state for the grid and the loupe.
//
// Owns: { selected: Set<number>, isSelectMode: bool, anchorId: number | null }
//
// Each call site instantiates its own copy (the grid and the loupe should
// not share state — selecting photos in the loupe must not change what's
// selected in the grid behind it).
//
// Shift+click semantics
// ---------------------
// `extend(id, orderedIds)` selects every id in the contiguous range from
// `anchorId` to `id`, inclusive of both endpoints, against `orderedIds` —
// which is the list the call site considers "ordered" (the grid passes
// `displayGridItems`, the loupe passes `sortedImages`).
//
// The anchor does NOT advance on shift+click. Repeated shift+clicks keep
// extending from the same anchor, which matches Finder / Lightroom behaviour
// and is what users expect from "click A, scroll, shift+click Z" range
// selection.
//
// When `anchorId` is null at the moment of `extend` (the user hit shift+click
// without a prior single click), the hook falls back to behaving like
// `toggle(id)` so the action establishes the anchor instead of being a no-op.

import { useCallback, useRef, useState } from 'react'

export function useMultiSelect() {
  const [selected, setSelected] = useState(() => new Set())
  const [isSelectMode, setIsSelectMode] = useState(false)
  // Held in a ref because `extend` reads it inside a setSelected callback
  // and we don't want stale-closure surprises if multiple events fire in
  // quick succession.
  const anchorRef = useRef(null)

  const enter = useCallback(() => setIsSelectMode(true), [])

  const exit = useCallback(() => {
    setIsSelectMode(false)
    setSelected(new Set())
    anchorRef.current = null
  }, [])

  const clear = useCallback(() => {
    setSelected(new Set())
    anchorRef.current = null
  }, [])

  // Set the anchor without changing the selection set. Used by call sites
  // that have a notion of "focused but not selected" (e.g. the grid's bare-
  // click focus ring) so that a subsequent shift+click extends from the
  // focused tile rather than falling back to a plain toggle.
  const setAnchor = useCallback((id) => {
    anchorRef.current = id ?? null
  }, [])

  const toggle = useCallback((id) => {
    if (id == null) return
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
    // Anchor advances on every single-click toggle. Shift+click after this
    // will extend from here.
    anchorRef.current = id
  }, [])

  const extend = useCallback((id, orderedIds) => {
    if (id == null || !Array.isArray(orderedIds) || orderedIds.length === 0) {
      return
    }
    const anchor = anchorRef.current
    if (anchor == null || anchor === id) {
      // No anchor yet (or user shift+clicked the same tile they last clicked)
      // — fall back to a plain toggle, which establishes the anchor.
      toggle(id)
      return
    }
    // Find both endpoints in the ordered list. Either being missing means
    // the caller's `orderedIds` is out of sync with what the user clicked
    // (e.g. a filter changed between clicks). Treat as a fresh toggle.
    const anchorIdx = orderedIds.indexOf(anchor)
    const targetIdx = orderedIds.indexOf(id)
    if (anchorIdx === -1 || targetIdx === -1) {
      toggle(id)
      return
    }
    const lo = Math.min(anchorIdx, targetIdx)
    const hi = Math.max(anchorIdx, targetIdx)
    setSelected(prev => {
      const next = new Set(prev)
      for (let i = lo; i <= hi; i++) next.add(orderedIds[i])
      return next
    })
    // Anchor stays at its current value — repeated shift+clicks extend from
    // the same pivot, matching Finder / Lightroom. (Do not update anchorRef.)
  }, [toggle])

  return {
    selected,
    isSelectMode,
    enter,
    exit,
    toggle,
    extend,
    setAnchor,
    clear,
    size: selected.size,
  }
}
