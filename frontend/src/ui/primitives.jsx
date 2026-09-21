// Atomic UI primitives — small stateless components shared across the app.

import { useRef, useState, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'

// Shared popover style — used by both InfoTooltip and HoverPopover.
// All visual properties set inline to bypass any cascade interference
// (parent opacity, Tailwind JIT misses, stacking-context quirks). Rendered
// via createPortal into document.body so it never inherits ancestor opacity.
// Fixed 256px wide pill; escapes any ancestor overflow. Position carries a
// `left` plus either `top` (below the trigger) or `bottom` (flipped above),
// computed by placePopover to keep the popover inside the viewport.
function popoverStyle(pos) {
  const style = {
    position: 'fixed',
    width: 256,
    boxSizing: 'border-box',
    backgroundColor: '#252628',
    border: '1px solid rgba(255,255,255,0.18)',
    borderRadius: '6px',
    padding: '10px',
    color: '#f9f9f9',
    fontSize: '12px',
    lineHeight: 1.55,
    zIndex: 9999,
    boxShadow: '0 12px 32px rgba(0,0,0,0.6), 0 4px 12px rgba(0,0,0,0.4)',
    pointerEvents: 'none',
  }
  if (pos.top    != null) style.top    = pos.top
  if (pos.bottom != null) style.bottom = pos.bottom
  if (pos.left   != null) style.left   = pos.left
  if (pos.right  != null) style.right  = pos.right
  return style
}

// placePopover — decide where to position the popover relative to its
// trigger. One consistent rule so every "i" reads the same everywhere:
//
//   • Horizontally, the popover always hangs off the trigger's LEFT edge
//     and extends rightward ("bottom-right of the icon"). When that would
//     overflow the right margin, it's nudged left only as much as needed
//     to fit ("bottom-left") — it never detaches to the side of the icon.
//
//   • Vertically it sits BELOW the trigger. Only when there's no room
//     below does it flip ABOVE (top-right / top-left mirror).
//
// Width is treated as a hard 256px; we reserve a generous viewport margin
// so small drift (border, scrollbar, sub-pixel rendering) doesn't push
// the popover off-screen.
const POPOVER_WIDTH       = 256
const POPOVER_MARGIN      = 16  // generous to absorb rendering drift
const POPOVER_GAP         = 6
const POPOVER_HEIGHT_BUDGET = 220  // conservative height estimate for vertical fit

function placePopover(r) {
  const vw = document.documentElement.clientWidth  || window.innerWidth
  const vh = document.documentElement.clientHeight || window.innerHeight

  // Horizontal: anchor to the trigger's left edge, clamp into the viewport.
  // Prefer left-aligned (extends right); shift left only enough to fit.
  const left = Math.max(
    POPOVER_MARGIN,
    Math.min(r.left, vw - POPOVER_WIDTH - POPOVER_MARGIN),
  )

  // Vertical: below by default; flip above when below doesn't fit but above does.
  const fitsBelow = r.bottom + POPOVER_GAP + POPOVER_HEIGHT_BUDGET + POPOVER_MARGIN <= vh
  const fitsAbove = r.top    - POPOVER_GAP - POPOVER_HEIGHT_BUDGET - POPOVER_MARGIN >= 0

  if (!fitsBelow && fitsAbove) {
    return { left, bottom: vh - r.top + POPOVER_GAP }
  }
  return { left, top: r.bottom + POPOVER_GAP }
}

export function Spinner() {
  return (
    <div className="w-4 h-4 rounded-full border-2 border-[rgba(255,255,255,0.1)] border-t-[#5BB8D4] animate-spin flex-shrink-0" />
  )
}

// HighlightedText — renders `text` with case-insensitive matches of `query`
// wrapped in an amber-tinted <mark>. Returns the plain string when query is
// empty or doesn't match anywhere.
export function HighlightedText({ text, query }) {
  const t = text ?? ''
  const q = (query ?? '').trim()
  if (!q) return t
  const lower = t.toLowerCase()
  const needle = q.toLowerCase()
  const out = []
  let i = 0
  let key = 0
  while (i < t.length) {
    const found = lower.indexOf(needle, i)
    if (found === -1) { out.push(t.slice(i)); break }
    if (found > i) out.push(t.slice(i, found))
    out.push(
      <mark
        key={key++}
        className="bg-[rgba(232,184,74,0.30)] text-[#E8B84A] rounded-[2px] px-0.5"
      >
        {t.slice(found, found + needle.length)}
      </mark>
    )
    i = found + needle.length
  }
  return <>{out}</>
}

// ScoreBadge — dot + number, used in thumbnail overlays across grid /
// filmstrip / GroupLoupe / CompareView. The dot is tinted by the score's
// quality band (Pearl / Steel / Stone / Iron — the band tint palette,
// distinct from decision colours per DESIGN.md). Same 75 / 55 / 35
// cutoffs as Technical Overall and Personal score, so a quick scan of
// the grid reads the band ladder visually without having to parse each
// number. The number itself stays neutral white.
function scoreBandDot(score) {
  if (score >= 75) return 'bg-[#C8D8E4]'  // Pearl  — Excellent
  if (score >= 55) return 'bg-[#9CADBB]'  // Steel  — Good
  if (score >= 35) return 'bg-[#A09480]'  // Stone  — Fair
  return                  'bg-[#8A7878]'  // Iron   — Poor
}

export function ScoreBadge({ score }) {
  if (score == null) return (
    <span className="flex items-center gap-1">
      <span className="w-1.5 h-1.5 rounded-full bg-[#2a2a2a] flex-shrink-0" />
      <span className="text-xs font-mono text-[#8a8b8c]">—</span>
    </span>
  )
  return (
    <span className="flex items-center gap-1">
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${scoreBandDot(score)}`} />
      <span className="text-xs font-mono text-[#f9f9f9]">{score.toFixed(0)}</span>
    </span>
  )
}

// Canonical decision colors. Source of truth lives in index.css as
// --color-state-{keep,maybe,reject}. Mirrored here as Tailwind arbitrary
// values so any inline copy can be tinted without crossing the CSS-var line.
const DECISION_COLORS = {
  keep:   'text-[#7DB89A]',
  maybe:  'text-[#E8B84A]',
  reject: 'text-[#C97B7B]',
}

// DecisionWord — wraps inline copy in the right state tint.
// Use whenever the word "Keep", "Maybe", or "Reject" appears in user-facing
// strings. Pass `weight="medium"` (default) or "bold" to control emphasis.
//
//   <DecisionWord kind="keep">Keep</DecisionWord>
//   <DecisionWord kind="reject">Reject all 12?</DecisionWord>
//
// Renders a span — safe to nest inside <p>, <button>, <h2>, <kbd> labels.
export function DecisionWord({ kind, weight = 'medium', className = '', children }) {
  const tone = DECISION_COLORS[kind] || ''
  const w = weight === 'bold' ? 'font-semibold' : 'font-medium'
  return <span className={`${tone} ${w} ${className}`}>{children}</span>
}

// DecisionBadge — compact pill for K/M/R
export function DecisionBadge({ decision }) {
  if (!decision) return null
  const styles = {
    keep:   'bg-[rgba(125,184,154,0.20)] text-[#7DB89A]',
    maybe:  'bg-[rgba(232,184,74,0.20)] text-[#E8B84A]',
    reject: 'bg-[rgba(201,123,123,0.20)] text-[#C97B7B]',
  }
  const labels = { keep: 'K', maybe: 'M', reject: 'R' }
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${styles[decision]}`}>
      {labels[decision]}
    </span>
  )
}

