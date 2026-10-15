import { memo } from 'react'
import { Check } from 'lucide-react'
import { API } from '../api'
import { middleTruncate } from '../ui/format'
import { GroupOverlayChips, GroupBulkBar } from '../ui/TileChips'
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

// DecidedGroupTile — a single grid cell standing in for the members of one
// burst that landed in the CURRENT decision view (Keep, Maybe or Reject).
//
// Model: mirror of GroupTile, but for decided members instead of undecided
// ones. Where GroupTile always shows the still-undecided remainder of a
// burst, DecidedGroupTile shows the already-decided members visible in this
// view — the run App.jsx's displayGridItems clustering pass already computes
// (see the "Collapse burst members into group tiles" block, gated to
// decisionFilter === 'keep' | 'maybe' | 'reject'). It renders for a run of
// ONE too: a lone survivor is still a window onto its burst, and the peek
// panel is now the only way to see where its groupmates went. Reuses the same
// stacked-paper chrome as GroupTile, but:
//   - the footer names the decision and states the burst's size when this
//     bucket holds only part of it ("Group · 1 of 4 keeps");
//   - the ring can carry the decision colour (unlike GroupTile, whose members
//     are always undecided and thus always neutral);
//   - the bulk-action bar (GroupBulkBar) renders on the tile itself, for a run
//     of ≥2 — moving "all" of one photo is just a re-cull, which the peek
//     panel's K/M/R already does;
//   - in the Keeps view a mark-reviewed action rides along, because collapsing
//     the run took away ImageCard's per-photo check button for the auto-culled
//     keepers it now hides;
//   - there is no "AI ranking" concept for decided photos, so no AI pulse
//     chip — GroupOverlayChips renders with no prerankState;
//   - opening the tile (double-click / Enter) goes to DetailView carrying the
//     burst, not GroupLoupe — these photos are already decided, so there's
//     nothing to rank, only to look at properly and re-cull.
function DecidedGroupTileImpl({
  hero, images = [], members, decision, runImageIds, needsReviewIds,
  isSelected, onSelect, onOpen, onBulkDecide, onMarkReviewed,
  showFilename = true,
  isSelectMode = false,
  isDropHover = false,
  onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
  scrub,
}) {
  // The peek walks the WHOLE burst, not just this view's bucket — seeing which
  // members were culled differently is the point of it. Falls back to the
  // visible run if a caller doesn't supply the full list.
  const scrubMembers = members?.length ? members : images
  if (!hero) return null

  const count = images.length
  const total = scrubMembers.length
  // "3 of 5 rejects" whenever the rest of the burst went elsewhere — the fact
  // the survivor chips used to carry, said once and without a second colour.
  // The noun agrees with the number it follows, which in the "of" form is the
  // TOTAL (always ≥2, so always plural): "1 of 4 keeps", never "1 of 4 keep".
  const partial = count < total
  const plural = partial || count !== 1
  const decisionWord =
    decision === 'reject' ? (plural ? 'rejects' : 'reject') :
    decision === 'keep'   ? (plural ? 'keeps'   : 'keep')   :
                            (plural ? 'maybes'  : 'maybe')
  const countLabel = partial ? `${count} of ${total} ${decisionWord}` : `${count} ${decisionWord}`
  const borderColorClass =
    decision === 'reject' ? 'border-[#C97B7B]' :
    decision === 'keep'   ? 'border-[#7DB89A]' :
                            'border-[#E8B84A]'
  const reviewCount = needsReviewIds?.length ?? 0

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
  } else if (decision === 'keep') {
    ringClass = 'ring-1 ring-[rgba(125,184,154,0.40)] hover:ring-[rgba(125,184,154,0.65)]'
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
      title={disabled ? 'Groups can’t be multi-selected — exit Select first' : `${countLabel} in this group · click to peek · double-click to review`}
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

            {runImageIds && runImageIds.length > 0 && !isSelectMode && onBulkDecide && (
              <div className="absolute top-1.5 left-1.5 z-20 opacity-0 group-hover/run:opacity-100 transition-opacity">
                <GroupBulkBar
                  count={runImageIds.length}
                  viewDecision={decision}
                  onDecide={(target) => onBulkDecide(runImageIds, target)}
                />
              </div>
            )}

            {/* Mark-reviewed — the collapsed twin of ImageCard's hover check
                button, releasing this group's auto-culled keepers to
                Provenance. Hover-only and top-right, exactly where the
                per-photo button sat before the run was collapsed. */}
            {reviewCount > 0 && !isSelectMode && onMarkReviewed && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onMarkReviewed(needsReviewIds) }}
                onDoubleClick={(e) => e.stopPropagation()}
                title={`Mark ${reviewCount} reviewed — release to Provenance`}
                className="absolute top-1.5 right-1.5 z-20 inline-flex items-center gap-1 h-6 px-2 rounded-full bg-[rgba(7,8,10,0.65)] ring-1 ring-[rgba(125,184,154,0.50)] text-[#7DB89A] text-[11px] font-semibold leading-none tabular-nums opacity-0 group-hover/run:opacity-100 hover:bg-[rgba(125,184,154,0.25)] transition-opacity"
              >
                <Check size={13} strokeWidth={2.5} />
                {reviewCount}
              </button>
            )}
          </div>

          {showFilename && (
            <div className="px-2 py-1.5">
              <div className="h-[30px] space-y-0.5 overflow-hidden">
                <p className="text-xs text-[#cecece] truncate leading-tight">
                  <span className="text-[#9c9c9d]">Group · </span>{countLabel}
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
