// Single source of truth for the bottom-pill Filter. Keeps predicate +
// chip formatting + helper data extraction in one place so App and
// FilterPill never disagree.
//
// The filter is a *multi-filter* object — several categories can be active at
// once and combine with AND (they narrow the set):
//
//   { cameras: string[], date: {from,to} | null, composition: 'portraits' |
//     'landscape' | 'group' | null }
//
// Within the Camera category the selected cameras OR together (a photo passes
// if it was shot on ANY selected camera). Across categories it's AND. The
// composition types are mutually contradictory (Portraits = ≥1 face, Landscape
// = 0 faces, Group = >1 face) so only one is selectable at a time.

export const EMPTY_FILTER = { cameras: [], date: null, composition: null }

// True when no category is active — the identity filter (everything passes).
export function isFilterEmpty(filters) {
  if (!filters) return true
  return (
    (!filters.cameras || filters.cameras.length === 0) &&
    !filters.date &&
    !filters.composition
  )
}

// Predicate for a single composition type. Extracted so both the predicate and
// any future summary can share it.
function compositionPredicate(kind) {
  switch (kind) {
    case 'portraits': return (img) => (img.face_count ?? 0) >= 1
    case 'landscape': return (img) => (img.face_count ?? 0) === 0
    case 'group':     return (img) => (img.face_count ?? 0) > 1
    default:          return () => true
  }
}

// Returns true when the image passes ALL active filter categories. When the
// filter is empty every image passes (identity predicate).
export function filterPredicate(filters) {
  if (isFilterEmpty(filters)) return () => true

  const cameras = filters.cameras && filters.cameras.length
    ? new Set(filters.cameras)
    : null
  const date = filters.date
  const from = date?.from
  const to   = date ? (date.to ?? date.from) : null
  const comp = filters.composition ? compositionPredicate(filters.composition) : null

  return (img) => {
    // Camera: OR across the selected cameras.
    if (cameras && !cameras.has(img.camera)) return false
    // Date: from/to are 'YYYY-MM-DD' local-day strings, compared on the day
    // portion of `shot_at` (ISO timestamp) to avoid timezone-shift bugs.
    if (date) {
      if (!img.shot_at) return false
      const day = String(img.shot_at).slice(0, 10)
      if (day < from || day > to) return false
    }
    // Composition: AND with the rest.
    if (comp && !comp(img)) return false
    return true
  }
}

// Short label rendered next to the funnel icon when a filter is active.
// Date formatting collapses ranges in the same year and same month for
// brevity ("Aug 5 – 7" not "Aug 5, 2026 – Aug 7, 2026").
const MONTH_DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const MONTH_DAY_YEAR = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

function dateLabel(date) {
  const from = parseLocalDate(date.from)
  const to   = parseLocalDate(date.to ?? date.from)
  if (!from || !to) return 'Date'
  const sameDay  = date.from === (date.to ?? date.from)
  const sameYear = from.getFullYear() === to.getFullYear()
  if (sameDay) {
    return from.getFullYear() === new Date().getFullYear()
      ? MONTH_DAY.format(from)
      : MONTH_DAY_YEAR.format(from)
  }
  if (!sameYear) {
    return `${MONTH_DAY_YEAR.format(from)} – ${MONTH_DAY_YEAR.format(to)}`
  }
  // Same year: "Aug 5 – 7" if same month, else "Aug 28 – Sep 2".
  const sameMonth = from.getMonth() === to.getMonth()
  return sameMonth
    ? `${MONTH_DAY.format(from)} – ${to.getDate()}`
    : `${MONTH_DAY.format(from)} – ${MONTH_DAY.format(to)}`
}

const COMPOSITION_LABEL = {
  portraits: 'Portraits',
  landscape: 'Landscape',
  group: 'Group photos',
}

// One descriptor per active filter, for the removable chips in the bottom bar.
// Each chip carries a stable `key`, its display `label`, and a `next` filter
// object with that chip removed (so App can wire the × button without knowing
// the model internals). Cameras yield one chip each so they can be dropped
// individually.
export function getActiveFilterChips(filters) {
  if (isFilterEmpty(filters)) return []
  const chips = []

  for (const cam of filters.cameras ?? []) {
    chips.push({
      key: `camera:${cam}`,
      label: cam,
      next: { ...filters, cameras: filters.cameras.filter(c => c !== cam) },
    })
  }
  if (filters.composition) {
    chips.push({
      key: `composition:${filters.composition}`,
      label: COMPOSITION_LABEL[filters.composition] ?? filters.composition,
      next: { ...filters, composition: null },
    })
  }
  if (filters.date) {
    chips.push({
      key: 'date',
      label: dateLabel(filters.date),
      next: { ...filters, date: null },
    })
  }
  return chips
}

// Newest `shot_at` across the supplied images, returned as a Date in local
// time. Used to seed the date picker's default selection so the user lands
// on the most recently shot day rather than today (or January).
export function getNewestShotDate(images) {
  let max = null
  for (const img of images) {
    if (!img.shot_at) continue
    const d = new Date(img.shot_at)
    if (isNaN(d.getTime())) continue
    if (!max || d > max) max = d
  }
  return max
}

// Sorted list of distinct, non-null camera values. Driver for the camera
// submenu; if the list has 0 or 1 entries the submenu hides itself.
export function getUniqueCameras(images) {
  const set = new Set()
  for (const img of images) {
    if (img.camera) set.add(img.camera)
  }
  return Array.from(set).sort()
}

// Parse 'YYYY-MM-DD' as local-time midnight (avoids the off-by-one timezone
// shift you get from `new Date('2026-05-08')`, which parses as UTC).
function parseLocalDate(s) {
  if (!s) return null
  const [y, m, d] = s.split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

// Format a JS Date as 'YYYY-MM-DD' in local time (matches `shot_at` day key).
export function toLocalDateKey(date) {
  if (!date) return null
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}
