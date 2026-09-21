import { memo } from 'react'
import { API } from '../api'
import { middleTruncate } from '../ui/format'
import { GroupOverlayChips, PartnerCountChips, GroupBulkBar } from '../ui/TileChips'
import { SELECTION_RING, SELECTION_RING_STRONG } from '../ui/buttons'

// GroupTile — a single grid cell standing in for the UNDECIDED remainder of a
// similarity group.
//
// Model: a group tile only ever represents the members of a burst that are
// still undecided. The instant a member is decided (K/M/R) it leaves the tile
// and renders as an individual card in its decision's filter view. So a group
// tile is ALWAYS all-undecided → always neutral, no decision chrome.
//
// Chrome: stacked-paper edges behind the tile + a top-left image overlay (the
// Layers "this is a group" identifier, plus an AI marker while the burst is
// being ranked). The footer is a single optional block ("Group · N undecided"
// + "Best: <hero filename>", labeled so it reads as the top pick rather than
// the group's name), gated by the F filename toggle — mirroring ImageCard's
// structure exactly so photo and group tiles are the same height, and both
// grow when filenames are hidden.
//
// `group` is the FULL, untouched group object — it flows to the loupe on open
// so ranking / batch actions / "Finish group" see the whole burst.
// `undecidedImages` is the render-only subset this tile displays.
//
// Single click selects; double click opens GroupLoupe (mirrors ImageCard).

// Shared stacked-paper chrome behind a hero card — two thin offset cards that
// read as a stack at a glance. Used by both GroupTile (undecided) and
// DecidedGroupTile (a same-decision run collapsed into one tile).
function StackedPaperEdges() {
  return (
    <>
      <div
        aria-hidden
        className="absolute inset-0 rounded-lg bg-[#161718] ring-1 ring-[rgba(255,255,255,0.06)]"
        style={{ transform: 'translate(6px, 6px)' }}
      />
      <div
        aria-hidden
        className="absolute inset-0 rounded-lg bg-[#161718] ring-1 ring-[rgba(255,255,255,0.06)]"
        style={{ transform: 'translate(3px, 3px)' }}
      />
    </>
  )
}

