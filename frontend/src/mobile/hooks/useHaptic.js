// Haptic feedback wrapper. Uses the Vibration API where available; silently
// no-ops everywhere else. iOS Safari does NOT implement navigator.vibrate;
// the user gets visual + audio feedback only there. That's acceptable —
// haptics are a delight, not a load-bearing affordance.
//
// Usage:
//   const haptic = useHaptic()
//   haptic('light')   // 8ms
//   haptic('medium')  // 14ms
//   haptic('success') // light double-tap pattern
//   haptic('warning') // medium-pause-medium

const PATTERNS = {
  light:   [8],
  medium:  [14],
  heavy:   [22],
  success: [10, 40, 10],
  warning: [14, 60, 14],
  error:   [22, 30, 22, 30, 22],
}

export function useHaptic() {
  return (kind = 'light') => {
    try {
      if (typeof navigator === 'undefined') return
      if (typeof navigator.vibrate !== 'function') return
      navigator.vibrate(PATTERNS[kind] || PATTERNS.light)
    } catch {
      // Some browsers throw if user hasn't interacted yet. Swallow.
    }
  }
}
