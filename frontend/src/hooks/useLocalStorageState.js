// useLocalStorageState — like useState but persists to localStorage.
// Used by CollapsibleSection to remember which sections the user has expanded.

import { useEffect, useState } from 'react'

export function useLocalStorageState(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw == null ? initial : JSON.parse(raw)
    } catch { return initial }
  })
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* quota / disabled */ }
  }, [key, value])
  return [value, setValue]
}
