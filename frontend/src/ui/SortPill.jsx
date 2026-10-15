import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowLeft, ArrowRight, ChevronRight } from 'lucide-react'
// Glyph + icon vary by orientation: vertical axis (grid) reads top→bottom so
// desc is ↓; horizontal axis (filmstrip strip flows left→right) reads
// start→end so desc is →. Keeping desc/asc tied to "newest/highest first"
// regardless of axis means only the visual indicator changes.
const DIR_GLYPHS = {
  vertical:   { desc: '↓', asc: '↑' },
  horizontal: { desc: '→', asc: '←' },
}
const DIR_ICONS = {
  vertical:   { desc: ArrowDown,  asc: ArrowUp },
  horizontal: { desc: ArrowRight, asc: ArrowLeft },
}
import {
  ALL_METRICS,
  SCORE_GROUPS,
  TOP_LEVEL_OPTIONS,
  getMetricLabel,
  isScoreField,
  getDirectionShort,
  getDirectionTooltip,
} from '../sortMetrics'

// Bottom-pill Sort control. Two-level menu:
//   L1: Date · Name · Score ▸
//   L2 (under Score): metrics grouped by Technical / AI Quality / Personal /
//   EXIF, only metrics in `visibleMetrics` are rendered. Empty groups hide
//   their headline + divider.
// The leading direction toggle (↓/↑ arrow) flips dir without opening the
// menu — clicking the label opens it. Inside the menu, clicking the row that
// is already the active sort flips the direction too and leaves the menu
// open, so "newest → oldest" is a second click on the same row.
export function SortPill({
  sortField,
  sortDir,
  onSelectField,
  onToggleDir,
  visibleMetrics,
  open,
  onOpen,        // () => void — also closes other pills
  onClose,       // () => void
  orientation = 'vertical',  // 'vertical' (grid) | 'horizontal' (filmstrip)
}) {
  const DIR_GLYPH = DIR_GLYPHS[orientation] || DIR_GLYPHS.vertical
  const DIR_ICON  = DIR_ICONS[orientation]  || DIR_ICONS.vertical
  const [scoreOpen, setScoreOpen] = useState(false)
  // L2 submenu placement: prefer `right` (open to the right of L1) but flip
  // to `left` when there isn't enough room — keeps the submenu on-screen on
  // narrow viewports.
  const [scorePlacement, setScorePlacement] = useState('right')
  const scoreRowRef = useRef(null)
  useLayoutEffect(() => {
    if (!scoreOpen || !scoreRowRef.current) return
    const r = scoreRowRef.current.getBoundingClientRect()
    const SUBMENU_WIDTH = 240
    const fitsRight = r.right + SUBMENU_WIDTH + 12 <= window.innerWidth
    setScorePlacement(fitsRight ? 'right' : 'left')
  }, [scoreOpen])

  // Closing the L1 menu always collapses L2 too.
  const closeAll = () => { setScoreOpen(false); onClose() }

  // A row is the direction toggle for its own field. Clicking the field that
  // is already active flips desc↔asc and keeps the menu open, so the order can
  // be reversed by clicking the same row again — the leading pill arrow was
  // the only way to do it, and the greyed "Newest ↓" readout in each row read
  // as a control while doing nothing. Picking a *different* field selects it
  // and closes the stack, direction preserved.
  const pickField = (field) => {
    if (field === sortField) { onToggleDir(); return }
    onSelectField(field)
    closeAll()
  }

  // Row tooltip: on the active row it explains the flip, elsewhere it names
  // what the row will produce with the direction currently in force.
  const rowTitle = (field) =>
    field === sortField
      ? getDirectionTooltip(field, sortDir)
      : `Sort by ${getMetricLabel(field).toLowerCase()} — ${getDirectionShort(field, sortDir).toLowerCase()} first`

  const visibleSet = new Set(visibleMetrics)
  const groupedMetrics = SCORE_GROUPS.map(group => ({
    group,
    items: ALL_METRICS.filter(m => m.group === group && visibleSet.has(m.id)),
  })).filter(g => g.items.length > 0)

  const activeLabel = getMetricLabel(sortField)
  const DirIcon = sortDir === 'desc' ? DIR_ICON.desc : DIR_ICON.asc

  // The pill is two adjacent buttons inside one rounded container so the
  // active background spans both. Clicking the arrow flips dir; clicking the
  // label toggles the menu.
  const containerActive = open
    ? 'bg-[#1a1b1d] border-[#2a2b2d]'
    : 'border-transparent hover:bg-[rgba(255,255,255,0.06)]'

  return (
    <div className="relative" data-dropdown="true">
      <div className={`inline-flex items-center rounded-lg border whitespace-nowrap ${containerActive}`}>
        <button
          onMouseDown={(e) => e.preventDefault()}
          onClick={(e) => { e.stopPropagation(); onToggleDir() }}
          title={getDirectionTooltip(sortField, sortDir)}
          className={`ml-1 my-0.5 px-1.5 py-0.5 rounded-md text-xs inline-flex items-center border border-transparent hover:border-[rgba(255,255,255,0.20)] transition-colors ${open ? 'text-[#f0f0f0]' : 'text-[#cecece]'}`}
        >
          <DirIcon size={15} />
        </button>
        <button
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => (open ? closeAll() : onOpen())}
          title="Sort"
          className={`pr-2 pl-1 py-1 text-xs inline-flex items-center gap-1.5 transition-opacity ${open ? 'text-[#f0f0f0]' : 'text-[#cecece]'}`}
        >
          {activeLabel}
        </button>
      </div>

      {open && (
        <div className="absolute left-0 bottom-full mb-1 z-[70] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[140px] max-w-[calc(100vw-16px)] shadow-lg">
          {TOP_LEVEL_OPTIONS.map(({ field, label }) => (
            <button
              key={field}
              onClick={() => pickField(field)}
              onMouseEnter={() => setScoreOpen(false)}
              title={rowTitle(field)}
              aria-label={rowTitle(field)}
              className={`w-full text-left px-3 py-1.5 text-xs transition-opacity flex items-center justify-between gap-3 hover:opacity-70 ${
                sortField === field ? 'text-[#f9f9f9]' : 'text-[#8a8b8c]'
              }`}
            >
              <span>{label}</span>
              <span className={`text-xs whitespace-nowrap ${
                sortField === field ? 'text-[#cecece]' : 'text-[#8a8b8c]'
              }`}>
                {getDirectionShort(field, sortDir)} {DIR_GLYPH[sortDir]}
              </span>
            </button>
          ))}

          {groupedMetrics.length > 0 && (
            <div className="relative" ref={scoreRowRef}>
              <button
                onClick={() => setScoreOpen(v => !v)}
                onMouseEnter={() => setScoreOpen(true)}
                className={`w-full text-left px-3 py-1.5 text-xs transition-opacity flex items-center justify-between ${
                  isScoreField(sortField) ? 'text-[#f9f9f9]' : 'text-[#8a8b8c] hover:opacity-70'
                }`}
              >
                Score
                <ChevronRight size={12} />
              </button>

              {scoreOpen && (
                <div
                  className={`absolute bottom-0 ${
                    scorePlacement === 'right' ? 'left-full ml-1' : 'right-full mr-1'
                  } z-[71] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[220px] max-w-[calc(100vw-16px)] shadow-lg max-h-[80vh] overflow-y-auto`}
                  onMouseLeave={() => { /* keep open until outside-click */ }}
                >
                  {groupedMetrics.map(({ group, items }) => (
                    <div key={group}>
                      <div className="flex items-center gap-2 px-3 pt-2 pb-1">
                        <span className="text-[11px] font-semibold text-[#cecece] tracking-wide whitespace-nowrap">
                          {group}
                        </span>
                        <div className="flex-1 h-px bg-[#2a2b2d]" />
                      </div>
                      {items.map(metric => (
                        <button
                          key={metric.id}
                          onClick={() => pickField(metric.id)}
                          title={rowTitle(metric.id)}
                          aria-label={rowTitle(metric.id)}
                          className={`w-full text-left px-3 py-1.5 text-xs transition-opacity flex items-center justify-between gap-3 hover:opacity-70 ${
                            sortField === metric.id ? 'text-[#f9f9f9]' : 'text-[#8a8b8c]'
                          }`}
                        >
                          <span>{metric.label}</span>
                          <span className={`text-xs whitespace-nowrap ${
                            sortField === metric.id ? 'text-[#cecece]' : 'text-[#8a8b8c]'
                          }`}>
                            {getDirectionShort(metric.id, sortDir)} {DIR_GLYPH[sortDir]}
                          </span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
