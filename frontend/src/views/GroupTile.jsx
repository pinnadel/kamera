import { memo } from 'react'
import { API } from '../api'
import { middleTruncate } from '../ui/format'
import { GroupOverlayChips } from '../ui/TileChips'

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
// + hero filename), gated by the F filename toggle — mirroring ImageCard's
// structure exactly so photo and group tiles are the same height, and both
// grow when filenames are hidden.
//
// `group` is the FULL, untouched group object — it flows to the loupe on open
// so ranking / batch actions / "Finish group" see the whole burst.
// `undecidedImages` is the render-only subset this tile displays.
//
// Single click selects; double click opens GroupLoupe (mirrors ImageCard).

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
    ringClass = 'ring-[3px] ring-[#5BB8D4] scale-[1.02]'
  } else if (isSelected) {
    ringClass = 'ring-1 ring-[#5BB8D4]'
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
      onClick={disabled ? undefined : onSelect}
      onDoubleClick={disabled ? undefined : onOpen}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      aria-disabled={disabled || undefined}
      className={`relative block w-full text-left bg-transparent rounded-lg transition-[transform,opacity] ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      style={{ opacity }}
      title={disabled ? 'Groups can’t be multi-selected — exit Select first' : `${group.size} similar photos · double-click to open`}
    >
      {/* Stacked-paper edges — peeking out behind the hero card to read as a
          stack at a glance. Two thin offset cards, restrained so the tile still
          feels native to the grid. Paired with the overlay Layers chip, these
          make "this is a group" unmistakable. */}
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
                <p className="text-[10px] text-[#6a6b6c] truncate font-mono leading-tight" title={hero.filename}>
                  {middleTruncate(hero.filename)}
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
