import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Camera, ChevronRight, Filter, X } from 'lucide-react'
import { DayPicker } from 'react-day-picker'
import 'react-day-picker/style.css'
import {
  getActiveFilterLabel,
  getNewestShotDate,
  getUniqueCameras,
  toLocalDateKey,
} from '../filterUtils'

// Bottom-pill Filter control. Icon-only when no filter is set; expands to
// `[icon] active-label [×]` once a filter is active. Filters are mutually
// exclusive and live in App state (no localStorage persistence).
//
// Submenus (Date, Camera) flip placement to `right-full` when there isn't
// room on the right of L1 — same viewport-aware logic as SortPill.
export function FilterPill({
  filter,
  setFilter,
  images,
  open,
  onOpen,    // () => void — also closes other pills
  onClose,   // () => void
}) {
  const [submenu, setSubmenu] = useState(null) // 'date' | 'camera' | null
  const [placement, setPlacement] = useState('right')
  const subRowRef = useRef(null)

  // Date-picker draft selection (uncommitted). Seeded from current filter
  // when re-opening the date submenu, otherwise from the newest photo.
  const [dateDraft, setDateDraft] = useState(undefined)

  const newestDate = useMemo(() => getNewestShotDate(images), [images])
  const cameras    = useMemo(() => getUniqueCameras(images), [images])

  const closeAll = () => { setSubmenu(null); onClose() }

  const apply = (next) => { setFilter(next); closeAll() }
  const clear = () => setFilter(null)

  const activeLabel = getActiveFilterLabel(filter)

  // Measure available room when a submenu opens. If the trigger's right edge
  // plus submenu width would overflow, flip to left placement.
  useLayoutEffect(() => {
    if (!submenu || !subRowRef.current) return
    const r = subRowRef.current.getBoundingClientRect()
    // Width budget includes panel padding + border + 12px safety margin.
    // Date picker with 32px day cells × 7 + p-2 (16px) + border ≈ 256px.
    const SUB_WIDTH = submenu === 'date' ? 280 : 200
    setPlacement(r.right + SUB_WIDTH + 12 <= window.innerWidth ? 'right' : 'left')
  }, [submenu])

  // Seed the date draft when the submenu opens.
  const openDateSubmenu = () => {
    if (filter?.type === 'date') {
      const f = parseISODay(filter.from)
      const t = parseISODay(filter.to ?? filter.from)
      setDateDraft({ from: f, to: t })
    } else {
      setDateDraft(newestDate ? { from: newestDate, to: newestDate } : undefined)
    }
    setSubmenu('date')
  }

  // Pill button styling mirrors SortPill so the active background spans the
  // whole rounded container.
  const containerActive = open || filter
    ? 'bg-[#1a1b1d] border-[#2a2b2d]'
    : 'border-transparent hover:bg-[rgba(255,255,255,0.06)]'

  return (
    <div className="relative" data-dropdown="true">
      <div className={`inline-flex items-center rounded-lg border whitespace-nowrap transition-colors ${containerActive}`}>
        <button
          onClick={() => (open ? closeAll() : onOpen())}
          title={filter ? `Filter: ${activeLabel}` : 'Filter'}
          aria-label={filter ? `Filter: ${activeLabel}` : 'Filter'}
          className={`px-2 py-1 text-xs inline-flex items-center gap-1.5 ${open || filter ? 'text-[#f0f0f0]' : 'text-[#cecece]'}`}
        >
          <Filter size={15} />
          {activeLabel && <span>{activeLabel}</span>}
        </button>
        {filter && (
          <button
            onClick={(e) => { e.stopPropagation(); clear() }}
            title="Clear filter"
            aria-label="Clear filter"
            className="mr-1 my-0.5 px-1 py-0.5 rounded-md inline-flex items-center text-[#9c9c9d] hover:text-[#f0f0f0] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
          >
            <X size={13} />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute left-0 bottom-full mb-1 z-[70] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[170px] max-w-[calc(100vw-16px)] shadow-lg">
          {/* Date — opens the calendar submenu */}
          <div className="relative" ref={submenu === 'date' ? subRowRef : null}>
            <button
              onClick={() => (submenu === 'date' ? setSubmenu(null) : openDateSubmenu())}
              onMouseEnter={openDateSubmenu}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-opacity ${
                filter?.type === 'date' ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
              }`}
            >
              <span>Date</span>
              <ChevronRight size={12} />
            </button>
          </div>

          <FilterRow
            label="Portraits"
            active={filter?.type === 'portraits'}
            onSelect={() => apply({ type: 'portraits' })}
            onMouseEnter={() => setSubmenu(null)}
          />
          <FilterRow
            label="Landscape"
            active={filter?.type === 'landscape'}
            onSelect={() => apply({ type: 'landscape' })}
            onMouseEnter={() => setSubmenu(null)}
          />
          <FilterRow
            label="Group photos"
            active={filter?.type === 'group'}
            onSelect={() => apply({ type: 'group' })}
            onMouseEnter={() => setSubmenu(null)}
          />

          {cameras.length > 1 && (
            <>
              <div className="my-1 mx-3 h-px bg-[#2a2b2d]" />
              <div className="relative" ref={submenu === 'camera' ? subRowRef : null}>
                <button
                  onClick={() => setSubmenu(s => s === 'camera' ? null : 'camera')}
                  onMouseEnter={() => setSubmenu('camera')}
                  className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-opacity ${
                    filter?.type === 'camera' ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
                  }`}
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Camera size={12} /> Camera
                  </span>
                  <ChevronRight size={12} />
                </button>
              </div>
            </>
          )}

          {/* ── Date submenu — react-day-picker range ───────────────────── */}
          {submenu === 'date' && (
            <div
              className={`pca-day-picker absolute bottom-0 ${
                placement === 'right' ? 'left-full ml-1' : 'right-full mr-1'
              } z-[71] bg-[#111214] border border-[#2a2b2d] rounded-lg shadow-lg p-2 max-w-[calc(100vw-16px)]`}
              style={{
                // Inline CSS vars beat the package's bundled style.css regardless
                // of import order. Cell sizes are tuned so the calendar fits in
                // ~256px (7×32px + padding) — keeps the panel narrow enough to
                // stay on-screen even when the L1 menu is near the right edge.
                '--rdp-accent-color': '#5BB8D4',
                '--rdp-accent-background-color': 'rgba(91,184,212,0.18)',
                '--rdp-range_middle-background-color': 'rgba(91,184,212,0.18)',
                '--rdp-range_middle-color': '#cecece',
                '--rdp-range_start-color': '#07080a',
                '--rdp-range_end-color': '#07080a',
                '--rdp-range_start-date-background-color': '#5BB8D4',
                '--rdp-range_end-date-background-color': '#5BB8D4',
                '--rdp-day-height': '32px',
                '--rdp-day-width': '32px',
                '--rdp-day_button-height': '30px',
                '--rdp-day_button-width': '30px',
                '--rdp-day_button-border': '1px solid transparent',
                '--rdp-selected-border': '1px solid #5BB8D4',
                '--rdp-nav_button-height': '28px',
                '--rdp-nav_button-width': '28px',
                '--rdp-nav-height': '36px',
                '--rdp-weekday-padding': '4px 0',
                '--rdp-font-family': 'inherit',
              }}
            >
              <DayPicker
                mode="range"
                selected={dateDraft}
                onSelect={setDateDraft}
                defaultMonth={dateDraft?.from ?? newestDate ?? new Date()}
                showOutsideDays
                weekStartsOn={1}
              />
              <div className="flex items-center justify-end gap-2 pt-2 mt-1 border-t border-[#2a2b2d]">
                {filter?.type === 'date' && (
                  <button
                    onClick={() => { clear(); closeAll() }}
                    className="px-2 py-1 rounded text-[11px] text-[#9c9c9d] hover:text-[#f0f0f0] transition-colors"
                  >
                    Clear
                  </button>
                )}
                <button
                  onClick={() => {
                    if (!dateDraft?.from) return
                    const from = toLocalDateKey(dateDraft.from)
                    const to   = toLocalDateKey(dateDraft.to ?? dateDraft.from)
                    apply({ type: 'date', from, to })
                  }}
                  disabled={!dateDraft?.from}
                  className="px-3 py-1 rounded text-[11px] font-medium bg-[rgba(91,184,212,0.15)] border border-[rgba(91,184,212,0.30)] text-[#5BB8D4] hover:bg-[rgba(91,184,212,0.25)] disabled:opacity-40 transition-colors"
                >
                  Apply
                </button>
              </div>
            </div>
          )}

          {/* ── Camera submenu ─────────────────────────────────────────── */}
          {submenu === 'camera' && cameras.length > 1 && (
            <div
              className={`absolute bottom-0 ${
                placement === 'right' ? 'left-full ml-1' : 'right-full mr-1'
              } z-[71] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[180px] max-w-[calc(100vw-16px)] shadow-lg`}
            >
              {cameras.map(cam => (
                <button
                  key={cam}
                  onClick={() => apply({ type: 'camera', value: cam })}
                  className={`w-full text-left px-3 py-1.5 text-xs transition-opacity ${
                    filter?.type === 'camera' && filter.value === cam
                      ? 'text-[#f9f9f9]'
                      : 'text-[#6a6b6c] hover:opacity-70'
                  }`}
                >
                  {cam}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FilterRow({ label, active, onSelect, onMouseEnter }) {
  return (
    <button
      onClick={onSelect}
      onMouseEnter={onMouseEnter}
      className={`w-full text-left px-3 py-1.5 text-xs transition-opacity ${
        active ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
      }`}
    >
      {label}
    </button>
  )
}

function parseISODay(s) {
  if (!s) return undefined
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
