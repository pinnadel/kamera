// ShootingBehavior — histograms over the shooting_log corpus: cameras, lens,
// focal length, aperture, ISO, film simulation, plus shots over time.
//
// Blocks with no rows are dropped rather than rendered as a labelled hole.
// "Film simulation" is the reason: for a Nikon- or Hasselblad-only window it
// can never fill, and a permanent "No data in this dimension yet" reads as a
// broken panel rather than an absent dimension.
//
// "Unknown" is not a bucket, it is missing EXIF. The SQL emits it via
// `CASE WHEN x IS NULL THEN 'Unknown'`, and letting it compete as a bar was
// ruinous: 70% of rows carry no lens_model, so "Unknown" set the scale and
// every real lens collapsed into a stub — 911 frames on the 135mm rendered
// at a fifth of a bar that means "we don't know". Missing data now leaves the
// chart and is stated underneath instead, where it is information rather than
// noise. Film simulation is 94% unknown for the same reason (only Fuji bodies
// write it), and the remaining 373 frames make a perfectly good chart once
// the void stops dominating it.
//
// Each histogram carries its OWN denominator. The shares used to divide by
// the Cameras total, but /shooting/cameras filters `WHERE camera IS NOT NULL`
// while /shooting/distributions does not — 1 530 rows differ, so the lens
// tooltip reported 92% where the truth was 70.5%.
//
// Not corpus-filtered — shooting_log has no source_type column, and the
// question this answers ("what do I shoot with?") doesn't split along the
// model's corpus boundary. The header says "all sources" so the corpus
// switch never looks like it filters something it doesn't.

import { useState } from 'react'
import { HoverPopover } from '../../ui/primitives'
import { Card, StatBar, Empty, useGrow } from './primitives'

// The literal the router's CASE statements emit for a NULL column.
const MISSING = 'Unknown'

// Long tails (15 lenses, most with single-digit counts) collapse behind a
// toggle so a block stays scannable.
const TOP_N = 8

