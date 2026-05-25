import { memo, useState } from 'react'
import { Check } from 'lucide-react'
import { API } from '../api'
import { HighlightedText, ScoreBadge } from '../ui/primitives'
import { pickHeadlineScore } from '../ui/format'

// ImageCard — one tile in the grid.
// Memoized: in a 741-photo batch every polling tick used to trigger a re-render
// of all 741 cards because App.jsx's `setTabs` returned a new tabs array. With
// memo + stable handlers in App.jsx, only cards whose own props actually change
// (image data, selection, search query) re-render.
// Derive a short subfolder label from the photo's file_path relative to its
// source_folder. Returns null when the photo lives directly in the analysis
// root (no subfolder). Strips the conventional decision dirs (_Keeps etc.)
// so a moved photo's badge points at its ORIGINAL subfolder, not the bucket
// it landed in.
function subfolderLabel(image) {
  const path = image?.file_path
  const root = image?.source_folder
  if (!path || !root) return null
  if (!path.startsWith(root)) return null
  const rel = path.slice(root.length).replace(/^\/+/, '')
  const parts = rel.split('/')
  if (parts.length <= 1) return null
  // Drop trailing decision-bucket segments (we want the originating
  // subfolder, not the cull destination).
  const dropDirs = new Set(['_Keeps', '_Maybes', '_Trash'])
  const dirSegments = parts.slice(0, -1).filter(p => !dropDirs.has(p))
  if (dirSegments.length === 0) return null
  return dirSegments.join('/')
}

