// DecisionBar — Reject / Maybe / Keep tap targets, sized for thumbs.
// Each button is min 64pt tall × 33% width with icon + label.
// Spatial convention matches Tinder / Lightroom Mobile: Reject left, Keep right.
//
// `current` highlights the currently-applied decision so the user can see
// at a glance what state the photo is in. Tap a different button to change;
// tap the same button to clear (resets to undecided).

import { Heart, HelpCircle, Trash2 } from 'lucide-react'

const KINDS = [
  { kind: 'reject', label: 'Reject', Icon: Trash2 },
  { kind: 'maybe',  label: 'Maybe',  Icon: HelpCircle },
  { kind: 'keep',   label: 'Keep',   Icon: Heart },
]

export function DecisionBar({ current, onDecide, disabled }) {
  return (
    <div
      className="fixed left-0 right-0 z-30 px-4 pb-3"
      style={{
        // Sit just above the home-indicator safe area. CullView, TrainView,
        // and GroupView don't render BottomNav while culling — the decision
        // bar IS the persistent bottom chrome.
        bottom: 'var(--safe-bottom)',
      }}
    >
      <div
        className="flex gap-3 py-3 m-blur-surface rounded-3xl border border-white/5 px-3"
        role="group"
        aria-label="Decide on this photo"
      >
        {KINDS.map(({ kind, label, Icon }) => {
          const isActive = current === kind
          return (
            <button
              key={kind}
              type="button"
              data-kind={kind}
              data-active={isActive}
              disabled={disabled}
              onClick={() => onDecide(kind)}
              aria-label={isActive ? `${label} (currently selected)` : label}
              aria-pressed={isActive}
              className="m-decision-btn flex-1 disabled:opacity-50"
            >
              <Icon size={20} aria-hidden="true" strokeWidth={2.2} />
              <span>{label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
