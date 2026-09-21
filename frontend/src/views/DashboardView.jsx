// DashboardView — persistent stats view, decoupled from analysis tabs.
//
// Reads only durable backend tables (training_samples, shooting_log,
// personal_model.pkl). Lifecycle is independent of which tab is active or
// whether any tab is open at all.
//
// Two filters, with deliberately different reach — the header says so rather
// than letting the user infer it:
//
//   Corpus     ('folder' | 'photos') — since the 2026-09-19 split each corpus
//              has its own model and its own slice of training_samples.
//              Governs the top three cards. Shooting behavior is shared
//              (shooting_log has no source_type) and is labelled as such.
//
//   Timeframe  (All time / 30d / 90d / 1y) — session-only, resets on reload;
//              it's exploratory, not a setting. It cannot window the model's
//              own facts: training_size, trained_at and accuracy describe one
//              pickle fit on everything. The model card shows how many
//              decisions landed inside the window instead, and says so.

import { useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useDashboard } from '../hooks/useDashboard'
import { Segmented } from '../ui/primitives'
import { Kpi } from './dashboard/primitives'
import { ModelCard } from './dashboard/ModelCard'
import { DecisionHistory } from './dashboard/DecisionHistory'
import { KeepVsReject } from './dashboard/KeepVsReject'
import { ShootingBehavior } from './dashboard/ShootingBehavior'

const TIMEFRAMES = [
  { key: 'all', label: 'All time', days: null },
  { key: '30d', label: '30d',      days: 30 },
  { key: '90d', label: '90d',      days: 90 },
  { key: '1y',  label: '1y',       days: 365 },
]

// Labels name the source of the pictures, not the tab they arrive in: the two
// models exist because an iPhone's pipeline and a camera sensor produce
// incompatible sharpness/aperture/ISO distributions. The noun "model" always
// stays attached to "Camera" in copy so it never reads as the app's own name.
const CORPORA = [
  { key: 'folder', label: 'Camera', title: 'Camera RAW/JPEG culled from folders' },
  { key: 'photos', label: 'iPhone', title: 'Decisions made on Apple Photos albums' },
]

function isoDateNDaysAgo(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  // YYYY-MM-DD in local time; SQLite compares lexicographically.
  return d.toISOString().slice(0, 10)
}

