import { Check, CircleDot, Undo2 } from 'lucide-react'
import { API } from '../api'
import { BTN_SECONDARY, BTN_DANGER } from '../ui/buttons'
import { DecisionWord } from '../ui/primitives'

export function ConflictModal({ conflict, onResolve }) {
  if (!conflict) return null

  const { image, previous, latest } = conflict
  const decisionLabel = d => d === 'keep' ? 'Keep' : d === 'reject' ? 'Reject' : 'Maybe'

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(7,8,10,0.80)]">
      <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-2xl p-6 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10),inset_0_1px_0_rgba(255,255,255,0.05)] max-w-xs w-full flex flex-col gap-4">
        {/* Thumbnail */}
        <div className="bg-[#07080a] rounded-lg overflow-hidden h-36 flex items-center justify-center">
          <img
            src={`${API}/previews/${image.id}`}
            alt={image.filename}
            className="max-h-full max-w-full object-contain"
          />
        </div>

        <div>
          <p className="text-sm text-[#f9f9f9] text-center font-medium mb-1">
            Changed your mind?
          </p>
          <p className="text-xs text-[#cecece] text-center leading-relaxed">
            You marked this{' '}
            <DecisionWord kind={previous} weight="bold">{decisionLabel(previous)}</DecisionWord>
            {' '}last time — now{' '}
            <DecisionWord kind={latest} weight="bold">{decisionLabel(latest)}</DecisionWord>.
            {' '}Which feels more accurate?
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <button
            onClick={() => onResolve(latest)}
            className={BTN_SECONDARY + ' justify-center w-full'}
          >
            <Check size={14} /> Keep latest — <DecisionWord kind={latest}>{decisionLabel(latest)}</DecisionWord>
          </button>
          <button
            onClick={() => onResolve(previous)}
            className={BTN_SECONDARY + ' justify-center w-full'}
          >
            <Undo2 size={16} /> Stick with original — <DecisionWord kind={previous}>{decisionLabel(previous)}</DecisionWord>
          </button>
          <button
            onClick={() => onResolve('maybe')}
            className={BTN_DANGER + ' justify-center w-full'}
          >
            <CircleDot size={16} /> Mark as <DecisionWord kind="maybe">Maybe</DecisionWord> — I'm unsure
          </button>
        </div>
      </div>
    </div>
  )
}
