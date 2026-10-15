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
//
// PartnerCountChips ("2 Keeps", "1 Maybe") lived here until 2026-09-21. It
// counted, on the tile, where the rest of a burst was culled — which the peek
// panel now states on each frame as you scrub it, in the same palette. Two
// places saying one thing, and on a burst's lone survivor the chip was also
// the only way into the group at all. Both jobs moved to the panel.

import { Layers } from 'lucide-react'

// GroupBulkBar — a hover-revealed action row for a group run in the Rejects /
// Maybes grid. Answers "move this whole visible group at once" without opening
// the overlay: one button per target decision that ISN'T the current view
// (Maybes view → Keep all / Reject all; Rejects view → Keep all / Maybe all).
// Acts on the run's visible members only (this view's bucket) — survivors culled
// into other buckets are left alone. Buttons are tinted to their target
// decision, matching the canonical palette. Rendered above the group border
// (z-20) and only interactive on hover so it adds no permanent chrome.
const BULK_BTN_STYLES = {
  keep:   { bg: 'rgba(125,184,154,0.22)', fg: '#7DB89A', label: 'Keep all' },
  maybe:  { bg: 'rgba(232,184,74,0.22)',  fg: '#E8B84A', label: 'Maybe all' },
  reject: { bg: 'rgba(201,123,123,0.22)', fg: '#C97B7B', label: 'Reject all' },
}

export function GroupBulkBar({ count, viewDecision, onDecide }) {
  // Targets = the two survivor/cull buckets other than the one we're viewing.
  const targets = ['keep', 'maybe', 'reject'].filter(kind => kind !== viewDecision)
  return (
    <div
      className="flex items-center gap-1"
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      {targets.map(kind => {
        const s = BULK_BTN_STYLES[kind]
        return (
          <button
            key={kind}
            type="button"
            onClick={(e) => { e.stopPropagation(); onDecide(kind) }}
            className="inline-flex items-center h-[22px] px-2 rounded-md bg-[rgba(7,8,10,0.72)] backdrop-blur-sm text-[11px] font-semibold leading-none hover:opacity-80 transition-opacity"
            style={{ color: s.fg, boxShadow: `inset 0 0 0 1px ${s.bg}` }}
            title={`Move all ${count} photos in this group to ${s.label.replace(' all', '')}`}
          >
            {s.label}
          </button>
        )
      })}
    </div>
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