function relativeTime(ts) {
  if (!ts) return null
  const secs = Math.round((Date.now() - ts) / 1000)
  if (secs < 10) return 'just now'
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.round(mins / 60)}h ago`
}

export function DashboardView() {
  const [timeframe, setTimeframe] = useState('all')
  const [corpus, setCorpus] = useState('folder')

  const since = useMemo(() => {
    const tf = TIMEFRAMES.find(t => t.key === timeframe)
    return tf?.days == null ? null : isoDateNDaysAgo(tf.days)
  }, [timeframe])

  const {
    modelCard, decisionTimeline, featureDeltas,
    cameras, distributions, shootingTimeline,
    errors = {}, loading, refreshing, lastUpdated, refetch,
  } = useDashboard(since, corpus)

  const timeframeLabel = TIMEFRAMES.find(t => t.key === timeframe)?.label ?? 'All time'

  return (
    <div className="min-h-full bg-[#07080a]">
      <div className="max-w-6xl mx-auto px-6 py-6">
        <header className="mb-5 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold text-[#f9f9f9] tracking-[0.2px]">
              Dashboard
            </h1>
            <Segmented
              options={CORPORA}
              value={corpus}
              onChange={setCorpus}
              ariaLabel="Taste corpus"
            />
          </div>

          <div className="flex items-center gap-2">
            <Segmented
              options={TIMEFRAMES}
              value={timeframe}
              onChange={setTimeframe}
              ariaLabel="Timeframe"
            />
            <button
              onClick={refetch}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-[#9c9c9d] border border-[rgba(255,255,255,0.10)] hover:text-[#cecece] transition-colors focus:outline-none focus-visible:text-[#5BB8D4]"
              title="Refresh now"
            >
              <RefreshCw
                size={14}
                className={refreshing ? 'animate-spin' : ''}
                style={refreshing ? { animationDuration: '900ms' } : undefined}
              />
              <span className="font-mono tabular-nums">
                {refreshing ? 'Syncing' : relativeTime(lastUpdated) || 'Refresh'}
              </span>
            </button>
          </div>
        </header>

        <KpiRow
          modelCard={modelCard}
          timeline={decisionTimeline}
          deltas={featureDeltas}
          cameras={cameras}
          loading={loading}
          stale={refreshing}
          timeframeLabel={timeframeLabel}
        />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
          <ModelCard
            data={modelCard}
            error={errors.modelCard}
            loading={loading && !modelCard}
            stale={refreshing}
            corpus={corpus}
            timeframeLabel={timeframeLabel}
          />
          <DecisionHistory
            data={decisionTimeline}
            error={errors.decisionTimeline}
            loading={loading && !decisionTimeline}
            stale={refreshing}
          />
          <div className="lg:col-span-2">
            <KeepVsReject
              data={featureDeltas}
              error={errors.featureDeltas}
              loading={loading && !featureDeltas}
              stale={refreshing}
            />
          </div>
          <div className="lg:col-span-2">
            <ShootingBehavior
              cameras={cameras}
              distributions={distributions}
              timeline={shootingTimeline}
              error={errors.cameras || errors.distributions || errors.shootingTimeline}
              loading={loading && !cameras}
              stale={refreshing}
            />
          </div>
        </div>

        <p className="mt-5 text-[11px] text-[#8a8b8c] text-center leading-relaxed">
          Stats accumulate forever. Closing tabs or clearing analysis won&apos;t reset this view.
        </p>
      </div>
    </div>
  )
}

// KpiRow — the page's entry point. Four numbers that answer "how is it going?"
// before any chart is read. Each carries the comparison that gives it meaning:
// an accuracy without its baseline is a number, not a verdict.
function KpiRow({ modelCard, timeline, deltas, cameras, loading, stale, timeframeLabel }) {
  const rows = timeline?.rows || []
  const totals = rows.reduce(
    (a, r) => ({
      keep:   a.keep   + (r.keep   || 0),
      maybe:  a.maybe  + (r.maybe  || 0),
      reject: a.reject + (r.reject || 0),
    }),
    { keep: 0, maybe: 0, reject: 0 },
  )
  const decided = totals.keep + totals.maybe + totals.reject
  const keepRate = decided === 0 ? null : totals.keep / decided

  const ready = modelCard?.ready && modelCard?.validation?.model_accuracy != null
  const acc = modelCard?.validation?.model_accuracy
  const base = modelCard?.validation?.baseline_accuracy
  const minDecisions = modelCard?.min_decisions ?? 30
  const corpusCount = modelCard?.decided_count ?? 0

  const shots = (cameras?.cameras || []).reduce((s, r) => s + r.count, 0)
  const nKept = deltas?.n_kept_photos ?? 0
  const nRej = deltas?.n_rejected_photos ?? 0

  return (
    <div
      className={`rounded-xl border border-[rgba(255,255,255,0.06)] bg-[#101111] px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-5 transition-opacity duration-200 ${
        stale ? 'opacity-50' : 'opacity-100'
      }`}
    >
      {ready ? (
        <Kpi
          label="Accuracy"
          value={`${(acc * 100).toFixed(0)}`}
          unit="%"
          delta={base != null ? `${acc >= base ? '▲' : '▼'} vs ${(base * 100).toFixed(0)}% baseline` : null}
          deltaTone={base != null && acc >= base ? 'good' : 'bad'}
          loading={loading}
          tooltip="How often the model's keep/maybe/reject call matches yours on held-out decisions, against scoring by overall quality alone."
        />
      ) : (
        <Kpi
          label="Toward first model"
          value={`${corpusCount}`}
          unit={`/ ${minDecisions}`}
          delta={`${Math.max(0, minDecisions - corpusCount)} more decisions`}
          loading={loading}
          tooltip="This corpus trains its own model. The camera model needs 30 decisions, the iPhone model 150 — an iPhone's pipeline compresses the variance the model learns from."
        />
      )}

      <Kpi
        label="Keep rate"
        value={keepRate == null ? '—' : `${(keepRate * 100).toFixed(0)}`}
        unit={keepRate == null ? '' : '%'}
        delta={decided > 0 ? `${totals.keep} kept of ${decided}` : 'no decisions yet'}
        deltaTone="neutral"
        loading={loading}
        tooltip={`Share of decisions in ${timeframeLabel.toLowerCase()} that were Keep.`}
      />

      <Kpi
        label="Decisions"
        value={decided.toLocaleString()}
        delta={timeframeLabel === 'All time' ? 'all time' : `in ${timeframeLabel}`}
        deltaTone="neutral"
        loading={loading}
        tooltip={`${nKept} keeps and ${nRej} rejects carry a frozen feature vector and feed the model.`}
      />

      <Kpi
        label="Shots analyzed"
        value={shots.toLocaleString()}
        delta="all sources"
        deltaTone="neutral"
        loading={loading}
        tooltip="Every frame ever analyzed, from shooting_log. Not split by corpus — a phone frame is still a frame you shot."
      />
    </div>
  )
}
