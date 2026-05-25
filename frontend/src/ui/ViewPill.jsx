import { Grid2x2, GalleryThumbnails, Check } from 'lucide-react'

// Bottom-pill View control. Two-part button (icon + label) inside one rounded
// container, mirroring SortPill: clicking either half opens the overlay menu.
// The overlay has two sections:
//   1. Layout   — Grid / Filmstrip (configurable via `layoutOptions`)
//   2. Size     — contextual: tile size for Grid, strip-thumb size for Filmstrip
//
// The pill is presentational; the caller owns layout + size state and passes
// in the option arrays. Both surfaces (main grid + GroupLoupe) reuse this so
// the interaction model stays identical across views.
//
// Defaults below are tuned for the main grid:
//   Grid sizes (cols, fewer = larger tiles):
//     Small=8, Medium=6, Large=4, Largest=2
//   Filmstrip strip-thumb sizes (only the bottom strip; preview always fills):
//     Small=80, Medium=120, Large=180, Largest=260 px

export const GRID_SIZE_OPTIONS = [
  { label: 'Small',   value: 8 },
  { label: 'Medium',  value: 6 },
  { label: 'Large',   value: 4 },
  { label: 'Largest', value: 2 },
]
export const FILMSTRIP_SIZE_OPTIONS = [
  { label: 'Small',    value:  80 },
  { label: 'Medium',   value: 120 },
  { label: 'Large',    value: 180 },
  { label: 'Largest',  value: 260 },
]

// Icon + label carry the meaning; no descriptive hint needed.
export const DEFAULT_LAYOUT_OPTIONS = [
  { id: 'grid',      label: 'Grid',      Icon: Grid2x2 },
  { id: 'filmstrip', label: 'Filmstrip', Icon: GalleryThumbnails },
]

export function ViewPill({
  layout,                                       // string id of selected layout
  layoutOptions = DEFAULT_LAYOUT_OPTIONS,
  onSelectLayout,
  // Each layout has its own size scale. `sizeOptionsByLayout` maps
  // layout id → option array (`{label, value}`). `sizeByLayout` maps
  // layout id → currently selected `value`.
  sizeOptionsByLayout,
  sizeByLayout,
  onSelectSize,                                 // (layoutId, value) => void
  sizeLabelByLayout,                            // optional layoutId → header label override
  open,
  onOpen,                                       // closes other pills, opens this
  onClose,
}) {
  const current = layoutOptions.find(o => o.id === layout) || layoutOptions[0]
  const LayoutIcon = current.Icon
  const sizeOptions = sizeOptionsByLayout?.[layout] || []
  const sizeValue   = sizeByLayout?.[layout]
  const activeSize  = sizeOptions.find(o => o.value === sizeValue)
    ?? (sizeOptions.length
      ? sizeOptions.reduce((prev, curr) =>
          Math.abs(curr.value - sizeValue) < Math.abs(prev.value - sizeValue) ? curr : prev)
      : null)

  const sizeHeader = (sizeLabelByLayout && sizeLabelByLayout[layout]) || 'Size'

  const containerActive = open
    ? 'bg-[#1a1b1d] border-[#2a2b2d]'
    : 'border-transparent hover:bg-[rgba(255,255,255,0.06)]'

  // Both halves toggle the overlay; the user asked for icon-as-trigger
  // parity with the Sort pill's leading arrow — single-action, one menu,
  // both regions trigger it.
  const toggle = () => (open ? onClose() : onOpen())

  return (
    <div className="relative" data-dropdown="true">
      {/* Single button — both icon and label sit inside one target so the
          hover state applies uniformly to the whole pill (no per-half
          visual difference). Behaviour stays identical: any click toggles
          the overlay. */}
      <button
        onClick={toggle}
        title="View"
        aria-label="View"
        className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border text-xs whitespace-nowrap transition-opacity ${containerActive} ${open ? 'text-[#f0f0f0]' : 'text-[#cecece]'}`}
      >
        <LayoutIcon size={15} />
        {activeSize?.label || current.label}
      </button>

      {open && (
        <div className="absolute left-0 bottom-full mb-1 z-[70] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[126px] max-w-[calc(100vw-16px)] shadow-lg">

          {/* ── Layout ── (suppressed when there's only one layout option,
              since a single-row picker is just noise) */}
          {layoutOptions.length > 1 && (
            <>
              <div className="flex items-center gap-2 px-3 pt-2 pb-1">
                <span className="text-[11px] font-semibold text-[#cecece] tracking-wide whitespace-nowrap">Layout</span>
                <div className="flex-1 h-px bg-[#2a2b2d]" />
              </div>
              {layoutOptions.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  onClick={() => onSelectLayout(id)}
                  className={`w-full text-left px-3 py-1.5 text-xs transition-opacity inline-flex items-center gap-2 ${
                    layout === id ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
                  }`}
                >
                  <Icon size={15} />
                  {label}
                </button>
              ))}
            </>
          )}

          {/* ── Size (contextual label) ── */}
          {sizeOptions.length > 0 && (
            <>
              <div className="flex items-center gap-2 px-3 pt-2 pb-1 mt-1">
                <span className="text-[11px] font-semibold text-[#cecece] tracking-wide whitespace-nowrap">
                  {sizeHeader}
                </span>
                <div className="flex-1 h-px bg-[#2a2b2d]" />
              </div>
              {sizeOptions.map((opt) => {
                const isActive = activeSize && activeSize.value === opt.value
                return (
                  <button
                    key={opt.label}
                    onClick={() => onSelectSize(layout, opt.value)}
                    className={`w-full text-left px-3 py-1.5 text-xs transition-opacity flex items-center justify-between ${
                      isActive ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
                    }`}
                  >
                    <span>{opt.label}</span>
                    {isActive && <Check size={15} className="ml-4 text-[#5BB8D4]" />}
                  </button>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}