function GroupTileImpl({ group, undecidedImages = [], isSelected, onSelect, onOpen,
  // Filename row visibility — global Display preference (toggle / F key).
  showFilename = true,
  // True on the "All" view. Group tiles reserve the same right-edge slice as
  // ImageCard when filenames are hidden on All, so their images stay the same
  // width as photo tiles and the grid rows align. A group is always undecided,
  // so its bar is the neutral light-gray (never a decision colour).
  isAllView = false,
  // Drop-target affordances: GroupTile is a target for "join_group" moves
  // both in the main grid and inside the loupe rail. The parent decides
  // which payloads are valid; we just render the visual + forward events.
  isDropHover = false,
  // When the parent grid is in multi-select mode, groups cannot participate
  // (selection is per-photo). Render them visibly disabled.
  isSelectMode = false,
  onDragOver, onDragLeave, onDrop,
  // Drag source: dragging the whole tile onto another group tile merges the
  // two. Parent supplies the payload (same `photos` shape a card drag uses).
  onDragStart, onDragEnd,
  // Peek panel (hooks/useGroupScrub.js). A CLICK opens it — alongside, not
  // instead of, selecting the tile; double-click still opens the loupe.
  // Sweeping across the tile steps
  // through its members in a panel anchored below. Stable object identity, so
  // it doesn't defeat this component's memo().
  scrub,
}) {
  // Hero comes from the undecided subset — the AI's best pick if it's still
  // undecided, otherwise the top-scoring undecided member. (best_image_id may
  // point at a member that's since been decided and split out.)
  const hero =
    undecidedImages.find(img => img.id === group.best_image_id) || undecidedImages[0]
  if (!hero) return null

  const undecidedCount = undecidedImages.length

  // Ring is always neutral — a group tile only holds undecided members, so it
  // never carries a decision colour. Drop-hover (a gestured affordance) and
  // selection still win; select-mode disabled dims the whole tile.
  let ringClass
  if (isDropHover) {
    ringClass = `${SELECTION_RING_STRONG} scale-[1.02]`
  } else if (isSelected) {
    ringClass = SELECTION_RING
  } else {
    ringClass = 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  }
  // Groups can't be multi-selected → dim hard so they read as out-of-set.
  const opacity = isSelectMode ? 0.15 : 1

  // Reserve the right-edge slice to match ImageCard's decision bar (All view,
  // filenames hidden). Neutral light-gray — a group tile is always undecided.
  const reserveBar = isAllView && !showFilename

  // Single click = select only (keyboard cursor moves to this group; pressing
  // Enter/Space then opens the loupe). Double click = open the loupe directly.
  // tabIndex=-1: the parent wrapper div owns Tab focus via roving-tabIndex so
  // this button doesn't create a second Tab stop inside the same grid cell.
  const disabled = isSelectMode
  return (
    <button
      type="button"
      tabIndex={-1}
      onClick={disabled ? undefined : (e) => {
        onSelect?.()
        scrub?.onClick(e, group.images || undecidedImages)
      }}
      onDoubleClick={disabled ? undefined : onOpen}
      draggable={!disabled && !!onDragStart}
      onDragStart={disabled ? undefined : onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onMouseMove={disabled ? undefined : scrub?.onMove}
      aria-disabled={disabled || undefined}
      className={`relative block w-full text-left bg-transparent rounded-lg transition-[transform,opacity] ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      style={{ opacity }}
      title={disabled ? 'Groups can’t be multi-selected — exit Select first' : `${group.size} similar photos · click to peek · double-click to open`}
    >
      {/* Stacked-paper edges — peeking out behind the hero card to read as a
          stack at a glance. Two thin offset cards, restrained so the tile still
          feels native to the grid. Paired with the overlay Layers chip, these
          make "this is a group" unmistakable. */}
      <StackedPaperEdges />

      {/* Hero card */}
      <div className={`relative bg-[#161718] rounded-lg overflow-hidden flex items-stretch ${ringClass}`}>
        {/* Left column — image + optional footer. */}
        <div className="min-w-0 flex-1">
          {/* Image — with the group identifier overlay in the top-left corner. */}
          <div className="bg-[#07080a] aspect-[4/3] flex items-center justify-center overflow-hidden relative">
            <img
              src={`${API}/previews/${hero.id}`}
              alt={hero.filename}
              loading="lazy"
              className="max-h-full max-w-full object-contain"
            />
            <GroupOverlayChips prerankState={group.prerank_state} />
          </div>

          {/* Footer — mirrors ImageCard: a single optional block, gated by the F
              toggle. When hidden the image reclaims the height and the tile grows;
              group identity still reads from the stacked edges + overlay chip. */}
          {showFilename && (
            <div className="px-2 py-1.5">
              <div className="h-[30px] space-y-0.5 overflow-hidden">
                <p className="text-xs text-[#cecece] truncate leading-tight">
                  <span className="text-[#9c9c9d]">Group · </span>{undecidedCount} undecided
                </p>
                <p className="text-[10px] text-[#8a8b8c] truncate font-mono leading-tight" title={`Best pick: ${hero.filename}`}>
                  <span className="text-[#9c9c9d] font-sans">Best: </span>{middleTruncate(hero.filename)}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Right-edge slice — neutral light-gray, matches ImageCard's bar so
            group and photo images stay the same width on the All view when
            filenames are hidden. */}
        {reserveBar && (
          <div className="w-1.5 flex-none bg-[rgba(255,255,255,0.14)]" aria-hidden="true" />
        )}
      </div>
    </button>
  )
}

export const GroupTile = memo(GroupTileImpl)

// DecidedGroupTile — a single grid cell standing in for a run of ≥2 same-
// group photos that share the CURRENT decision view (Maybe or Reject).
//
// Model: mirror of GroupTile, but for decided members instead of undecided
// ones. Where GroupTile always shows the still-undecided remainder of a
// burst, DecidedGroupTile shows the already-decided members visible in this
// view — the run App.jsx's displayGridItems clustering pass already computes
// (see the "Cluster + annotate group members" block, gated to
// decisionFilter === 'reject' | 'maybe'). Reuses the same stacked-paper
// chrome as GroupTile, but:
//   - the footer names the decision ("Group · 3 maybes" / "Group · 2 rejects")
//     instead of "N undecided";
//   - the ring can carry the decision colour (unlike GroupTile, whose members
//     are always undecided and thus always neutral);
//   - the survivor chip (PartnerCountChips) and the bulk-action bar
//     (GroupBulkBar) render on the tile itself — the same components already
//     used when this run was exploded into individual ImageCards, just
//     relocated onto the single collapsed tile;
//   - there is no "AI ranking" concept for decided photos, so no AI pulse
//     chip — GroupOverlayChips renders with no prerankState;
//   - opening the tile (double-click / Enter) goes to GroupPartnersOverlay,
//     not GroupLoupe — these photos are already decided, so there's nothing
//     to rank/cull, only to review/re-cull.
function DecidedGroupTileImpl({
  hero, images = [], decision, groupChip, runImageIds,
  isSelected, onSelect, onOpen, onBulkDecide,
  showFilename = true,
  isSelectMode = false,
  isDropHover = false,
  onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
  scrub,
}) {
  // The peek walks the WHOLE burst, not just this view's bucket — seeing which
  // members were culled differently is the point of it. groupChip.members is
  // the full live member list, but it's only present when the group actually
  // has survivors elsewhere; without one, the visible run IS the whole group.
  const scrubMembers = groupChip?.members || images
  if (!hero) return null

  const count = images.length
  const decisionWord = decision === 'reject' ? 'rejects' : 'maybes'
  const borderColorClass = decision === 'reject' ? 'border-[#C97B7B]' : 'border-[#E8B84A]'

  // Ring: decision-coloured (matching ImageCard's own per-decision ring), with
  // selection/select-mode dimming taking the same priority order GroupTile
  // uses.
  let ringClass
  if (isDropHover) {
    ringClass = `${SELECTION_RING_STRONG} scale-[1.02]`
  } else if (isSelected) {
    ringClass = SELECTION_RING
  } else if (decision === 'reject') {
    ringClass = 'ring-1 ring-[rgba(201,123,123,0.40)] hover:ring-[rgba(201,123,123,0.65)]'
  } else {
    ringClass = 'ring-1 ring-[rgba(232,184,74,0.40)] hover:ring-[rgba(232,184,74,0.65)]'
  }
  const opacity = isSelectMode ? 0.15 : 1
  const disabled = isSelectMode

  return (
    <button
      type="button"
      tabIndex={-1}
      onClick={disabled ? undefined : (e) => {
        onSelect?.()
        scrub?.onClick(e, scrubMembers)
      }}
      onDoubleClick={disabled ? undefined : onOpen}
      draggable={!disabled && !!onDragStart}
      onDragStart={disabled ? undefined : onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onMouseMove={disabled ? undefined : scrub?.onMove}
      aria-disabled={disabled || undefined}
      className={`group/run relative block w-full text-left bg-transparent rounded-lg transition-[transform,opacity] ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      style={{ opacity }}
      title={disabled ? 'Groups can’t be multi-selected — exit Select first' : `${count} ${decisionWord} in this group · click to peek · double-click to review`}
    >
      <StackedPaperEdges />

      <div className={`relative bg-[#161718] rounded-lg overflow-hidden flex items-stretch border ${borderColorClass} ${ringClass}`}>
        <div className="min-w-0 flex-1">
          <div className="bg-[#07080a] aspect-[4/3] flex items-center justify-center overflow-hidden relative">
            <img
              src={`${API}/previews/${hero.id}`}
              alt={hero.filename}
              loading="lazy"
              className="max-h-full max-w-full object-contain"
            />
            <GroupOverlayChips />

            {groupChip && (
              <div className="absolute bottom-1.5 left-1.5 z-20">
                <PartnerCountChips
                  counts={groupChip.counts}
                  onOpen={onOpen}
                />
              </div>
            )}

            {runImageIds && runImageIds.length > 0 && !isSelectMode && onBulkDecide && (
              <div className="absolute top-1.5 left-1.5 z-20 opacity-0 group-hover/run:opacity-100 transition-opacity">
                <GroupBulkBar
                  count={runImageIds.length}
                  viewDecision={decision}
                  onDecide={(target) => onBulkDecide(runImageIds, target)}
                />
              </div>
            )}
          </div>

          {showFilename && (
            <div className="px-2 py-1.5">
              <div className="h-[30px] space-y-0.5 overflow-hidden">
                <p className="text-xs text-[#cecece] truncate leading-tight">
                  <span className="text-[#9c9c9d]">Group · </span>{count} {decisionWord}
                </p>
                <p className="text-[10px] text-[#8a8b8c] truncate font-mono leading-tight" title={`Best pick: ${hero.filename}`}>
                  <span className="text-[#9c9c9d] font-sans">Best: </span>{middleTruncate(hero.filename)}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </button>
  )
}

export const DecidedGroupTile = memo(DecidedGroupTileImpl)
