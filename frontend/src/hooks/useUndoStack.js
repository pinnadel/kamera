// useUndoStack — app-global LIFO undo stack with a fixed depth.
//
// Each action (bulk K/M/R, single K/M/R, group mutation) pushes one entry.
// Pressing U / Cmd+Z anywhere pops the top entry and replays the inverse.
//
// Depth = 5: covers "I just did a thing wrong" without growing unbounded
// in batch sessions. Editor-style linear history — entries past the cursor
// are dropped when a new action lands (no redo branch).
//
// Entry shapes:
//   { kind: 'decision', items: [{id, prev, next}], label }
//     - prev: 'keep' | 'maybe' | 'reject' | null  (null = no prior decision)
//     - next: same enum (the decision that was applied)
//
//   { kind: 'group', assignments: [{id, prev_manual_group_id}], label }
//     - prev_manual_group_id is a uuid string OR null. Replay restores each
//       photo to its prior anchor via /set-manual-group mode=restore_assignments.

import { useCallback, useRef, useState } from 'react'

const MAX_DEPTH = 5

export function useUndoStack() {
  // useState only so consumers can render stack size. The actual ops mutate
  // a ref so they're stable across renders.
  const [size, setSize] = useState(0)
  const stackRef = useRef([])

  const push = useCallback((entry) => {
    if (!entry) return
    const next = [...stackRef.current, entry]
    while (next.length > MAX_DEPTH) next.shift()
    stackRef.current = next
    setSize(next.length)
  }, [])

  const pop = useCallback(() => {
    if (stackRef.current.length === 0) return null
    const next = stackRef.current.slice(0, -1)
    const entry = stackRef.current[stackRef.current.length - 1]
    stackRef.current = next
    setSize(next.length)
    return entry
  }, [])

  const clear = useCallback(() => {
    stackRef.current = []
    setSize(0)
  }, [])

  const peek = useCallback(() => {
    return stackRef.current[stackRef.current.length - 1] ?? null
  }, [])

  return { push, pop, clear, peek, size }
}
