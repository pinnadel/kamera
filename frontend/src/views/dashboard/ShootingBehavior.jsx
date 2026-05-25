// ShootingBehavior — three columns of histograms over the shooting_log
// corpus: Cameras + Lens, Focal length + Aperture, ISO + Film simulation.
//
// Bars use a single neutral colour (#cecece) per the design system. Bucket
// order is preserved from the backend's CASE-driven SQL.

export function ShootingBehavior({ cameras, distributions, timeline }) {
  const camRows = cameras?.cameras || []
  const dist    = distributions || {}
  const tlRows  = timeline?.rows || []

  const hasAny = camRows.length > 0 ||
    (dist.focal_length || []).some(b => b.count > 0)

  return (
    <section className="rounded-xl border border-[#2a2b2d] bg-[#101111] p-6">
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-base font-semibold text-[#f9f9f9]">Shooting behavior</h2>
        {camRows.length > 0 && (
          <span className="text-xs text-[#9c9c9d]">
            <span className="font-mono text-[#f9f9f9]">
              {camRows.reduce((s, r) => s + r.count, 0)}
            </span>
            {' '}analyzed shots
          </span>
        )}
      </div>

      {!hasAny ? (
        <p className="text-xs text-[#9c9c9d] leading-relaxed">
          No shooting data yet — analyze a folder to see camera, lens, and exposure trends.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-6">
          <Cameras rows={camRows} />
          <Histogram title="Lens" rows={dist.lens_model || []} />
          <Histogram title="Focal length" rows={dist.focal_length || []} />
          <Histogram title="Aperture" rows={dist.aperture || []} />
          <Histogram title="ISO" rows={dist.iso || []} />
          <Histogram title="Film simulation" rows={dist.film_simulation || []} />
          {tlRows.length > 0 && (
            <div className="md:col-span-2">
              <ShootingTimeline rows={tlRows} />
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function Cameras({ rows }) {
  if (rows.length === 0) {
    return (
      <Block title="Cameras">
        <p className="text-xs text-[#6a6b6c]">No camera data captured yet.</p>
      </Block>
    )
  }
  const max = Math.max(...rows.map(r => r.count))
  return (
    <Block title="Cameras">
      <div className="flex flex-col gap-1.5">
        {rows.map(r => (
          <Bar key={r.camera} label={r.camera || 'Unknown'} value={r.count} max={max} />
        ))}
      </div>
    </Block>
  )
}

function Histogram({ title, rows }) {
  const cleaned = rows.filter(r => r.bucket && r.count > 0)
  if (cleaned.length === 0) {
    return (
      <Block title={title}>
        <p className="text-xs text-[#6a6b6c]">No data in this dimension yet.</p>
      </Block>
    )
  }
  const max = Math.max(...cleaned.map(r => r.count))
  return (
    <Block title={title}>
      <div className="flex flex-col gap-1.5">
        {cleaned.map(r => (
          <Bar key={r.bucket} label={r.bucket} value={r.count} max={max} />
        ))}
      </div>
    </Block>
  )
}

function ShootingTimeline({ rows }) {
  const max = Math.max(...rows.map(r => r.count))
  return (
    <Block title="Shots over time">
      <div className="flex items-end gap-0.5 h-20">
        {rows.map(r => (
          <div
            key={r.period}
            className="flex-1 bg-[#cecece] rounded-[1px] min-h-[1px]"
            style={{ height: `${(r.count / max) * 100}%` }}
            title={`${r.period}: ${r.count} shots`}
          />
        ))}
      </div>
    </Block>
  )
}

function Block({ title, children }) {
  return (
    <div>
      <div className="text-xs text-[#9c9c9d] mb-2">{title}</div>
      {children}
    </div>
  )
}

function Bar({ label, value, max }) {
  const pct = max === 0 ? 0 : (value / max) * 100
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-[#cecece] flex-1 truncate" title={label}>
        {label}
      </span>
      <div className="w-24 bg-[#1b1c1e] rounded-[3px] h-1.5">
        <div className="bg-[#cecece] h-1.5 rounded-[3px]" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-[#9c9c9d] w-10 text-right">{value}</span>
    </div>
  )
}
