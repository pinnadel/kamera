// ModelCard — hero block at the top of the dashboard.
//
// Three states (matches PersonalModelPanel's status framing):
//   - untrained: <30 decisions  → progress bar + copy
//   - learning:  ≥30 decisions, but model not yet ready → progress + spinner-ish
//   - ready:     model trained and active → top features + validation
//
// Cyan is reserved for state, not fills (per design system). The progress
// bar uses Cool Cyan #5BB8D4 only when it's the "fill towards readiness"
// signal; otherwise neutral.

const STATUS_LABELS = {
  untrained:       'Not trained yet',
  learning:        'Learning your taste',
  ready:           'Active',
  underperforming: 'Trained · Under review',
}

export function ModelCard({ data }) {
  if (!data) return <Skeleton />

  const {
    ready,
    decided_count = 0,
    min_decisions = 30,
    training_size = 0,
    trained_at,
    top_features = [],
    model_status,
    validation,
  } = data

  const status   = model_status || (ready ? 'ready' : 'untrained')
  const pct      = Math.min(100, Math.round((decided_count / min_decisions) * 100))
  const topFive  = top_features.slice(0, 5)
  const showVal  = ready && validation && validation.model_accuracy != null

  return (
    <section className="rounded-xl border border-[#2a2b2d] bg-[#101111] p-6">
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-base font-semibold text-[#f9f9f9]">Personal model</h2>
        <span className="text-xs text-[#9c9c9d]">{STATUS_LABELS[status]}</span>
      </div>

      {!ready ? (
        <>
          <div className="flex items-baseline gap-2 mb-2">
            <span className="font-mono text-2xl text-[#f9f9f9]">{decided_count}</span>
            <span className="text-sm text-[#9c9c9d]">/ {min_decisions} decisions</span>
          </div>
          <div className="w-full bg-[#1b1c1e] rounded-full h-1 mb-3">
            <div
              className="h-1 rounded-full transition-all bg-[#5BB8D4]"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-xs text-[#9c9c9d] leading-relaxed">
            Decide {Math.max(0, min_decisions - decided_count)} more photos to train your first personal model.
            All decisions are kept forever — clearing analysis tabs doesn&apos;t reset this.
          </p>
        </>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-baseline gap-6 flex-wrap">
            <span className="text-xs text-[#9c9c9d]">
              Trained on{' '}
              <span className="font-mono text-[#f9f9f9]">{training_size}</span>
              {' '}decisions
            </span>
            {trained_at && (
              <span className="text-xs text-[#9c9c9d]">
                Last trained{' '}
                <span className="font-mono text-[#f9f9f9]">{trained_at.slice(0, 10)}</span>
              </span>
            )}
            {showVal && (
              <span className="text-xs text-[#9c9c9d]">
                Accuracy{' '}
                <span className="font-mono text-[#f9f9f9]">
                  {(validation.model_accuracy * 100).toFixed(0)}%
                </span>
                {' '}vs baseline{' '}
                <span className="font-mono text-[#f9f9f9]">
                  {(validation.baseline_accuracy * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>

          {topFive.length > 0 && (
            <div>
              <div className="text-xs text-[#9c9c9d] mb-2">Top signals driving your model</div>
              <div className="flex flex-col gap-1.5">
                {topFive.map((f) => (
                  <FeatureBar key={f.name} name={f.name} weight={f.importance} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function FeatureBar({ name, weight }) {
  const pct = Math.round(Math.min(1, Math.max(0, weight)) * 100)
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-[#cecece] flex-1 truncate">
        {name.replace(/_/g, ' ')}
      </span>
      <div className="w-32 bg-[#1b1c1e] rounded-[3px] h-1.5">
        <div className="bg-[#cecece] h-1.5 rounded-[3px]" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-[#9c9c9d] w-8 text-right">{pct}%</span>
    </div>
  )
}

function Skeleton() {
  return (
    <section className="rounded-xl border border-[#2a2b2d] bg-[#101111] p-6">
      <div className="h-4 w-32 bg-[#1b1c1e] rounded mb-4" />
      <div className="h-8 w-24 bg-[#1b1c1e] rounded mb-3" />
      <div className="h-1 w-full bg-[#1b1c1e] rounded" />
    </section>
  )
}
