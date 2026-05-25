// PullVisionModelButton — small action+progress component used in the LLM
// "no_models" nudges (Settings + DetailView + GroupLoupe). Wraps the
// usePullModel hook so the button and its state-aware copy live in one
// place — the three call sites just drop it into their existing layout
// and supply onDone (typically a refetch of /lm-status).
//
// Visual contract:
//   idle      → cyan "Pull qwen2.5vl now (≈6 GB)" button
//   pulling   → amber pulse dot + "Downloading… (takes a few minutes)"
//   done      → green check + "Installed and ready"
//   error     → coral hint + inline Retry link
//
// Why a button per surface vs. a single global panel:
//   The three surfaces have different chrome (full panel in Settings,
//   compact strip in DetailView, single-line chip in GroupLoupe). A shared
//   button keeps the action and copy aligned while letting each surface
//   own its own framing.

import { useEffect } from 'react'
import { Check } from 'lucide-react'
import { usePullModel } from '../hooks/usePullModel'

function _formatEta(seconds) {
  if (typeof seconds !== 'number' || !isFinite(seconds) || seconds <= 0) return null
  if (seconds < 60) return `${seconds}s`
  const m = Math.round(seconds / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return rem === 0 ? `${h}h` : `${h}h ${rem}m`
}

// Pick MB or GB to match the total's scale, so both numbers in "X of Y" use
// the same unit (so 0.4 of 0.6 GB or 380 of 600 MB, never mixed). One decimal
// when GB, none when MB — keeps the line compact without losing useful detail.
function _formatBytePair(currentMb, totalMb) {
  if (typeof currentMb !== 'number' || typeof totalMb !== 'number' || totalMb <= 0) return null
  if (totalMb >= 1024) {
    const cur = (currentMb / 1024).toFixed(1)
    const tot = (totalMb / 1024).toFixed(1)
    return `${cur} of ${tot} GB`
  }
  return `${Math.round(currentMb)} of ${Math.round(totalMb)} MB`
}

export function PullVisionModelButton({ onDone, compact = false }) {
  const { state, detail, progress, pull, reset } = usePullModel()

  // Notify parent when the pull finishes so it can refetch /lm-status.
  // We do this in an effect (not in pull()) so consumers can read the
  // settled state on the way to 'done' if they want to.
  useEffect(() => {
    if (state === 'done' && onDone) onDone()
  }, [state, onDone])

  if (state === 'pulling') {
    // Copy shape: "Downloading qwen2.5vl · 1.0 of 5.6 GB · ~25 min left".
    // Bytes-downloaded vs. total is the most meaningful information here —
    // the user can map it to their network speed at a glance. Percent was
    // redundant (X-of-Y already conveys progress) so it's dropped.
    // Compact variant (GroupLoupe top bar) keeps the same shape minus the
    // generic placeholder until bytes are flowing.
    const bytePair = progress ? _formatBytePair(progress.currentMb, progress.totalMb) : null
    const eta      = progress ? _formatEta(progress.etaSeconds) : null
    const segments = ['Downloading qwen2.5vl']
    if (bytePair) segments.push(bytePair)
    if (eta)      segments.push(`~${eta} left`)
    return (
      <div className="flex items-center gap-2 text-xs text-[#E8B84A]">
        <span className="w-1.5 h-1.5 rounded-full bg-[#E8B84A] animate-pulse shrink-0" />
        <span>
          {bytePair
            ? segments.join(' · ')
            : `Downloading qwen2.5vl…${!compact ? ' (≈6 GB, takes a few minutes)' : ''}`}
        </span>
      </div>
    )
  }
  if (state === 'done') {
    return (
      <div className="flex items-center gap-2 text-xs text-[#7DB89A]">
        <Check size={13} />
        <span>qwen2.5vl installed and ready</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <button
        onClick={() => pull('qwen2.5vl:7b')}
        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)] hover:opacity-70 transition-opacity"
      >
        {compact ? 'Pull qwen2.5vl' : 'Pull qwen2.5vl now (≈6 GB)'}
      </button>
      {state === 'error' && (
        <span className="text-xs text-[#C97B7B] flex items-center gap-1.5">
          {detail || 'Pull failed'}
          <button onClick={reset} className="underline hover:opacity-70">Retry</button>
        </span>
      )}
    </div>
  )
}
