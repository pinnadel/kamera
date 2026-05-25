import { Brain } from 'lucide-react'

// PersonalModelPanel — compact status bar showing model health, top features,
// validation accuracy, and a Train/Retrain button.

const STATUS_STYLES = {
  untrained:       { bg: 'bg-[#1b1c1e]',                       text: 'text-[#6a6b6c]', label: 'Not trained' },
  learning:        { bg: 'bg-[rgba(232,184,74,0.12)]',          text: 'text-[#E8B84A]', label: 'Learning'    },
  ready:           { bg: 'bg-[rgba(91,184,212,0.12)]',          text: 'text-[#5BB8D4]', label: 'Ready'       },
  underperforming: { bg: 'bg-[rgba(201,123,123,0.12)]',         text: 'text-[#C97B7B]', label: 'Under review' },
}

// Auto-cull only delegates to the personal model in the "ready" tier
// (≥50 samples AND validated to beat the threshold baseline). Below that
// the model still scores photos for the UI, but auto-cull falls back to
// the quality-threshold heuristic. Copy below explains that gate.
const STATUS_COPY = {
  untrained:       'Make 30 decisions to unlock the first training pass.',
  learning:        'Model is learning your taste — auto-cull is using quality thresholds until it crosses 50 samples.',
  ready:           'Model is driving auto-cull decisions.',
  underperforming: 'Model trained but not yet better than defaults — auto-cull is using quality thresholds.',
}

function marginColor(margin) {
  if (margin >= 0.05) return 'text-[#5BB8D4]'
  if (margin > 0)     return 'text-[#E8B84A]'
  return 'text-[#C97B7B]'
}

function marginLabel(margin) {
  const pp = (margin * 100).toFixed(0)
  return margin >= 0 ? `+${pp} pp` : `${pp} pp`
}

export function PersonalModelPanel({ modelInfo, onTrain, training }) {
  if (!modelInfo) return null

  const {
    ready,
    decided_count,
    min_decisions,
    training_size,
    trained_at,
    top_features,
    model_status,
    validation,
  } = modelInfo

  const status     = model_status || (ready ? 'ready' : 'untrained')
  const statusStyle = STATUS_STYLES[status] || STATUS_STYLES.untrained
  const hasEnough  = decided_count >= min_decisions
  const pct        = Math.min(100, Math.round((decided_count / min_decisions) * 100))
  const topNames   = (top_features || []).slice(0, 3).map(f => f.name.replace(/_/g, ' ')).join(' · ')
  const showValidation = ready && validation && validation.model_accuracy != null

  return (
    <div className="mb-4 flex flex-col gap-2 text-xs text-[#6a6b6c]">

      {/* Row 1: heading + status pill + train button */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[#f9f9f9] font-medium">Personal model</span>

        {/* Status pill */}
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusStyle.bg} ${statusStyle.text}`}>
          {statusStyle.label}
        </span>

        {/* Trained-on count + top signals (shown when model exists) */}
        {ready && (
          <>
            <span className="text-[#7B82C9]">trained on {training_size} decisions</span>
            {topNames && <span className="text-[#6a6b6c]">top signals: {topNames}</span>}
            {trained_at && <span className="text-[#9c9c9d]">{trained_at.slice(0, 10)}</span>}
          </>
        )}

        {/* Progress toward first train (untrained / learning before 20 decisions) */}
        {!ready && (
          <>
            <span className={hasEnough ? 'text-[#7DB89A]' : 'text-[#6a6b6c]'}>
              {decided_count}/{min_decisions} decisions
            </span>
            <div className="w-24 bg-[#1b1c1e] rounded-full h-1">
              <div
                className="h-1 rounded-full transition-all bg-[#7B82C9]"
                style={{ width: `${pct}%` }}
              />
            </div>
          </>
        )}

        {/* Train / Retrain button — pushed to the right */}
        <button
          onClick={onTrain}
          disabled={!hasEnough || training}
          className={`ml-auto inline-flex items-center gap-1.5 px-3 py-1 rounded text-xs transition-opacity ${
            hasEnough && !training
              ? 'border border-[rgba(232,184,74,0.50)] text-[#f9f9f9] bg-transparent hover:opacity-70'
              : 'text-[#6a6b6c] bg-[#101111] cursor-default'
          }`}
        >
          <Brain size={16} />
          {training ? 'Training…' : ready ? 'Retrain' : 'Train model'}
        </button>
      </div>

      {/* Row 2: one-liner status copy */}
      <p className="text-xs text-[#9c9c9d] mt-1 leading-relaxed">
        {STATUS_COPY[status]}
      </p>

      {/* Row 3: validation accuracy (only when model is ready and data exists) */}
      {showValidation && (
        <div className="flex items-center gap-4 mt-1">
          <span className="text-xs text-[#9c9c9d]">
            Model accuracy{' '}
            <span className="text-[#f9f9f9] font-mono">
              {(validation.model_accuracy * 100).toFixed(0)}%
            </span>
          </span>
          <span className="text-xs text-[#9c9c9d]">
            vs baseline{' '}
            <span className="text-[#f9f9f9] font-mono">
              {(validation.baseline_accuracy * 100).toFixed(0)}%
            </span>
          </span>
          <span className={`text-xs font-mono ${marginColor(validation.margin)}`}>
            {marginLabel(validation.margin)}
          </span>
        </div>
      )}

      {/* Row 4: underperforming warning */}
      {status === 'underperforming' && (
        <p className="text-xs text-[#E8B84A] mt-2 leading-relaxed">
          Auto-cull is using quality thresholds — the model hasn&apos;t beaten the baseline yet. Make more decisions to improve it.
        </p>
      )}
    </div>
  )
}
