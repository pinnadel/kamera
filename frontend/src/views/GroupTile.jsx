import { memo } from 'react'
import { Check, Layers, Sparkles } from 'lucide-react'
import { API } from '../api'
import { DecisionWord } from '../ui/primitives'

// GroupTile — a single grid cell standing in for an entire similarity group.
//
// Replaces the old full-width GroupStrip. The visual goals:
// 1. Slot into the regular grid layout (same shape as ImageCard) so groups
//    don't fragment the scroll into wide-row breakouts.
// 2. Make the "this is a stack of N similar photos" reading instant via a
//    stacked-paper edge behind the tile + a count badge in the corner.
// 3. Communicate the group's status with strict semantics — a colored ring +
//    K/M/R badge appears only when EVERY member shares that decision.
//    Mixed-resolved groups (e.g. 1K + 4R) get a neutral ring + small ✓ so
//    "I kept a winner" can't be confused with "everything is kept here".
// 4. Single click selects, double click opens GroupLoupe (mirrors ImageCard).
//
// All decisions still happen per-photo; nothing here mutates state directly.

// RAW formats whose decode path differs from in-camera rendered files. When a
// group contains BOTH a RAW and a rendered version of the same shot, scores
// will differ (in-camera sharpening + tone curve are baked into the JPEG/HIF
// but absent from the RAW decode). Surfacing this as a tooltip on the tile
// preempts the "why are these scored so differently?" question.
const RAW_EXTS = new Set(['RAF', 'NEF', 'CR2', 'CR3', 'ARW', 'DNG'])
const RENDERED_EXTS = new Set(['JPG', 'JPEG', 'HIF', 'HEIC', 'PNG'])

function detectMixedFormats(images) {
  let hasRaw = false
  let hasRendered = false
  for (const img of images) {
    const dot = img.filename?.lastIndexOf('.')
    if (dot == null || dot < 0) continue
    const ext = img.filename.slice(dot + 1).toUpperCase()
    if (RAW_EXTS.has(ext))      hasRaw = true
    if (RENDERED_EXTS.has(ext)) hasRendered = true
    if (hasRaw && hasRendered) return true
  }
  return false
}

const DECISION_LABEL_PLURAL = { keep: 'kept', maybe: 'maybe', reject: 'rejected' }
const DECISION_BG = {
  keep:   'rgba(125,184,154,0.95)',
  maybe:  'rgba(232,184,74,0.95)',
  reject: 'rgba(201,123,123,0.95)',
}

