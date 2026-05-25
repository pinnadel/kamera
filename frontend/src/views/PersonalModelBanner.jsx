// PersonalModelBanner — informational status strip above the grid.
//
// Two phases:
//   Startup (0–50): two-segment bar toward first training pass (30) and
//     auto-cull delegation (50). Banner hides once "ready" is met but the
//     model hasn't accumulated growth-phase samples yet.
//   Growth (50+): single gradient bar within the current tier. Never hides
//     automatically — reframes from "do more" to "watch it get smarter".
//     Milestones: 50 Calibrating → 100 Knows your eye → 200 Your curator
//     → 500 Deeply attuned.
//
// Bar color journey amber → sage → cyan → bright cyan mirrors the mental
// model of the model warming up (learning) and then cooling into confident
// intelligence. The gradient within each tier shows both where you are and
// where the tier is heading.

import { X } from 'lucide-react'
import { DecisionWord, InfoTooltip } from '../ui/primitives'

// ─── Startup tiers (status: untrained / learning / underperforming) ──────────

const STARTUP_TIER = {
  untrained: {
    pillBg:   'bg-[rgba(156,156,157,0.12)]',
    pillText: 'text-[#cecece]',
    pillLabel: 'Not trained yet',
    cardBg:    'bg-[#101111]',
    fillSegA:  'bg-[#9c9c9d]',
    fillSegB:  'bg-[#9c9c9d]/30',
  },
  learning: {
    pillBg:   'bg-[rgba(232,184,74,0.18)]',
    pillText: 'text-[#E8B84A]',
    pillLabel: 'Learning your taste',
    cardBg:    'bg-[rgba(232,184,74,0.04)]',
    fillSegA:  'bg-[#7DB89A]',
    fillSegB:  'bg-[#E8B84A]',
  },
  underperforming: {
    pillBg:   'bg-[rgba(201,123,123,0.18)]',
    pillText: 'text-[#C97B7B]',
    pillLabel: 'Under review',
    cardBg:    'bg-[rgba(201,123,123,0.04)]',
    fillSegA:  'bg-[#7DB89A]',
    fillSegB:  'bg-[#C97B7B]',
  },
}

// ─── Growth tiers (status: ready) ────────────────────────────────────────────
// Bar gradient warms → cools to signal "the model is getting smarter".

const GROWTH_TIERS = [
  {
    key:       'calibrating',
    start:     50,
    end:       100,
    pillBg:    'bg-[rgba(232,184,74,0.15)]',
    pillText:  'text-[#E8B84A]',
    pillLabel: 'Calibrating',
    cardBg:    'bg-[rgba(232,184,74,0.03)]',
    barFrom:   '#E8B84A',
    barTo:     '#A8BEA4',
    nextLabel: 'Knows your eye',
  },
  {
    key:       'knows-your-eye',
    start:     100,
    end:       200,
    pillBg:    'bg-[rgba(125,184,154,0.18)]',
    pillText:  'text-[#7DB89A]',
    pillLabel: 'Knows your eye',
    cardBg:    'bg-[rgba(125,184,154,0.04)]',
    barFrom:   '#7DB89A',
    barTo:     '#5BB8D4',
    nextLabel: 'Your curator',
  },
  {
    key:       'your-curator',
    start:     200,
    end:       500,
    pillBg:    'bg-[rgba(91,184,212,0.18)]',
    pillText:  'text-[#5BB8D4]',
    pillLabel: 'Your curator',
    cardBg:    'bg-[rgba(91,184,212,0.04)]',
    barFrom:   '#5BB8D4',
    barTo:     '#82D8EC',
    nextLabel: 'Deeply attuned',
  },
  {
    key:       'deeply-attuned',
    start:     500,
    end:       null,
    pillBg:    'bg-[rgba(91,184,212,0.25)]',
    pillText:  'text-[#82D8EC]',
    pillLabel: 'Deeply attuned',
    cardBg:    'bg-[rgba(91,184,212,0.07)]',
    barFrom:   '#5BB8D4',
    barTo:     '#B0F0FF',
    nextLabel: null,
  },
]

