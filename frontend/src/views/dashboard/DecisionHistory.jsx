// DecisionHistory — stacked weekly K/M/R bars + totals.
//
// The bars used to be fixed 12px columns in an `overflow-x-auto` strip: over
// a year that hid ~40 weeks behind a scrollbar with no affordance pointing at
// them, inside a page that already scrolls vertically. They now flex to fill
// the card, so the whole series is always visible and the card's width sets
// the bar width instead of the other way round.
//
// Hover uses HoverPopover (portalled, themed) rather than the native `title`
// tooltip — a light OS tooltip on a #07080a canvas was the loudest break with
// the design system on this page.
//
// The axis is real time. The endpoint zero-fills every bucket between the
// first and last decision, because omitting quiet weeks made ten bars
// spanning W20–W37 draw as ten adjacent columns: eighteen weeks of history
// looked like ten consecutive ones, and a three-week pause was
// indistinguishable from no pause.
//
// A non-zero week can never render as nothing. One week held a single
// decision against a peak of 1 751 — 0.1px, i.e. invisible — so any week with
// activity gets a 2px floor, and a week with none gets a baseline tick
// instead. "Nothing happened" and "almost nothing happened" have to look
// different or the floor would be a lie.
//
// Empty state is one line of copy, never an empty axis.

import { HoverPopover } from '../../ui/primitives'
import { Card, Empty, useGrow } from './primitives'

const COLORS = {
  keep:   '#7DB89A',
  maybe:  '#E8B84A',
  reject: '#C97B7B',
}

export function DecisionHistory({ data, error, loading, stale }) {
  const rows = data?.rows || []

  const totals = rows.reduce(
    (acc, r) => ({
      keep:   acc.keep   + (r.keep   || 0),
      maybe:  acc.maybe  + (r.maybe  || 0),
      reject: acc.reject + (r.reject || 0),
    }),
    { keep: 0, maybe: 0, reject: 0 },
  )
  const grandTotal = totals.keep + totals.maybe + totals.reject

  // One scale across all bars so the trend is legible; each bar's internal
  // split is a share of its own week.
  const maxWeek = rows.reduce(
    (m, r) => Math.max(m, (r.keep || 0) + (r.maybe || 0) + (r.reject || 0)),
    0,
  )

  const busiest = rows.find(
    r => (r.keep || 0) + (r.maybe || 0) + (r.reject || 0) === maxWeek,
  )
  const quietWeeks = rows.filter(
    r => (r.keep || 0) + (r.maybe || 0) + (r.reject || 0) === 0,
  ).length

  return (
    <Card
      title="Decision history"
      headerRight={
        grandTotal > 0 && (
          <span>
            <span className="font-mono text-[#f9f9f9] tabular-nums">
              {grandTotal.toLocaleString()}
            </span>{' '}
            decisions
          </span>
        )
      }
      error={error}
      loading={loading}
      stale={stale}
      footnote={
        busiest
          ? `Busiest week: ${formatPeriod(busiest.period)} with ${maxWeek.toLocaleString()} decisions.` +
            (quietWeeks > 0
              ? ` ${quietWeeks} of ${rows.length} weeks had none.`
              : '')
          : null
      }
    >
      {rows.length === 0 ? (
        <Empty>
          Your decisions will appear here as you cull. Each K/M/R press is recorded in a
          corpus that survives Clear Analysis and folder moves.
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-end gap-px h-28" role="img"
               aria-label={`Weekly decisions: ${totals.keep} keep, ${totals.maybe} maybe, ${totals.reject} reject across ${rows.length} weeks`}>
            {rows.map(r => (
              <WeekBar key={r.period} row={r} maxTotal={maxWeek} />
            ))}
          </div>
          <div className="flex items-center justify-between text-[11px] text-[#8a8b8c] font-mono">
            <span>{formatPeriod(rows[0].period)}</span>
            <span>{formatPeriod(rows[rows.length - 1].period)}</span>
          </div>
          <div className="flex items-center gap-4 text-xs pt-1">
            <Legend color={COLORS.keep}   label="Keep"   count={totals.keep} />
            <Legend color={COLORS.maybe}  label="Maybe"  count={totals.maybe} />
            <Legend color={COLORS.reject} label="Reject" count={totals.reject} />
          </div>
        </div>
      )}
    </Card>
  )
}

// "2026-W37" → "W37 '26". Weeks are the unit the backend buckets by; showing
// the raw strftime output was the kind of leak that makes a chart feel unfinished.
function formatPeriod(period) {
  const m = /^(\d{4})-W(\d{2})$/.exec(period || '')
  if (!m) return period
  return `W${Number(m[2])} '${m[1].slice(2)}`
}

function WeekBar({ row, maxTotal }) {
  const total = (row.keep || 0) + (row.maybe || 0) + (row.reject || 0)
  const targetPct = maxTotal === 0 ? 0 : (total / maxTotal) * 100
  const heightPct = useGrow(targetPct)
  const seg = (n) => (total === 0 ? 0 : (n / total) * 100)

  return (
    <HoverPopover
      block
      className="group flex-1 min-w-[2px] cursor-default"
      content={
        <span className="flex flex-col gap-0.5">
          <span className="font-mono">{formatPeriod(row.period)}</span>
          <span>
            {total === 0
              ? 'no decisions'
              : `${row.keep || 0} keep · ${row.maybe || 0} maybe · ${row.reject || 0} reject`}
          </span>
        </span>
      }
    >
      <div className="h-28 flex flex-col justify-end">
        {total === 0 ? (
          // A week you didn't cull. Not a stub bar — a stub would read as a
          // very small amount, which is a different statement.
          <div className="w-full h-px bg-[#1b1c1e] rounded-full" />
        ) : (
          <div
            className="w-full flex flex-col-reverse rounded-[2px] overflow-hidden opacity-90 group-hover:opacity-100 transition-opacity"
            style={{
              height: `${heightPct}%`,
              minHeight: '2px',
              transition: 'height 240ms cubic-bezier(0.22, 1, 0.36, 1), opacity 120ms ease',
            }}
          >
            <div style={{ height: `${seg(row.reject)}%`, backgroundColor: COLORS.reject }} />
            <div style={{ height: `${seg(row.maybe)}%`,  backgroundColor: COLORS.maybe  }} />
            <div style={{ height: `${seg(row.keep)}%`,   backgroundColor: COLORS.keep   }} />
          </div>
        )}
      </div>
    </HoverPopover>
  )
}

function Legend({ color, label, count }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[#9c9c9d]">
      <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: color }} />
      <span>{label}</span>
      <span className="font-mono text-[#f9f9f9] tabular-nums">{count.toLocaleString()}</span>
    </span>
  )
}