function GroupTileImpl({ group, isSelected, onSelect, onOpen, filterContext,
  // Drop-target affordances: GroupTile is a target for "join_group" moves
  // both in the main grid and inside the loupe rail. The parent decides
  // which payloads are valid; we just render the visual + forward events.
  isDropHover = false,
  // When the parent grid is in multi-select mode, groups cannot participate
  // (selection is per-photo). Render them visibly disabled so the user reads
  // them as out-of-set rather than just "another tile".
  isSelectMode = false,
  onDragOver, onDragLeave, onDrop,
  // Quick-decide: when the tile is shown under a decision filter (Maybe,
  // typically), the user can K/R the entire matching subset without
  // opening the loupe. The handler receives (decision, imageIds) where
  // imageIds are the members currently matching filterContext.decision.
  // Parent gates this — pass null to disable. We render the buttons only
  // when there's actually something to decide (matchingCount > 0).
  quickDecide = null,
}) {
  const hero = group.images.find(img => img.id === group.best_image_id) || group.images[0]
  if (!hero) return null

  const counts = group.images.reduce(
    (acc, img) => {
      if (img.decision === 'keep')   acc.keep++
      else if (img.decision === 'maybe')  acc.maybe++
      else if (img.decision === 'reject') acc.reject++
      return acc
    },
    { keep: 0, maybe: 0, reject: 0 },
  )
  const total        = group.images.length
  const decidedTotal = counts.keep + counts.maybe + counts.reject
  const allDecided   = decidedTotal === total && total > 0
  const mixedFormats = detectMixedFormats(group.images)

  // Strict resolution semantics. A single-decision verdict (K/M/R ring +
  // badge) only renders when EVERY member shares that decision. The old
  // "best outcome wins" logic was misleading: a 1-keep + 4-reject group used
  // to render as green "Keep". Mixed-resolved gets its own neutral-with-✓
  // state and lets the progress chip carry the breakdown.
  let resolvedSingle = null
  if (allDecided) {
    if      (counts.keep   === total) resolvedSingle = 'keep'
    else if (counts.maybe  === total) resolvedSingle = 'maybe'
    else if (counts.reject === total) resolvedSingle = 'reject'
  }
  // Filter context — present when this tile is shown under an active
  // Keep/Maybe/Reject/Undecided or composition filter. Drives the
  // partial-match dim, the footer text override, and the bottom-right
  // "why am I in this filter" mini-badge.
  const fc = filterContext || null
  const partialMatch = fc && fc.matchingCount < fc.total

  // "Done" treatment: every member decided AND we're not under a filter
  // context (under a filter, the user is actively looking at this group, so
  // don't seal it). The tile keeps its full aspect-[4/3] shape (so the grid
  // stays even-rowed), and gets:
  //   - a strong dark scrim over the image
  //   - a centered "✓ Done" seal — decision-tinted for unanimous, neutral
  //     charcoal for mixed-resolved
  //   - K/M/R count footer instead of "Group · N similar"
  //   - progress chip suppressed (the seal carries the signal)
  //   - mild whole-tile dim, with a hover lift so it's still discoverable
  // Opens normally so the user can review or undo.
  const isDone = allDecided && !fc

  // Ring + footer tint only on single-decision-all-same. Mixed-resolved is
  // intentionally neutral so the ring stops shouting a decision the group
  // doesn't unanimously hold. Drop-hover beats every other state because
  // it's a transient affordance the user has actively gestured for.
  // When the tile is collapsed (isDone), the ring goes fully neutral — a
  // finished group shouldn't shout a decision color.
  let ringClass
  if (isDropHover) {
    ringClass = 'ring-[3px] ring-[#5BB8D4] scale-[1.02]'
  } else if (isSelected) {
    ringClass = 'ring-1 ring-[#5BB8D4]'
  } else if (isDone) {
    ringClass = 'ring-1 ring-[rgba(255,255,255,0.04)] hover:ring-[rgba(255,255,255,0.12)]'
  } else if (resolvedSingle === 'keep') {
    ringClass = 'ring-1 ring-[rgba(125,184,154,0.65)] hover:ring-[rgba(125,184,154,0.90)]'
  } else if (resolvedSingle === 'maybe') {
    ringClass = 'ring-1 ring-[rgba(232,184,74,0.65)] hover:ring-[rgba(232,184,74,0.90)]'
  } else if (resolvedSingle === 'reject') {
    ringClass = 'ring-1 ring-[rgba(201,123,123,0.65)] hover:ring-[rgba(201,123,123,0.90)]'
  } else {
    ringClass = 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  }
  // Footer tint suppressed in the done state — the ✓ Done pill carries the
  // signal and a tinted footer would compete with the dimming.
  const footerTint = isDone ? undefined :
    resolvedSingle === 'keep'   ? 'rgba(125,184,154,0.22)' :
    resolvedSingle === 'maybe'  ? 'rgba(232,184,74,0.22)'  :
    resolvedSingle === 'reject' ? 'rgba(201,123,123,0.22)' :
    undefined

  // Top-right corner letter badge — suppressed for done groups (the centered
  // seal carries that signal more strongly); shown for unanimous in-progress
  // outliers (rare: would only fire if a group somehow has unanimous-decided
  // members but still has undecideds, which can't actually happen with
  // current logic — kept for safety).
  const decisionBadge = !isDone && (
    resolvedSingle === 'keep'   ? { letter: 'K', bg: DECISION_BG.keep }   :
    resolvedSingle === 'maybe'  ? { letter: 'M', bg: DECISION_BG.maybe }  :
    resolvedSingle === 'reject' ? { letter: 'R', bg: DECISION_BG.reject } :
    null
  )
  // Centered "Done" seal colour. Tinted for unanimous, neutral charcoal for
  // mixed-resolved. The seal sits on top of the scrim, so its contrast is
  // already strong — we don't need to over-saturate the tint.
  const doneSealBg =
    resolvedSingle === 'keep'   ? DECISION_BG.keep  :
    resolvedSingle === 'maybe'  ? DECISION_BG.maybe :
    resolvedSingle === 'reject' ? DECISION_BG.reject :
    'rgba(22,23,24,0.92)'  // neutral for mixed-resolved
  const doneSealFg =
    resolvedSingle ? '#07080a' : '#f0f0f0'
  // Push Mixed-formats chip down whenever the K/M/R letter badge occupies
  // top-right. The done state no longer puts anything there.
  const mixedFormatsTop = decisionBadge ? 'top-9' : 'top-1.5'

  // Bottom-left progress chip — visible while there's decision diversity
  // (≥2 outcomes) or undecideds remaining, AND the tile isn't in the done
  // state (the Done pill replaces it). Without the done gate, a 1K+4R group
  // would still show the chip even after collapsing.
  const decisionDiversity =
    (counts.keep > 0 ? 1 : 0) + (counts.maybe > 0 ? 1 : 0) + (counts.reject > 0 ? 1 : 0)
  const showProgressChip = !isDone && decidedTotal > 0 && (decisionDiversity > 1 || decidedTotal < total)

  // Opacity layers: select-mode disabled (groups can't participate) is the
  // strongest dim — overrides every other state so the disabled read
  // dominates. The done state is handled via Tailwind classes (below) rather
  // than inline opacity, so :hover can lift it; other states stay inline.
  const opacity =
    isSelectMode               ? 0.15 :
    !isDone && resolvedSingle === 'reject' ? 0.45 :
    !isDone && partialMatch    ? 0.55 :
    1
  // Done state opacity. Lighter than before (0.78 → 1.0 hover) because the
  // scrim + centered seal already do most of the "this is sealed" work; we
  // just nudge the whole tile back so it doesn't compete with in-progress
  // groups for attention. Tailwind class is required (not inline style) so
  // :hover can lift it — inline opacity wins specificity against any hover
  // class for the same property.
  const doneOpacityClass = isDone ? 'opacity-[0.78] hover:opacity-100' : ''

  // Footer text — match summary when under a filter, K/M/R breakdown when
  // fully decided (lifting the progress chip's info into the always-visible
  // footer since the chip is suppressed in the done state), otherwise the
  // default "Group · N similar".
  let footerLine
  if (fc) {
    if (fc.decision) {
      const label = fc.decision === 'undecided'
        ? <>to decide</>
        : <DecisionWord kind={fc.decision}>{DECISION_LABEL_PLURAL[fc.decision]}</DecisionWord>
      footerLine = <>{fc.matchingCount} of {fc.total} {label}</>
    } else {
      footerLine = <>{fc.matchingCount} of {fc.total} {(fc.label || 'match').toLowerCase()}</>
    }
  } else if (isDone) {
    const parts = []
    if (counts.keep   > 0) parts.push(<span key="k" className="text-[#7DB89A] font-mono">{counts.keep}K</span>)
    if (counts.maybe  > 0) parts.push(<span key="m" className="text-[#E8B84A] font-mono">{counts.maybe}M</span>)
    if (counts.reject > 0) parts.push(<span key="r" className="text-[#C97B7B] font-mono">{counts.reject}R</span>)
    const joined = []
    parts.forEach((p, i) => {
      if (i > 0) joined.push(<span key={`sep-${i}`} className="text-[#4a4b4c] mx-1">·</span>)
      joined.push(p)
    })
    footerLine = <span className="inline-flex items-center">{joined}</span>
  } else {
    footerLine = <><span className="text-[#9c9c9d]">Group · </span>{group.size} similar</>
  }

  // Bottom-right filter-context mini-badge — shows the filter's decision
  // letter on its colour, so users immediately see why this group surfaces
  // in the current filter. Only for decision filters with partial match;
  // the top-right resolved/✓ badge still reflects overall group state and
  // these complement rather than conflict.
  const filterMiniBadge =
    fc && fc.decision && fc.decision !== 'undecided' && partialMatch
      ? { letter: fc.decision === 'keep' ? 'K' : fc.decision === 'maybe' ? 'M' : 'R',
          bg:     DECISION_BG[fc.decision] }
      : null

  const innerCard = (
    <>
      <div className="bg-[#07080a] aspect-[4/3] flex items-center justify-center overflow-hidden relative">
        <img
          src={`${API}/previews/${hero.id}`}
          alt={hero.filename}
          loading="lazy"
          className="max-h-full max-w-full object-contain"
        />

        {/* Done scrim — renders directly over the image but BEFORE the
            corner badges so the Layers count and mixed-formats chip stay
            readable on top. DOM order = paint order for positioned siblings
            without z-index. */}
        {isDone && (
          <div
            aria-hidden
            className="absolute inset-0 bg-[rgba(7,8,10,0.62)]"
          />
        )}

        {/* Group count badge — top-left, neutral surface so it reads as
            metadata, not a status colour. Appended with a small prerank
            marker so users can tell at a glance whether the AI has already
            ranked this burst, is currently looking at it, or hasn't gotten
            to it yet. The marker stays in the same chip as the count so we
            don't multiply chrome — same backdrop, same position.

            Marker semantics:
              pending          → static rainbow "AI" tag. "AI hasn't looked
                                 here yet; you can still manually cull
                                 single photos upfront."
              in_progress      → rainbow "AI" + cyan pulse dot. Exactly one
                                 tile in the grid carries this at any moment.
              ready            → faint star icon. "Opening this loupe will
                                 be instant; AI's pick is already cached."
              near_duplicates  → neutral "≈" glyph. "AI determined these
                                 frames are visually near-identical; the
                                 score-based pick applies."
              not_applicable / undefined → nothing rendered. */}
        <span
          className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none bg-[rgba(7,8,10,0.78)] text-[#f0f0f0] backdrop-blur-sm flex items-center gap-1"
        >
          <Layers size={12} />
          {group.size}
          {group.prerank_state === 'pending' && (
            <span
              className="ai-text-rainbow text-[9px] font-semibold uppercase tracking-wider leading-none ml-0.5"
              title="AI hasn't ranked this burst yet — you can still cull manually"
            >
              AI
            </span>
          )}
          {group.prerank_state === 'in_progress' && (
            <span
              className="inline-flex items-center gap-0.5 ml-0.5"
              title="AI is ranking this burst right now"
            >
              <span className="ai-text-rainbow text-[9px] font-semibold uppercase tracking-wider leading-none">
                AI
              </span>
              <span className="w-1 h-1 rounded-full bg-[#5BB8D4] animate-pulse" />
            </span>
          )}
          {group.prerank_state === 'ready' && (
            <Sparkles
              size={10}
              className="text-[#cecece] ml-0.5 opacity-80"
              aria-label="AI rank ready"
            />
          )}
          {group.prerank_state === 'near_duplicates' && (
            <span
              className="ml-0.5 text-[11px] leading-none text-[#9c9c9d] font-semibold"
              title="Near-duplicate frames — using score-based pick"
              aria-label="Near-duplicate frames; using score-based pick"
            >
              ≈
            </span>
          )}
        </span>

        {/* Mixed-format badge — surfaces when a group contains both RAW and
            rendered (JPEG/HIF) versions of the same shot. The two versions
            will score differently because in-camera processing applies
            sharpening + tone curves the RAW decode skips. */}
        {mixedFormats && (
          <span
            className={`absolute ${mixedFormatsTop} right-1.5 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase leading-none bg-[rgba(7,8,10,0.78)] text-[#E8B84A] backdrop-blur-sm border border-[rgba(232,184,74,0.40)]`}
            title="This group contains both RAW and rendered (JPEG/HIF) versions — scores will differ because in-camera processing isn't applied to the RAW decode"
          >
            Mixed formats
          </span>
        )}

        {/* Top-right: single-decision K/M/R badge */}
        {decisionBadge && (
          <div
            className="absolute top-1.5 right-1.5 w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold text-[#07080a]"
            style={{ backgroundColor: decisionBadge.bg }}
          >
            {decisionBadge.letter}
          </div>
        )}

        {/* Done seal — centered "✓ Group done" pill that sits on top of
            the scrim (rendered earlier) and visually seals the completed
            group. Tinted with the decision colour when unanimous, neutral
            charcoal for mixed-resolved. Rendered last in the image div so
            it paints above every other overlay. */}
        {isDone && (
          <div
            className="absolute inset-0 flex items-center justify-center pointer-events-none"
          >
            <div
              className="px-3 py-1.5 rounded-md flex items-center gap-1.5 text-xs font-semibold leading-none shadow-lg backdrop-blur-sm"
              style={{ backgroundColor: doneSealBg, color: doneSealFg }}
              title="All decisions made — click to review"
            >
              <Check size={14} strokeWidth={2.75} />
              Group done
            </div>
          </div>
        )}

        {/* Aggregated decision progress — bottom-left. */}
        {showProgressChip && (
          <div className="absolute bottom-1.5 left-1.5 flex items-center gap-1 text-[10px] font-mono leading-none bg-[rgba(7,8,10,0.78)] backdrop-blur-sm rounded px-1.5 py-0.5">
            {counts.keep > 0 && (
              <span className="text-[#7DB89A]">{counts.keep}K</span>
            )}
            {counts.maybe > 0 && (
              <span className="text-[#E8B84A]">{counts.maybe}M</span>
            )}
            {counts.reject > 0 && (
              <span className="text-[#C97B7B]">{counts.reject}R</span>
            )}
            {!allDecided && (
              <span className="text-[#6a6b6c]">/ {group.size}</span>
            )}
          </div>
        )}

        {/* Filter-context mini-badge — bottom-right, only on partial
            decision-filter matches. */}
        {filterMiniBadge && (
          <div
            className="absolute bottom-1.5 right-1.5 w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold text-[#07080a]"
            style={{ backgroundColor: filterMiniBadge.bg }}
            title="Matching member in this filter"
          >
            {filterMiniBadge.letter}
          </div>
        )}

        {/* Quick-decide buttons — bottom-center, only when the tile is
            shown under a Maybe filter AND there are matching members. Lets
            the user batch-K or batch-R every Maybe member in the group
            without opening the loupe. Stops propagation so clicks don't
            also fire onSelect/onOpen on the wrapper button. The buttons
            sit above the scrim and below the centered Done seal (which
            only renders when isDone, mutually exclusive with this surface
            because isDone requires no active fc). */}
        {quickDecide && fc?.decision === 'maybe' && fc.matchingCount > 0 && !isDone && (
          <div
            className="absolute bottom-1.5 left-1/2 -translate-x-1/2 flex items-center gap-1"
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => quickDecide('keep')}
              title={fc.matchingCount === 1 ? 'Keep this Maybe' : `Keep all ${fc.matchingCount} Maybes (K)`}
              className="px-2 py-0.5 rounded text-[10px] font-bold leading-none backdrop-blur-sm transition-colors"
              style={{ backgroundColor: 'rgba(125,184,154,0.85)', color: '#07080a' }}
            >
              K{fc.matchingCount > 1 ? ` · ${fc.matchingCount}` : ''}
            </button>
            <button
              type="button"
              onClick={() => quickDecide('reject')}
              title={fc.matchingCount === 1 ? 'Reject this Maybe' : `Reject all ${fc.matchingCount} Maybes (R)`}
              className="px-2 py-0.5 rounded text-[10px] font-bold leading-none backdrop-blur-sm transition-colors"
              style={{ backgroundColor: 'rgba(201,123,123,0.85)', color: '#07080a' }}
            >
              R{fc.matchingCount > 1 ? ` · ${fc.matchingCount}` : ''}
            </button>
          </div>
        )}
      </div>

      {/* Metadata strip — mirrors ImageCard, tinted by resolvedSingle so the
          decision reads from the footer too. Footer text switches to match
          summary when under a filter. */}
      <div className="p-2 space-y-1" style={{ backgroundColor: footerTint }}>
        <p className="text-xs text-[#cecece] truncate">{footerLine}</p>
        <p className="text-[10px] text-[#6a6b6c] truncate font-mono" title={hero.filename}>
          {hero.filename}
        </p>
      </div>
    </>
  )

  // Single click = select only (keyboard cursor moves to this group; pressing
  // Enter/Space then opens the loupe). Double click = open the loupe directly.
  // Mirrors ImageCard's click model so groups and photos feel symmetric.
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
      className={`relative block w-full text-left bg-transparent rounded-lg transition-[transform,opacity] ${doneOpacityClass} ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
      style={{ opacity }}
      title={disabled ? 'Groups can’t be multi-selected — exit Select first' : `${group.size} similar photos · double-click to open`}
    >
      {/* Stacked-paper edges — peeking out behind the hero card to read as a
          stack at a glance. Two thin offset cards, restrained so the tile
          still feels native to the grid. */}
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
      <div className={`relative bg-[#161718] rounded-lg overflow-hidden ${ringClass}`}>
        {innerCard}
      </div>
    </button>
  )
}

export const GroupTile = memo(GroupTileImpl)