function getGrowthTier(trainingSize) {
  for (let i = GROWTH_TIERS.length - 1; i >= 0; i--) {
    if (trainingSize >= GROWTH_TIERS[i].start) return GROWTH_TIERS[i]
  }
  return GROWTH_TIERS[0]
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function relativeTime(iso) {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (seconds < 30)        return 'just now'
  if (seconds < 90)        return '1m ago'
  if (seconds < 3600)      return `${Math.round(seconds / 60)}m ago`
  if (seconds < 7200)      return '1h ago'
  if (seconds < 86400)     return `${Math.round(seconds / 3600)}h ago`
  if (seconds < 172800)    return 'yesterday'
  return `${Math.floor(seconds / 86400)}d ago`
}

// ─── Detail copy ─────────────────────────────────────────────────────────────

function HeadlineDetail({ status, decided, minDecisions, trainingSize, lastAutoTrainAt, pendingSamples, autoRunning, growthTier }) {
  const queued = pendingSamples > 0
    ? ` · ${pendingSamples} new ${pendingSamples === 1 ? 'decision' : 'decisions'} queued`
    : ''

  // Startup: untrained
  if (status === 'untrained') {
    const remaining = Math.max(0, minDecisions - decided)
    return (
      <>
        Your model trains itself in the background as you cull. Just{' '}
        {remaining} more{' '}
        <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
        <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
        <DecisionWord kind="reject">Reject</DecisionWord>
        {' '}{remaining === 1 ? 'decision' : 'decisions'} until the first training pass kicks in.
      </>
    )
  }

  // Startup: learning
  if (status === 'learning') {
    const fresh = relativeTime(lastAutoTrainAt)
    return (
      <>
        {autoRunning ? (
          <>Re-training on your latest decisions… </>
        ) : (
          <>
            Last updated {fresh ?? 'recently'} on {trainingSize} samples
            {pendingSamples > 0 ? ` · ${pendingSamples} new ${pendingSamples === 1 ? 'decision' : 'decisions'} queued` : ''}.{' '}
          </>
        )}
        Auto-cull will switch from quality thresholds to your taste once the model crosses 50 samples and beats them.
      </>
    )
  }

  // Startup: underperforming
  if (status === 'underperforming') {
    const fresh = relativeTime(lastAutoTrainAt)
    return (
      <>
        Trained on {trainingSize} samples {fresh ? `(${fresh})` : ''} — but not yet
        better than the quality-threshold defaults. The model retrains itself
        automatically as you cull more; results often improve with a wider
        mix of{' '}
        <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
        <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
        <DecisionWord kind="reject">Reject</DecisionWord>
        {' '}decisions.
      </>
    )
  }

  // Growth: calibrating (50–100)
  if (growthTier?.key === 'calibrating') {
    const remaining = growthTier.end - trainingSize
    return (
      <>
        {autoRunning && <>Re-training on your latest decisions… </>}
        Auto-cull is now driven by your taste. Keep culling to sharpen its read on your creative eye —{' '}
        {remaining} more {remaining === 1 ? 'decision' : 'decisions'} to the next refinement{queued}.
      </>
    )
  }

  // Growth: knows your eye (100–200)
  if (growthTier?.key === 'knows-your-eye') {
    return (
      <>
        {autoRunning && <>Re-training on your latest decisions… </>}
        Your model has learned from {trainingSize} of your decisions and is building a nuanced picture of your eye.
        More variety — across light, subject, and moment — sharpens it further{queued}.
      </>
    )
  }

  // Growth: your curator (200–500)
  if (growthTier?.key === 'your-curator') {
    return (
      <>
        {autoRunning && <>Re-training on your latest decisions… </>}
        Trained on {trainingSize} decisions, your model has a strong read on your creative voice.
        Precision compounds — every session makes the picks more personal{queued}.
      </>
    )
  }

  // Growth: deeply attuned (500+)
  return (
    <>
      {autoRunning && <>Re-training on your latest decisions… </>}
      With {trainingSize} decisions learned, your model has a deep, nuanced understanding of your taste.
      It keeps refining quietly in the background as you cull{queued}.
    </>
  )
}

// ─── Banner ───────────────────────────────────────────────────────────────────

export function PersonalModelBanner({ modelInfo, onDismiss }) {
  if (!modelInfo) return null

  const status       = modelInfo.model_status || (modelInfo.ready ? 'ready' : 'untrained')
  const decided      = modelInfo.decided_count ?? 0
  const minDecisions = modelInfo.min_decisions ?? 30
  const trainingSize = modelInfo.training_size ?? 0
  const autoRunning  = !!modelInfo.auto_running
  const lastAutoTrainAt = modelInfo.last_auto_train_at
  const pendingSamples  = modelInfo.pending_samples ?? 0

  const isGrowth   = status === 'ready'
  // Tier resolution tracks `decided` (durable decisions) for the same reason
  // the bar does — so the pill label tracks the user's progress, not the
  // auto-trainer's last commit. `trainingSize` lags by up to RETRAIN_DELTA.
  const growthTier = isGrowth ? getGrowthTier(decided) : null
  const startupTier = !isGrowth ? (STARTUP_TIER[status] || STARTUP_TIER.untrained) : null

  // Progress is driven by `decided_count` (the durable training_samples row
  // count) rather than `training_size` (samples the model was last fit on).
  // The auto-trainer only commits a new `training_size` every RETRAIN_DELTA
  // decisions, so reading from it makes the bar look frozen between retrains
  // even though the user is actively culling. `decided_count` moves on every
  // K/M/R, which matches the user's mental model. The tooltip still surfaces
  // `training_size` for users who want to know what the model has learnt from.
  //
  // Startup bar: segment A = 0→30 (or full when trained), segment B = 30→50
  const pctA = !isGrowth && status === 'untrained'
    ? Math.min(1, decided / minDecisions)
    : 1
  const pctB = !isGrowth && status !== 'untrained'
    ? Math.min(1, (decided - minDecisions) / (50 - minDecisions))
    : 0

  // Growth bar: progress within the current tier toward next milestone
  const growthPct = isGrowth
    ? (growthTier.end
        ? Math.min(1, (decided - growthTier.start) / (growthTier.end - growthTier.start))
        : 1)
    : 0

  const progressLabel = isGrowth
    ? `${decided} decisions`
    : status === 'untrained'
      ? `${decided} / ${minDecisions} decisions`
      : `${decided} / 50 decisions`

  const pillBg    = isGrowth ? growthTier.pillBg    : startupTier.pillBg
  const pillText  = isGrowth ? growthTier.pillText  : startupTier.pillText
  const pillLabel = isGrowth ? growthTier.pillLabel : startupTier.pillLabel
  const cardBg    = isGrowth ? growthTier.cardBg    : startupTier.cardBg

  return (
    <div className="ai-border mb-3">
      <div className={`ai-border-inner px-4 py-3 ${cardBg}`}>

        {/* Row 1 — headline + status pill + auto-train indicator + dismiss */}
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm text-[#f9f9f9] font-medium">Personal taste model</span>

          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium ${pillBg} ${pillText}`}>
            <span className="font-bold">Status:</span>
            <span>{pillLabel}</span>
            <InfoTooltip>
              <HeadlineDetail
                status={status}
                decided={decided}
                minDecisions={minDecisions}
                trainingSize={trainingSize}
                lastAutoTrainAt={lastAutoTrainAt}
                pendingSamples={pendingSamples}
                autoRunning={autoRunning}
                growthTier={growthTier}
              />
            </InfoTooltip>
          </span>

          {autoRunning && (
            <span className="inline-flex items-center gap-1.5 text-[11px] text-[#5BB8D4]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4] animate-pulse" />
              re-training…
            </span>
          )}

          {onDismiss && (
            <button
              onClick={onDismiss}
              aria-label="Hide personal model banner"
              title="Hide — manual retraining is still available in Settings"
              className="ml-auto text-[#6a6b6c] hover:opacity-70 transition-opacity px-1"
            >
              <X size={16} />
            </button>
          )}
        </div>

        {/* Row 2 — progress bar */}
        <div className="flex items-center gap-3 mt-3">
          {isGrowth ? (
            // Growth: single gradient bar. Color shifts with each tier to
            // communicate increasing model confidence.
            <div className="flex-1 h-1.5 rounded-full bg-[#1b1c1e] overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{
                  width: `${growthPct * 100}%`,
                  background: `linear-gradient(to right, ${growthTier.barFrom}, ${growthTier.barTo})`,
                }}
              />
            </div>
          ) : (
            // Startup: two-segment bar so both milestones are visible at once
            <div className="flex-1 flex items-center gap-1 h-1.5">
              <div className="flex-1 h-1.5 rounded-l-full bg-[#1b1c1e] overflow-hidden">
                <div
                  className={`h-full transition-all ${startupTier.fillSegA}`}
                  style={{ width: `${pctA * 100}%` }}
                />
              </div>
              <div className="flex-1 h-1.5 rounded-r-full bg-[#1b1c1e] overflow-hidden">
                <div
                  className={`h-full transition-all ${startupTier.fillSegB}`}
                  style={{ width: `${pctB * 100}%` }}
                />
              </div>
            </div>
          )}

          <span className="text-[11px] font-mono text-[#9c9c9d] tabular-nums whitespace-nowrap">
            {progressLabel}
          </span>
        </div>

        {/* Row 3 — milestone labels under bar */}
        {isGrowth ? (
          <div className="flex justify-between mt-1.5 text-[10px] text-[#6a6b6c] tabular-nums">
            <span>{growthTier.start}</span>
            <span className={growthPct >= 1 && growthTier.nextLabel ? 'text-[#5BB8D4]' : ''}>
              {growthTier.end
                ? `${growthTier.end} · ${growthTier.nextLabel}`
                : 'keeps improving'}
            </span>
          </div>
        ) : (
          <div className="flex justify-between mt-1.5 text-[10px] text-[#6a6b6c] tabular-nums">
            <span>0</span>
            <span className={pctA >= 1 ? 'text-[#7DB89A]' : ''}>
              {minDecisions} · first training pass
            </span>
            <span className={pctB >= 1 ? 'text-[#5BB8D4]' : ''}>
              50 · drives auto-cull
            </span>
          </div>
        )}

      </div>
    </div>
  )
}
