// DashboardView — persistent stats view, decoupled from analysis tabs.
//
// Renders four sections that all read from durable backend tables
// (training_samples, shooting_log, personal_model.pkl). Lifecycle is fully
// independent of which folder tab is active or whether any tab is open.
//
// Timeframe filter (All time / 30d / 90d / 1y) is session-only — resets
// on reload. It's exploratory, not a setting.

import { useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useDashboard } from '../hooks/useDashboard'
import { ModelCard } from './dashboard/ModelCard'
import { DecisionHistory } from './dashboard/DecisionHistory'
import { KeepVsReject } from './dashboard/KeepVsReject'
import { ShootingBehavior } from './dashboard/ShootingBehavior'

const TIMEFRAMES = [
  { id: 'all', label: 'All time', days: null },
  { id: '30d', label: '30d',      days: 30 },
  { id: '90d', label: '90d',      days: 90 },
  { id: '1y',  label: '1y',       days: 365 },
]

function isoDateNDaysAgo(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  // YYYY-MM-DD in local time; SQLite compares lexicographically.
  return d.toISOString().slice(0, 10)
}

export function DashboardView() {
  const [timeframe, setTimeframe] = useState('all')
  const since = useMemo(() => {
    const tf = TIMEFRAMES.find(t => t.id === timeframe)
    return tf?.days == null ? null : isoDateNDaysAgo(tf.days)
  }, [timeframe])

  const {
    modelCard, decisionTimeline, featureDeltas,
    cameras, distributions, shootingTimeline,
    loading, error, refetch,
  } = useDashboard(since)

  return (
    <div className="min-h-screen bg-[#07080a]">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <header className="mb-6 flex items-baseline justify-between gap-6 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold text-[#f9f9f9] mb-1">Dashboard</h1>
            <p className="text-xs text-[#9c9c9d]">
              Stats grow over time and persist across analysis sessions. Closing tabs or clearing analysis won&apos;t reset this view.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <TimeframeChips value={timeframe} onChange={setTimeframe} />
            <button
              onClick={refetch}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded text-xs text-[#cecece] border border-[#2a2b2d] hover:opacity-70 transition-opacity"
            >
              <RefreshCw size={15} /> Refresh
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-lg border border-[rgba(201,123,123,0.40)] bg-[rgba(201,123,123,0.08)] text-xs text-[#C97B7B]">
            Failed to load dashboard: {error}
          </div>
        )}

        <div className="flex flex-col gap-4">
          <ModelCard data={modelCard} />
          <DecisionHistory data={decisionTimeline} />
          <KeepVsReject data={featureDeltas} />
          <ShootingBehavior
            cameras={cameras}
            distributions={distributions}
            timeline={shootingTimeline}
          />
        </div>

        {loading && Object.keys(modelCard || {}).length === 0 && (
          <p className="text-xs text-[#6a6b6c] mt-4 text-center">Loading…</p>
        )}
      </div>
    </div>
  )
}

function TimeframeChips({ value, onChange }) {
  return (
    <div
      role="radiogroup"
      aria-label="Timeframe"
      className="inline-flex items-center rounded border border-[#2a2b2d] overflow-hidden"
    >
      {TIMEFRAMES.map((tf, i) => {
        const selected = tf.id === value
        return (
          <button
            key={tf.id}
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(tf.id)}
            className={
              `px-3 py-1 text-xs transition-colors ` +
              (selected
                ? 'bg-[#1b1c1e] text-[#f9f9f9]'
                : 'text-[#9c9c9d] hover:text-[#cecece]') +
              (i > 0 ? ' border-l border-[#2a2b2d]' : '')
            }
          >
            {tf.label}
          </button>
        )
      })}
    </div>
  )
}
