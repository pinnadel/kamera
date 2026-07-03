// TileChips — status chrome for grid tiles.
//
// The vocabulary is deliberately tiny now:
//   • ImageCard  → NO chips. Decision reads from the tile's colored ring +
//     footer tint; a K/M/R chip would just repeat the colour.
//   • GroupTile  → a small overlay in the image's top-left corner: a persistent
//     Layers icon ("this is a group") plus an AI marker ONLY while the burst is
//     actively being ranked. Groups are the sole exception to "nothing on the
//     image" — the identifier belongs where the eye lands, and a group tile only
//     ever holds undecided members so it carries no decision chrome at all.

import { Layers } from 'lucide-react'

// PartnerCountChips — how the surviving members of this photo's original burst
// were decided. Shown ONCE per group (on the run's first tile) at the border's
// lower-left in the Rejects/Maybes views, so the user can confirm a groupmate
// survived before trusting a cull. One worded pill per non-zero survivor bucket,
// e.g. "2 Keeps", "1 Maybe" — pluralized. The whole cluster is a button that
// opens the group-partners overlay (which lists the differently-culled photos
// and lets the user re-cull). Colors mirror the canonical decision palette.
const PARTNER_CHIP_STYLES = {
  keep:   { bg: 'rgba(125,184,154,0.22)', fg: '#7DB89A', singular: 'Keep',  plural: 'Keeps' },
  maybe:  { bg: 'rgba(232,184,74,0.22)',  fg: '#E8B84A', singular: 'Maybe', plural: 'Maybes' },
  reject: { bg: 'rgba(201,123,123,0.22)', fg: '#C97B7B', singular: 'Reject', plural: 'Rejects' },
}

export function PartnerCountChips({ counts, onOpen }) {
  if (!counts) return null
  const chips = ['keep', 'maybe', 'reject'].filter(kind => (counts[kind] || 0) > 0)
  if (chips.length === 0) return null
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onOpen?.() }}
      className="flex items-center gap-1 cursor-pointer hover:opacity-80 transition-opacity"
      title="See which group members were culled differently — click to review or re-cull"
    >
      {chips.map(kind => {
        const s = PARTNER_CHIP_STYLES[kind]
        const n = counts[kind]
        return (
          <span
            key={kind}
            className="inline-flex items-center h-[18px] px-1.5 rounded bg-[rgba(7,8,10,0.55)] backdrop-blur-sm text-[10px] font-semibold leading-none tabular-nums"
            style={{ color: s.fg, boxShadow: `inset 0 0 0 1px ${s.bg}` }}
          >
            {n} {n === 1 ? s.singular : s.plural}
          </span>
        )
      })}
    </button>
  )
}

// GroupOverlayChips — top-left image overlay for a group tile. Dark translucent
// backing so it reads on any photo. Layers icon is always shown; the AI pill
// (rainbow "AI" + cyan pulse) appears only while `prerankState === 'in_progress'`,
// with a hover title for detail. Other prerank states aren't surfaced here (not
// user-actionable on the tile).
export function GroupOverlayChips({ prerankState, count }) {
  const ranking = prerankState === 'in_progress'
  return (
    <div className="absolute top-1.5 left-1.5 flex items-center gap-1 pointer-events-none">
      <span
        className="inline-flex items-center gap-1 h-[18px] px-1.5 rounded bg-[rgba(7,8,10,0.72)] backdrop-blur-sm text-[#f0f0f0] text-[10px] font-semibold leading-none"
        title="Similar-photo group"
      >
        <Layers size={12} />
        {count != null && count}
      </span>
      {ranking && (
        <span
          className="inline-flex items-center gap-1 h-[18px] px-1.5 rounded bg-[rgba(7,8,10,0.72)] backdrop-blur-sm text-[10px] font-bold leading-none"
          title="AI is ranking this burst right now"
        >
          <span className="ai-text-rainbow uppercase tracking-wider">AI</span>
          <span className="w-1 h-1 rounded-full bg-[#5BB8D4] animate-pulse" />
        </span>
      )}
    </div>
  )
}
