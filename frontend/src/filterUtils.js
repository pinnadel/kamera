// Single source of truth for the bottom-pill Filter. Keeps predicate +
// label formatting + helper data extraction in one place so App and
// FilterPill never disagree.

// Returns true when the image passes the active filter. When `filter` is
// null/undefined every image passes (identity predicate).
export function filterPredicate(filter) {
  if (!filter) return () => true
  switch (filter.type) {
    case 'date': {
      // from/to are 'YYYY-MM-DD' local-day strings. We compare on the day
      // portion of `shot_at` (ISO timestamp) to avoid timezone-shift bugs.
      const from = filter.from
      const to   = filter.to ?? filter.from
      return (img) => {
        if (!img.shot_at) return false
        const day = String(img.shot_at).slice(0, 10)
        return day >= from && day <= to
      }
    }
    case 'portraits':
      return (img) => (img.face_count ?? 0) >= 1
    case 'landscape':
      return (img) => (img.face_count ?? 0) === 0
    case 'group':
      return (img) => (img.face_count ?? 0) > 1
    case 'camera':
      return (img) => img.camera === filter.value
    default:
      return () => true
  }
}

// Short label rendered next to the funnel icon when a filter is active.
// Date formatting collapses ranges in the same year and same month for
// brevity ("Aug 5 – 7" not "Aug 5, 2026 – Aug 7, 2026").
const MONTH_DAY = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const MONTH_DAY_YEAR = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

export function getActiveFilterLabel(filter) {
  if (!filter) return null
  switch (filter.type) {
    case 'date': {
      const from = parseLocalDate(filter.from)
      const to   = parseLocalDate(filter.to ?? filter.from)
      if (!from || !to) return 'Date'
      const sameDay  = filter.from === (filter.to ?? filter.from)
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
    case 'portraits': return 'Portraits'
    case 'landscape': return 'Landscape'
    case 'group':     return 'Group photos'
    case 'camera':    return filter.value
    default:          return null
  }
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