function ImageCardImpl({ image, isSelected, isMultiSelected = false, isSelectMode = false, isDropHover = false, searchQuery, modelInfo,
  // Drag-and-drop is opt-in: when `draggable` is true, the card carries
  // the supplied dataTransfer payload on dragstart. Source-of-truth lives
  // in the parent (App.jsx for grid, GroupLoupe for loupe).
  draggable = false, onDragStart, onDragEnd,
  // Quick-decide: when the tile is shown under a Maybe filter, the parent
  // can pass a handler so the user can K/R this single photo without
  // opening the loupe. Handler signature: quickDecide(decision). Parent
  // passes null when this surface shouldn't render (any non-Maybe view).
  quickDecide = null,
}) {
  // Per-card load state. RAW previews are generated on first /previews/<id>
  // request (1–3s for demosaicing), so a freshly-analyzed card needs its own
  // shimmer underlay; otherwise the trailing-skeleton slot disappears the
  // moment the image row hits /images but the <img> is still in flight,
  // leaving a dark empty tile.
  const [loaded, setLoaded] = useState(false)

  // Decision-tinted ring on undisturbed cards so the K/M/R state reads
  // immediately on the tile, not just from the corner badge. Selection
  // wins over decision tint so the cyan focus signal is never ambiguous.
  // Multi-select uses the same 1px cyan ring as single-focus selection —
  // the corner check badge already disambiguates "selected in the active
  // set" from "keyboard cursor". A heavier ring just adds visual noise.
  let border
  if (isDropHover) {
    border = 'ring-[3px] ring-[#5BB8D4] scale-[1.02]'
  } else if (isMultiSelected) {
    border = 'ring-1 ring-[#5BB8D4]'
  } else if (isSelected) {
    border = 'ring-1 ring-[#5BB8D4]'
  } else if (image.decision === 'keep') {
    border = 'ring-1 ring-[rgba(125,184,154,0.40)] hover:ring-[rgba(125,184,154,0.65)]'
  } else if (image.decision === 'maybe') {
    border = 'ring-1 ring-[rgba(232,184,74,0.40)] hover:ring-[rgba(232,184,74,0.65)]'
  } else if (image.decision === 'reject') {
    border = 'ring-1 ring-[rgba(201,123,123,0.40)] hover:ring-[rgba(201,123,123,0.65)]'
  } else {
    border = 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  }

  // Visual emphasis cues for the active selection set:
  //   - selected tiles stay full opacity (already the case)
  //   - non-selected tiles in select mode dim to 70% so the selection
  //     reads as the "active set" instead of getting lost in the grid
  //   - draggable selected tiles get a `grab` cursor (downgrades to
  //     `grabbing` while dragging via the browser default)
  const dimmedBySelectMode = isSelectMode && !isMultiSelected ? 'opacity-70' : ''
  const dragCursor = draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'
  const rejectedOpacity = image.decision === 'reject' ? 'opacity-[0.75]' : ''

  return (
    <div
      className={`bg-[#161718] rounded-lg overflow-hidden transition-all ${border} ${dimmedBySelectMode || rejectedOpacity} ${dragCursor}`}
      draggable={draggable}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
    >
      {/* Thumbnail — aspect-ratio scales the preview with the cell width, so
          "Largest" (2-col grid) gets a properly tall image instead of being
          letterboxed in a fixed h-36. */}
      <div className="bg-[#07080a] aspect-[4/3] flex items-center justify-center overflow-hidden relative">
        <img
          src={`${API}/previews/${image.id}`}
          alt={image.filename}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setLoaded(true)}
          className="max-h-full max-w-full object-contain"
        />
        {/* Shimmer overlay sits ON TOP of the <img> until it loads.
            Keeping the <img> at full opacity (rather than fading it in)
            ensures the browser's lazy loader treats it as visible and
            actually fires the GET — opacity-0 + lazy was deferring
            requests indefinitely. The overlay covers any partial-load
            flash; pointer-events-none lets clicks pass through. */}
        {!loaded && (
          <div className="absolute inset-0 shimmer pointer-events-none" aria-hidden="true" />
        )}
        {/* Decision badge overlay — top-right corner */}
        {image.decision && (
          <div className={`absolute top-1.5 right-1.5 w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold
            ${image.decision === 'keep'   ? 'bg-[rgba(125,184,154,0.85)] text-[#07080a]' : ''}
            ${image.decision === 'reject' ? 'bg-[rgba(201,123,123,0.85)] text-[#07080a]' : ''}
            ${image.decision === 'maybe'  ? 'bg-[rgba(232,184,74,0.85)] text-[#07080a]' : ''}
          `}>
            {image.decision === 'keep' ? 'K' : image.decision === 'reject' ? 'R' : 'M'}
          </div>
        )}
        {/* Multi-select check badge — top-left corner, only visible in
            select mode. Takes precedence over the compare badge's slot
            because both occupy the same corner and compare isn't a
            useful affordance during a multi-select gesture. */}
        {isSelectMode && (
          <div
            className={`absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-colors pointer-events-none
              ${isMultiSelected
                ? 'bg-[#5BB8D4] text-[#07080a]'
                : 'bg-[rgba(7,8,10,0.65)] ring-1 ring-[rgba(255,255,255,0.30)] text-transparent'}`}
            aria-hidden="true"
          >
            <Check size={12} strokeWidth={3} />
          </div>
        )}

        {/* Quick-decide buttons — bottom-center, only when parent enables
            (Maybe view) AND the photo is currently a Maybe AND select mode
            isn't active. Solo-tile analog of the GroupTile buttons. */}
        {quickDecide && image.decision === 'maybe' && !isSelectMode && (
          <div
            className="absolute bottom-1.5 left-1/2 -translate-x-1/2 flex items-center gap-1"
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => quickDecide('keep')}
              title="Keep this Maybe (K)"
              className="px-2 py-0.5 rounded text-[10px] font-bold leading-none backdrop-blur-sm transition-colors"
              style={{ backgroundColor: 'rgba(125,184,154,0.85)', color: '#07080a' }}
            >
              K
            </button>
            <button
              type="button"
              onClick={() => quickDecide('reject')}
              title="Reject this Maybe (R)"
              className="px-2 py-0.5 rounded text-[10px] font-bold leading-none backdrop-blur-sm transition-colors"
              style={{ backgroundColor: 'rgba(201,123,123,0.85)', color: '#07080a' }}
            >
              R
            </button>
          </div>
        )}
      </div>

      {/* Metadata — tinted by decision so K/M/R reads from the footer too. */}
      <div
        className="p-2 space-y-1"
        style={{
          backgroundColor:
            image.decision === 'keep'   ? 'rgba(125,184,154,0.14)' :
            image.decision === 'maybe'  ? 'rgba(232,184,74,0.14)'  :
            image.decision === 'reject' ? 'rgba(201,123,123,0.14)' :
            undefined,
        }}
      >
        {(() => {
          const sub = subfolderLabel(image)
          return sub && (
            <p className="text-[10px] font-mono text-[#5BB8D4] truncate" title={`Subfolder: ${sub}`}>
              {sub}/
            </p>
          )
        })()}
        <p className="text-xs text-[#9c9c9d] truncate" title={image.filename}>
          <HighlightedText text={image.filename} query={searchQuery} />
        </p>
        <div className="flex items-center gap-1.5">
          {/* Score badge prefers personal_score, but ONLY once the model
              hits the readiness gate (model_status === 'ready', i.e.
              training_size >= 50 AND beats baseline). Before that — in
              "untrained" or "learning" mode — personal_score exists but
              isn't trustworthy, so we fall back to technical overall.
              `pickHeadlineScore` encapsulates this rule for every
              ScoreBadge call site. The indigo "personal differs"
              indicator that used to ride alongside this badge is gone —
              once the badge IS the personal read, the indicator is
              redundant. */}
          <ScoreBadge score={pickHeadlineScore(image, modelInfo)} />
          {image.analysis_status === 'pending' && (
            <div className="shimmer flex-1 h-2.5 rounded" />
          )}
        </div>
      </div>
    </div>
  )
}

export const ImageCard = memo(ImageCardImpl)