// ScoreBar — horizontal fill bar for a 0-100 value
export function ScoreBar({ value, color = 'bg-[#a8a8a8]', tooltip }) {
  if (value == null) return (
    <div className="flex items-center gap-2" title={tooltip}>
      <div className="flex-1 bg-[#1b1c1e] rounded-[3px] h-1.5 border border-dashed border-[rgba(255,255,255,0.07)]" />
      <span className="text-xs font-mono text-[#f9f9f9] w-6 text-right">—</span>
    </div>
  )
  return (
    <div className="flex items-center gap-2" title={tooltip}>
      <div className="flex-1 bg-[#1b1c1e] rounded-[3px] h-1.5">
        <div className={`${color} h-1.5 rounded-[3px]`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs font-mono text-[#f9f9f9] w-6 text-right">{value.toFixed(0)}</span>
    </div>
  )
}

export function Toggle({ enabled, onChange, label }) {
  return (
    <button
      role="switch"
      aria-checked={enabled}
      aria-label={label}
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#101111] ${
        enabled ? 'bg-[#5BB8D4]' : 'bg-[#3a3a3a]'
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
          enabled ? 'translate-x-4' : 'translate-x-0'
        }`}
      />
    </button>
  )
}

// InfoTooltip — small "i" badge with a hover-revealed popover.
// Uses position:fixed (anchored via getBoundingClientRect) so the popover
// escapes any ancestor with overflow:auto/hidden — needed because the
// DetailView panel scrolls vertically and would otherwise clip the popover.

// HoverPopover — wraps any trigger element (e.g. a chip) so that hovering it
// reveals a popover with arbitrary content. Same fixed-positioning trick as
// InfoTooltip, just applied to a caller-supplied trigger instead of the "i"
// badge. Use this when the visible element itself should carry the hover
// affordance and you don't want a separate icon.
//   block — when true, wrap with a <div> so the trigger participates in
//           block-level layout (used by row-sized triggers like meter rows).
//   className — replaces the wrapper's own class. Needed when the wrapper is
//           itself a layout child (a flex-1 chart column): styling the
//           element *inside* the wrapper can't make the wrapper flex.
export function HoverPopover({ content, children, block = false, className }) {
  const triggerRef = useRef(null)
  const [pos, setPos] = useState(null)

  useLayoutEffect(() => {
    if (!pos) return
    const update = () => {
      const r = triggerRef.current?.getBoundingClientRect()
      if (r) setPos(placePopover(r))
    }
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [pos !== null])

  const open = () => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setPos(placePopover(r))
  }
  const close = () => setPos(null)

  const Wrapper = block ? 'div' : 'span'
  return (
    <Wrapper
      ref={triggerRef}
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={close}
      className={className ?? (block ? '' : 'inline-flex')}
    >
      {children}
      {pos && createPortal(
        <span role="tooltip" style={popoverStyle(pos)} className="normal-case tracking-normal font-normal">
          {content}
        </span>,
        document.body,
      )}
    </Wrapper>
  )
}

export function InfoTooltip({ children }) {
  const iconRef = useRef(null)
  const [pos, setPos] = useState(null)

  useLayoutEffect(() => {
    if (!pos) return
    const update = () => {
      const r = iconRef.current?.getBoundingClientRect()
      if (r) setPos(placePopover(r))
    }
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [pos !== null])

  const open = () => {
    const r = iconRef.current?.getBoundingClientRect()
    if (r) setPos(placePopover(r))
  }
  const close = () => setPos(null)

  return (
    <span className="relative inline-flex items-center">
      <span
        ref={iconRef}
        role="button"
        aria-label="More information"
        tabIndex={0}
        onMouseEnter={open}
        onMouseLeave={close}
        onFocus={open}
        onBlur={close}
        className={`inline-flex items-center justify-center w-3.5 h-3.5 cursor-help select-none transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-[#5BB8D4] rounded-full ${pos ? 'text-[#5BB8D4]' : 'text-current opacity-60 hover:opacity-100'}`}
      >
        <svg viewBox="0 0 14 14" className="w-full h-full" fill="currentColor" aria-hidden="true">
          <circle cx="7" cy="7" r="6.2" fill="none" stroke="currentColor" strokeWidth="1" />
          <circle cx="7" cy="4.2" r="0.9" />
          <rect x="6.25" y="5.9" width="1.5" height="4.4" rx="0.3" />
        </svg>
      </span>
      {pos && createPortal(
        <span role="tooltip" style={popoverStyle(pos)} className="normal-case tracking-normal font-normal">
          {children}
        </span>,
        document.body,
      )}
    </span>
  )
}

// Segmented — the app's one control for a small set of mutually exclusive
// choices. Lived inside SettingsModal until the Dashboard needed the same
// thing for its corpus + timeframe switches; two hand-rolled copies of a
// radiogroup is exactly the drift CLAUDE.md's "all enumerated choices use
// Segmented" rule exists to prevent.
//
// Arrow-key navigation is implemented rather than merely implied: role=radio
// promises it to screen-reader users, and a radiogroup that only responds to
// Tab is a broken promise.
export function Segmented({ options, value, onChange, ariaLabel, busy = false, compact = false }) {
  const onKeyDown = (e) => {
    const dir = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1
              : e.key === 'ArrowLeft'  || e.key === 'ArrowUp'   ? -1
              : 0
    if (!dir) return
    e.preventDefault()
    const i = options.findIndex(o => o.key === value)
    const next = options[(i + dir + options.length) % options.length]
    if (next) onChange(next.key)
  }

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={`inline-flex rounded-lg border border-[rgba(255,255,255,0.10)] overflow-hidden ${busy ? 'opacity-50 pointer-events-none' : ''}`}
    >
      {options.map(({ key, label, title }, i) => (
        <button
          key={key}
          role="radio"
          aria-checked={value === key}
          tabIndex={value === key ? 0 : -1}
          onClick={() => onChange(key)}
          title={title}
          className={`inline-flex items-center gap-1.5 ${compact ? 'px-4' : 'px-3'} py-1.5 text-xs font-medium transition-colors whitespace-nowrap focus:outline-none focus-visible:bg-[#1a1b1d] focus-visible:text-[#5BB8D4] ${i > 0 ? 'border-l border-[rgba(255,255,255,0.10)]' : ''} ${value === key ? 'bg-[#26282b] text-[#f9f9f9] shadow-[inset_0_-2px_0_#5BB8D4]' : 'text-[#9c9c9d] hover:opacity-70'}`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
