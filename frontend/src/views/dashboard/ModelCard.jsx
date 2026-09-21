// ModelCard — the state of one corpus's taste model.
//
// Three states (matches PersonalModelPanel's status framing):
//   - untrained: below the corpus floor → progress toward the first fit
//   - learning:  trained, but under the 50-sample validation gate
//   - ready:     trained, validated, actively used by auto-cull
//
// The headline number lives in the KPI row above; this card carries what the
// KPI can't: which signals the model actually leans on. Feature importances
// are the one thing here that is genuinely explanatory rather than a status.
//
// Cyan stays reserved for state, never fill (docs/DESIGN.md) — the progress
// bar uses it because "fill toward readiness" IS the state signal. Feature
// bars are neutral Mid Gray, like every other score bar in the app.

import { Card, StatBar, Empty } from './primitives'

const STATUS_LABELS = {
  untrained:       'Not trained yet',
  learning:        'Learning your taste',
  ready:           'Active',
  underperforming: 'Trained · under review',
}

const STATUS_TONE = {
  untrained:       'text-[#8a8b8c]',
  learning:        'text-[#5BB8D4]',
  ready:           'text-[#7DB89A]',
  underperforming: 'text-[#E8B84A]',
}

export function ModelCard({ data, error, loading, stale, corpus, timeframeLabel }) {
  const {
    ready,
    decided_count = 0,
    min_decisions = 30,
    training_size = 0,
    trained_at,
    top_features = [],
    model_status,
    pending_samples = 0,
    window_sample_count,
    window_since,
  } = data || {}

  const status = model_status || (ready ? 'ready' : 'untrained')
  const pct = min_decisions === 0 ? 0 : Math.min(1, decided_count / min_decisions)
  const topFive = top_features.slice(0, 5)
  const maxImportance = topFive[0]?.importance || 1

  // The timeframe chips can't window a pickle — say so here rather than
  // leaving the user to guess whether "trained on 2 971" moved with the
  // filter. See the router docstring.
  const footnote = window_since
    ? `Model facts are all-time — a ${timeframeLabel.toLowerCase()} window can't retrain the model. ${window_sample_count ?? 0} decision${window_sample_count === 1 ? '' : 's'} landed inside it.`
    : null

  return (
    <Card
      title="Taste model"
      headerRight={
        <span className={STATUS_TONE[status]}>{STATUS_LABELS[status]}</span>
      }
      error={error}
      loading={loading}
      stale={stale}
      footnote={footnote}
    >
      {!ready ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-2xl text-[#f9f9f9] tabular-nums leading-none">
              {decided_count}
            </span>
            <span className="text-sm text-[#9c9c9d]">of {min_decisions} decisions</span>
          </div>
          <div className="w-full bg-[#1b1c1e] rounded-full h-1.5 overflow-hidden">
            <div
              className="h-1.5 rounded-full bg-[#5BB8D4]"
              style={{
                width: `${pct * 100}%`,
                transition: 'width 240ms cubic-bezier(0.22, 1, 0.36, 1)',
              }}
            />
          </div>
          <Empty>
            Decide {Math.max(0, min_decisions - decided_count)} more photos to train the{' '}
            {corpus === 'photos' ? 'iPhone' : 'camera'} model. Every decision is kept
            forever — clearing analysis tabs doesn&apos;t reset it.
          </Empty>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-5 flex-wrap text-xs text-[#9c9c9d]">
            <Fact label="Trained on" value={training_size.toLocaleString()} suffix="decisions" />
            {trained_at && <Fact label="Last fit" value={trained_at.slice(0, 10)} />}
            {pending_samples > 0 && (
              <Fact label="Queued" value={`+${pending_samples}`} suffix="new" />
            )}
          </div>

          {topFive.length > 0 ? (
            <div>
              <div className="text-[11px] text-[#8a8b8c] tracking-[0.3px] uppercase mb-2">
                Top signals
              </div>
              <div className="flex flex-col">
                {topFive.map(f => (
                  <StatBar
                    key={f.name}
                    label={f.name.replace(/_/g, ' ')}
                    value={`${Math.round(f.importance * 100)}%`}
                    fraction={f.importance / maxImportance}
                    tooltip={`${f.name.replace(/_/g, ' ')} accounts for ${(f.importance * 100).toFixed(1)}% of the model's splits.`}
                  />
                ))}
              </div>
            </div>
          ) : (
            <Empty>Feature importances appear after the next training run.</Empty>
          )}
        </div>
      )}
    </Card>
  )
}

function Fact({ label, value, suffix }) {
  return (
    <span>
      {label}{' '}
      <span className="font-mono text-[#f9f9f9] tabular-nums">{value}</span>
      {suffix && ` ${suffix}`}
    </span>
  )
}
