import { useEffect, useState, useRef } from 'react'

// Reveal-on-scroll-up header pattern. Returns `true` when the header should be
// hidden (translated up out of view). The header reveals immediately on any
// upward scroll, no matter how far down the user has scrolled, so they can
// reach the tab bar / settings without scrolling all the way to the top.
//
// Tunables:
//   threshold — pixels from top before hiding kicks in. Below this, header
//               always shows so the empty-page top-of-document feel is intact.
//   delta     — minimum px of movement to count as a direction change. Filters
//               out trackpad jitter / rubber-banding.
export function useHideOnScroll({ threshold = 80, delta = 6 } = {}) {
  const [hidden, setHidden] = useState(false)
  const lastY = useRef(0)
  const ticking = useRef(false)

  useEffect(() => {
    lastY.current = window.scrollY

    const onScroll = () => {
      if (ticking.current) return
      ticking.current = true
      window.requestAnimationFrame(() => {
        const y = window.scrollY
        const dy = y - lastY.current
        if (Math.abs(dy) >= delta) {
          if (dy > 0 && y > threshold) setHidden(true)
          else if (dy < 0) setHidden(false)
          lastY.current = y
        } else if (y <= threshold) {
          setHidden(false)
          lastY.current = y
        }
        ticking.current = false
      })
    }

    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [threshold, delta])

  return hidden
}
