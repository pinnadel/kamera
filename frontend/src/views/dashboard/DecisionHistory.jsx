// DecisionHistory — stacked weekly K/M/X bars + all-time totals.
//
// Empty state: a single line of copy, no empty bars (per plan: "never render
// an empty bar chart").

const COLORS = {
  keep:   '#7DB89A',
  maybe:  '#E8B84A',
  reject: '#C97B7B',
}

export function DecisionHistory({ data }) {
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

  // Find the max bar height so weekly bars are scaled relative to the busiest
  // week. Using the same scale across all bars makes the trend legible.
  const maxWeek = rows.reduce(
    (m, r) => Math.max(m, (r.keep || 0) + (r.maybe || 0) + (r.reject || 0)),
    0,
  )

  const keepRate = grandTotal === 0 ? null : totals.keep / grandTotal

  return (
    <section className="rounded-xl border border-[#2a2b2d] bg-[#101111] p-6">
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-base font-semibold text-[#f9f9f9]">Decision history</h2>
        {grandTotal > 0 && (
          <span className="text-xs text-[#9c9c9d]">
            <span className="font-mono text-[#f9f9f9]">{grandTotal}</span> all-time
            {keepRate != null && (
              <>
                {' · '}keep rate{' '}
                <span className="font-mono text-[#7DB89A]">
                  {(keepRate * 100).toFixed(0)}%
                </span>
              </>
            )}
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-[#9c9c9d] leading-relaxed">
          Your decisions will appear here as you cull. Each K/M/X press is recorded forever in a corpus that survives Clear Analysis and folder moves.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-end gap-1 h-32 overflow-x-auto pb-2">
            {rows.map((r) => (
              <WeekBar key={r.period} row={r} maxTotal={maxWeek} />
            ))}
          </div>
          <div className="flex items-center gap-4 text-xs">
            <Legend color={COLORS.keep}   label="Keep"   count={totals.keep} />
            <Legend color={COLORS.maybe}  label="Maybe"  count={totals.maybe} />
            <Legend color={COLORS.reject} label="Reject" count={totals.reject} />
          </div>
        </div>
      )}
    </section>
  )
}

function WeekBar({ row, maxTotal }) {
  const total = (row.keep || 0) + (row.maybe || 0) + (row.reject || 0)
  const heightPct = maxTotal === 0 ? 0 : (total / maxTotal) * 100
  // Each segment is sized as a share of THIS bar (not the max), so the
  // colour split inside one week is proportional to its own counts.
  const seg = (n) => total === 0 ? 0 : (n / total) * 100

  return (
    <div className="flex flex-col items-center gap-1 flex-shrink-0" title={`${row.period}: ${total} decisions`}>
      <div className="flex flex-col-reverse w-3 h-32 justify-start" style={{ height: '7rem' }}>
        <div style={{ height: `${heightPct}%` }} className="w-full flex flex-col-reverse rounded-[2px] overflow-hidden">
          <div style={{ height: `${seg(row.reject)}%`, backgroundColor: COLORS.reject }} />
          <div style={{ height: `${seg(row.maybe)}%`,  backgroundColor: COLORS.maybe  }} />
          <div style={{ height: `${seg(row.keep)}%`,   backgroundColor: COLORS.keep   }} />
        </div>
      </div>
      <span className="text-[9px] text-[#6a6b6c] font-mono">{row.period.slice(-3)}</span>
    </div>
  )
}

function Legend({ color, label, count }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[#9c9c9d]">
      <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: color }} />
      <span>{label}</span>
      <span className="font-mono text-[#f9f9f9]">{count}</span>
    </span>
  )
}
