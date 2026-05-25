import { useEffect, useState } from 'react'

// Reactive `prefers-reduced-motion`. Some animations get fully disabled (the
// AI border, spring-animated sheets); others fall back to a static end state.
export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.('change', onChange) || mq.addListener?.(onChange)
    return () => {
      mq.removeEventListener?.('change', onChange) || mq.removeListener?.(onChange)
    }
  }, [])
  return reduced
}