export function ShootingBehavior({ cameras, distributions, timeline, error, loading, stale }) {
  const camRows = cameras?.cameras || []
  const dist    = distributions || {}
  const tlRows  = timeline?.rows || []
  const totalShots = camRows.reduce((s, r) => s + r.count, 0)

  const blocks = [
    // `wide` blocks hold free-text labels (lens and body names run long and
    // truncate at one column width); the bucketed numeric ones do not.
    { id: 'cameras', title: 'Cameras', wide: true, missingLabel: 'no camera recorded',
      rows: camRows.map(r => ({ bucket: r.camera || MISSING, count: r.count })) },
    { id: 'lens', title: 'Lens', wide: true, missingLabel: 'no lens recorded',
      rows: dist.lens_model || [] },
    { id: 'focal', title: 'Focal length', missingLabel: 'no focal length recorded',
      rows: dist.focal_length || [] },
    { id: 'aperture', title: 'Aperture', missingLabel: 'no aperture recorded',
      rows: dist.aperture || [] },
    { id: 'iso', title: 'ISO', missingLabel: 'no ISO recorded',
      rows: dist.iso || [] },
    { id: 'film', title: 'Film simulation', missingLabel: 'no film simulation (non-Fuji bodies)',
      rows: dist.film_simulation || [] },
  ]
    .map(b => {
      const rows = b.rows.filter(r => r.bucket && r.count > 0)
      const total = rows.reduce((s, r) => s + r.count, 0)
      const missing = rows.find(r => r.bucket === MISSING)?.count || 0
      return { ...b, total, missing, rows: rows.filter(r => r.bucket !== MISSING) }
    })
    .filter(b => b.rows.length > 0)

  const hasAny = blocks.length > 0

  return (
    <Card
      title="Shooting behavior"
      headerRight={
        totalShots > 0 && (
          <span>
            <span className="font-mono text-[#f9f9f9] tabular-nums">
              {totalShots.toLocaleString()}
            </span>{' '}
            shots · all sources
          </span>
        )
      }
      error={error}
      loading={loading}
      stale={stale}
    >
      {!hasAny ? (
        <Empty>
          No shooting data in this window yet — analyze a folder or album to see camera,
          lens and exposure trends.
        </Empty>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-x-8 gap-y-6">
          {blocks.map(b => (
            <div key={b.id} className={b.wide ? 'xl:col-span-2' : ''}>
              <Block title={b.title}>
                <Histogram
                  rows={b.rows}
                  total={b.total}
                  missing={b.missing}
                  missingLabel={b.missingLabel}
                />
              </Block>
            </div>
          ))}
          {tlRows.length > 0 && (
            <div className="md:col-span-2 xl:col-span-4">
              <Block title="Shots over time">
                <ShootingTimeline rows={tlRows} />
              </Block>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

function Histogram({ rows, total, missing, missingLabel }) {
  const [expanded, setExpanded] = useState(false)
  // Scale to the known values only. Including the missing bucket here was the
  // whole problem: it is not a value, so it must not set the axis.
  const max = Math.max(...rows.map(r => r.count))
  const visible = expanded ? rows : rows.slice(0, TOP_N)
  const hidden = rows.length - visible.length

  return (
    <div className="flex flex-col">
      {visible.map(r => (
        <StatBar
          key={r.bucket}
          label={r.bucket}
          value={r.count.toLocaleString()}
          fraction={r.count / max}
          tooltip={
            total > 0
              ? `${r.bucket} — ${r.count.toLocaleString()} shots (${((r.count / total) * 100).toFixed(1)}% of this window)`
              : r.bucket
          }
        />
      ))}

      {hidden > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="self-start text-[11px] text-[#9c9c9d] hover:text-[#cecece] transition-colors px-2 py-1 -mx-2 mt-1 rounded focus:outline-none focus-visible:text-[#5BB8D4]"
        >
          Show {hidden} more
        </button>
      )}

      {missing > 0 && (
        <p className="text-[11px] text-[#8a8b8c] mt-2 leading-relaxed">
          <span className="font-mono tabular-nums">{missing.toLocaleString()}</span> shots
          {total > 0 && ` (${Math.round((missing / total) * 100)}%)`} have {missingLabel} —
          left out of the bars above.
        </p>
      )}
    </div>
  )
}

function ShootingTimeline({ rows }) {
  const max = Math.max(...rows.map(r => r.count))
  return (
    <div className="flex flex-col gap-2">
      <div
        className="flex items-end gap-px h-20"
        role="img"
        aria-label={`Shots per week across ${rows.length} weeks, peaking at ${max}`}
      >
        {rows.map(r => (
          <TimelineBar key={r.period} row={r} max={max} />
        ))}
      </div>
      <div className="flex items-center justify-between text-[11px] text-[#8a8b8c] font-mono">
        <span>{formatPeriod(rows[0].period)}</span>
        <span>{formatPeriod(rows[rows.length - 1].period)}</span>
      </div>
    </div>
  )
}

function TimelineBar({ row, max }) {
  const height = useGrow(max === 0 ? 0 : (row.count / max) * 100)
  return (
    <HoverPopover
      block
      className="group flex-1 min-w-[2px] cursor-default"
      content={row.count === 0
        ? `${formatPeriod(row.period)} — no shots`
        : `${formatPeriod(row.period)} — ${row.count.toLocaleString()} shots`}
    >
      <div className="h-20 flex flex-col justify-end">
        {row.count === 0 ? (
          <div className="w-full h-px bg-[#1b1c1e] rounded-full" />
        ) : (
          <div
            className="w-full bg-[#a8a8a8] rounded-[1px] opacity-80 group-hover:opacity-100"
            style={{
              height: `${height}%`,
              minHeight: '2px',
              transition: 'height 240ms cubic-bezier(0.22, 1, 0.36, 1), opacity 120ms ease',
            }}
          />
        )}
      </div>
    </HoverPopover>
  )
}

function formatPeriod(period) {
  const m = /^(\d{4})-W(\d{2})$/.exec(period || '')
  if (!m) return period
  return `W${Number(m[2])} '${m[1].slice(2)}`
}

function Block({ title, children }) {
  return (
    <div>
      <div className="text-[11px] text-[#8a8b8c] tracking-[0.3px] uppercase mb-2">{title}</div>
      {children}
    </div>
  )
}
