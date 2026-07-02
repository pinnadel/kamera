import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Camera, Check, ChevronRight, Filter } from 'lucide-react'
import { DayPicker } from 'react-day-picker'
import 'react-day-picker/style.css'
import {
  EMPTY_FILTER,
  getNewestShotDate,
  getUniqueCameras,
  isFilterEmpty,
  toLocalDateKey,
} from '../filterUtils'
import { ACTIVE_PILL } from './buttons'

// Bottom-pill Filter control. The trigger is an icon-only funnel that opens the
// menu; active filters render as removable chips in the bottom bar (see App),
// NOT on the pill itself. Multiple categories can be active at once and combine
// with AND: Camera (OR across cameras) · Date range · one composition type.
//
// Submenus (Date, Camera) flip placement to `right-full` when there isn't
// room on the right of L1 — same viewport-aware logic as SortPill.
export function FilterPill({
  filters,
  setFilters,
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

  const f = filters ?? EMPTY_FILTER
  const hasFilters = !isFilterEmpty(f)

  const closeAll = () => { setSubmenu(null); onClose() }

  // Toggle a single camera in/out of the OR-set. Menu stays open so several
  // can be stacked in one visit.
  const toggleCamera = (cam) => {
    const on = f.cameras?.includes(cam)
    setFilters({
      ...f,
      cameras: on ? f.cameras.filter(c => c !== cam) : [...(f.cameras ?? []), cam],
    })
  }

  // Composition types are mutually exclusive: picking one replaces any other,
  // re-picking the active one clears it. Menu stays open.
  const setComposition = (kind) => {
    setFilters({ ...f, composition: f.composition === kind ? null : kind })
    setSubmenu(null)
  }

  const applyDate = (date) => { setFilters({ ...f, date }); closeAll() }
  const clearDate = () => setFilters({ ...f, date: null })

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
    if (f.date) {
      const from = parseISODay(f.date.from)
      const to   = parseISODay(f.date.to ?? f.date.from)
      setDateDraft({ from, to })
    } else {
      setDateDraft(newestDate ? { from: newestDate, to: newestDate } : undefined)
    }
    setSubmenu('date')
  }

  // The pill signals "filters are active" via the shared LOUD `ACTIVE_PILL`
  // treatment (cyan container + border); which filters are active is shown by
  // the chips in the bar. Open-without-filter falls back to neutral.
  const containerActive = hasFilters
    ? ACTIVE_PILL
    : open
      ? 'bg-[#1a1b1d] border-[#2a2b2d]'
      : 'border-transparent hover:bg-[rgba(255,255,255,0.06)]'

  const triggerText = hasFilters
    ? ''
    : open ? 'text-[#f0f0f0]' : 'text-[#cecece]'

  return (
    <div className="relative" data-dropdown="true">
      <div className={`inline-flex items-center rounded-lg border whitespace-nowrap transition-colors ${containerActive}`}>
        <button
          onClick={() => (open ? closeAll() : onOpen())}
          title="Filter"
          aria-label="Filter"
          className={`px-2 py-1 text-xs inline-flex items-center gap-1.5 ${triggerText}`}
        >
          <Filter size={15} strokeWidth={hasFilters ? 2.5 : 2} />
        </button>
      </div>

      {open && (
        <div className="absolute left-0 bottom-full mb-1 z-[70] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[170px] max-w-[calc(100vw-16px)] shadow-lg">
          {/* Date — opens the calendar submenu */}
          <div className="relative" ref={submenu === 'date' ? subRowRef : null}>
            <button
              onClick={() => (submenu === 'date' ? setSubmenu(null) : openDateSubmenu())}
              onMouseEnter={openDateSubmenu}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-opacity ${
                f.date ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
              }`}
            >
              <span>Date</span>
              <ChevronRight size={12} />
            </button>
          </div>

          <FilterRow
            label="Portraits"
            active={f.composition === 'portraits'}
            onSelect={() => setComposition('portraits')}
            onMouseEnter={() => setSubmenu(null)}
          />
          <FilterRow
            label="Landscape"
            active={f.composition === 'landscape'}
            onSelect={() => setComposition('landscape')}
            onMouseEnter={() => setSubmenu(null)}
          />
          <FilterRow
            label="Group photos"
            active={f.composition === 'group'}
            onSelect={() => setComposition('group')}
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
                    f.cameras?.length ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
                  }`}
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Camera size={12} /> Camera
                    {f.cameras?.length > 0 && (
                      <span className="text-[10px] text-[#5BB8D4] font-semibold">{f.cameras.length}</span>
                    )}
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
                {f.date && (
                  <button
                    onClick={() => { clearDate(); closeAll() }}
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
                    applyDate({ from, to })
                  }}
                  disabled={!dateDraft?.from}
                  className="px-3 py-1 rounded text-[11px] font-medium bg-[rgba(91,184,212,0.15)] border border-[rgba(91,184,212,0.30)] text-[#5BB8D4] hover:bg-[rgba(91,184,212,0.25)] disabled:opacity-40 transition-colors"
                >
                  Apply
                </button>
              </div>
            </div>
          )}

          {/* ── Camera submenu — multi-select (OR across cameras) ────────── */}
          {submenu === 'camera' && cameras.length > 1 && (
            <div
              className={`absolute bottom-0 ${
                placement === 'right' ? 'left-full ml-1' : 'right-full mr-1'
              } z-[71] bg-[#111214] border border-[#2a2b2d] rounded-lg py-1 min-w-[180px] max-w-[calc(100vw-16px)] shadow-lg`}
            >
              {cameras.map(cam => {
                const on = f.cameras?.includes(cam)
                return (
                  <button
                    key={cam}
                    onClick={() => toggleCamera(cam)}
                    className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between gap-2 transition-opacity ${
                      on ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
                    }`}
                  >
                    <span>{cam}</span>
                    {on && <Check size={13} className="text-[#5BB8D4] shrink-0" />}
                  </button>
                )
              })}
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
      className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between gap-2 transition-opacity ${
        active ? 'text-[#f9f9f9]' : 'text-[#6a6b6c] hover:opacity-70'
      }`}
    >
      <span>{label}</span>
      {active && <Check size={13} className="text-[#5BB8D4] shrink-0" />}
    </button>
  )
}

function parseISODay(s) {
  if (!s) return undefined
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
