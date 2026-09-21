// Dashboard primitives — the shared chrome every card on the page is built
// from. Extracted because the four cards previously each re-implemented the
// same bar in three different geometries (w-32/h-1.5, w-24/h-1.5, w-full/h-1)
// and the same card border with a hardcoded #2a2b2d that matched nothing
// else in the app.
//
// Tokens here follow docs/DESIGN.md rather than the dashboard's old local
// inventions: panel border rgba(255,255,255,0.06) (the app's dominant border,
// 53 other usages), bar track #1b1c1e, bar fill #a8a8a8 Mid Gray, 6px track
// height, 3px radius.
//
// Motion: bars grow from 0 to their value once on mount over 240ms ease-out,
// the duration DESIGN.md specifies for score bars. Honoured only when the
// user hasn't asked for reduced motion — see useGrow.

import { useEffect, useRef, useState } from 'react'
import { HoverPopover } from '../../ui/primitives'

const REDUCED = () =>
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// useGrow — returns the width/height a bar should render at right now.
// Starts at 0 and flips to the real value on the next frame so the CSS
// transition has two values to interpolate between; a bar that mounts
// already at its final width animates nothing.
export function useGrow(target) {
  const [value, setValue] = useState(() => (REDUCED() ? target : 0))
  const grown = useRef(false)

  useEffect(() => {
    if (REDUCED() || grown.current) {
      setValue(target)
      return
    }
    const id = requestAnimationFrame(() => {
      grown.current = true
      setValue(target)
    })
    return () => cancelAnimationFrame(id)
  }, [target])

  return value
}

// ── Card ───────────────────────────────────────────────────────────────────
// One section of the dashboard. Owns its own loading, error and stale states
// so a failing endpoint costs one card instead of the page.
//
//   loading  — first load, no data yet → shimmer block
//   error    — this endpoint failed; any previously-loaded data stays visible
//              underneath, because stale-and-labelled beats blank
//   stale    — a refetch is in flight; content dims instead of snapping
export function Card({
  title,
  headerRight,
  error,
  loading = false,
  stale = false,
  footnote,
  className = '',
  children,
}) {
  return (
    <section
      className={`rounded-xl border border-[rgba(255,255,255,0.06)] bg-[#101111] p-5 ${className}`}
    >
      <div className="flex items-baseline justify-between mb-4 gap-4 flex-wrap">
        <h2 className="text-sm font-semibold text-[#f9f9f9] tracking-[0.2px]">{title}</h2>
        {headerRight && <div className="text-xs text-[#9c9c9d]">{headerRight}</div>}
      </div>

      {error && (
        <p className="mb-3 text-xs text-[#C97B7B]">
          Couldn&apos;t load this section ({error}). Showing the last values.
        </p>
      )}

      {loading ? (
        <CardSkeleton />
      ) : (
        <div
          className={`transition-opacity duration-200 ${stale ? 'opacity-40' : 'opacity-100'}`}
        >
          {children}
        </div>
      )}

      {footnote && !loading && (
        <p className="mt-4 text-[11px] text-[#8a8b8c] leading-relaxed">{footnote}</p>
      )}
    </section>
  )
}

export function CardSkeleton({ rows = 3 }) {
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="shimmer h-3 rounded"
          style={{ width: `${88 - i * 16}%` }}
        />
      ))}
    </div>
  )
}

// ── StatBar ────────────────────────────────────────────────────────────────
// The single bar primitive: label · track · value. Replaces three divergent
// copies. `width` is a 0–1 fraction of the row's own max.
export function StatBar({ label, value, fraction, tooltip, color = '#a8a8a8' }) {
  const pct = useGrow(Math.max(0, Math.min(1, fraction || 0)) * 100)

  const row = (
    <div className="group flex items-center gap-3 py-[3px] -mx-2 px-2 rounded transition-colors hover:bg-[rgba(255,255,255,0.03)]">
      <span className="text-xs text-[#cecece] flex-1 truncate">{label}</span>
      <div className="w-28 bg-[#1b1c1e] rounded-[3px] h-1.5 overflow-hidden flex-shrink-0">
        <div
          className="h-1.5 rounded-[3px]"
          style={{
            width: `${pct}%`,
            backgroundColor: color,
            transition: 'width 240ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        />
      </div>
      <span className="font-mono text-xs text-[#9c9c9d] w-10 text-right flex-shrink-0">
        {value}
      </span>
    </div>
  )

  return tooltip ? <HoverPopover content={tooltip} block>{row}</HoverPopover> : row
}

// ── Kpi ────────────────────────────────────────────────────────────────────
// A single headline number in the top row. `delta` is the small line beneath
// it — the comparison that makes the number mean something.
export function Kpi({ label, value, unit, delta, deltaTone = 'neutral', tooltip, loading }) {
  const toneClass = {
    good:    'text-[#7DB89A]',
    bad:     'text-[#C97B7B]',
    neutral: 'text-[#8a8b8c]',
  }[deltaTone]

  const body = (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="text-[11px] text-[#8a8b8c] tracking-[0.3px] uppercase truncate">
        {label}
      </span>
      {loading ? (
        <div className="shimmer h-7 w-20 rounded" aria-hidden="true" />
      ) : (
        <span className="font-mono text-2xl leading-none text-[#f9f9f9] tabular-nums">
          {value}
          {unit && <span className="text-sm text-[#9c9c9d] ml-1">{unit}</span>}
        </span>
      )}
      {delta && !loading && (
        <span className={`text-[11px] ${toneClass} truncate`}>{delta}</span>
      )}
    </div>
  )

  return tooltip ? <HoverPopover content={tooltip} block>{body}</HoverPopover> : body
}

// ── Empty ──────────────────────────────────────────────────────────────────
// One empty-state voice for the whole page: what will fill this, and why it
// is worth filling. Never "No data".
export function Empty({ children }) {
  return <p className="text-xs text-[#8a8b8c] leading-relaxed">{children}</p>
}
