// ModelBanner — mobile version of PersonalModelBanner growth tiers.
// Lives at the top of Browse. Tap to open ModelStatusView.

import { Sparkles, ChevronRight } from 'lucide-react'

const TIERS = [
  { min: 0,   max: 30,  key: 'untrained',     label: 'Just getting started',     fill: 0.05 },
  { min: 30,  max: 50,  key: 'underperforming', label: 'Learning your taste',    fill: 0.20 },
  { min: 50,  max: 100, key: 'calibrating',   label: 'Calibrating',              fill: 0.35 },
  { min: 100, max: 200, key: 'knows-your-eye', label: 'Knows your eye',          fill: 0.55 },
  { min: 200, max: 500, key: 'your-curator',   label: 'Your personal curator',   fill: 0.80 },
  { min: 500, max: Infinity, key: 'deeply-attuned', label: 'Deeply attuned',     fill: 1.00 },
]

function tierFor(info) {
  const status = info?.model_status || (info?.ready ? 'ready' : 'untrained')
  const n = info?.training_size ?? info?.decided_count ?? 0
  if (status === 'ready') {
    for (const t of TIERS) if (n < t.max) return t
    return TIERS[TIERS.length - 1]
  }
  if (n < 30) return TIERS[0]
  return TIERS[1]
}

export function ModelBanner({ info, onOpen }) {
  if (!info) return null
  const tier = tierFor(info)
  const n = info.training_size ?? info.decided_count ?? 0
  const next = TIERS.find(t => t.min > tier.min)

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Personal taste model — ${tier.label}, ${n} decisions. Tap for details.`}
      className="ai-border w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#07080a]"
    >
      <div className="ai-border-inner rounded-[10.5px] p-3 flex items-center gap-3">
        <span className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-[rgba(91,184,212,0.10)] flex-shrink-0">
          <Sparkles size={18} className="text-[#5BB8D4]" aria-hidden="true" />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[11px] uppercase tracking-wide text-[#9c9c9d] font-medium">Your taste model</p>
          <p className="text-[15px] font-semibold text-[#f9f9f9] truncate">{tier.label}</p>
          <div className="mt-1.5 h-1 rounded-full bg-white/5 overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(100, tier.fill * 100)}%`,
                background: 'linear-gradient(90deg, #5BB8D4, #7B82C9)',
              }}
            />
          </div>
          <p className="mt-1 text-[12px] text-[#9c9c9d] m-tabular">
            {n} decisions{next ? ` · ${next.min - n} until ${next.label.toLowerCase()}` : ''}
          </p>
        </div>
        <ChevronRight size={18} className="text-[#9c9c9d] flex-shrink-0" aria-hidden="true" />
      </div>
    </button>
  )
}
