import { useCallback, useEffect, useState } from 'react'
import { ALL_METRICS, LEAN_DEFAULTS } from '../sortMetrics'

const SORT_KEY    = 'pca.sort'
const VISIBLE_KEY = 'pca.sortMetricsVisible'

const VALID_FIELDS = new Set(['shot_at', 'filename', ...ALL_METRICS.map(m => m.id)])
const VALID_METRIC_IDS = new Set(ALL_METRICS.map(m => m.id))

function readSort() {
  try {
    const raw = localStorage.getItem(SORT_KEY)
    if (!raw) return { field: 'shot_at', dir: 'desc' }
    const parsed = JSON.parse(raw)
    const field = VALID_FIELDS.has(parsed?.field) ? parsed.field : 'shot_at'
    const dir   = parsed?.dir === 'asc' ? 'asc' : 'desc'
    return { field, dir }
  } catch {
    return { field: 'shot_at', dir: 'desc' }
  }
}

function readVisible() {
  try {
    const raw = localStorage.getItem(VISIBLE_KEY)
    if (!raw) return LEAN_DEFAULTS
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return LEAN_DEFAULTS
    const filtered = parsed.filter(id => VALID_METRIC_IDS.has(id))
    return filtered.length > 0 ? filtered : LEAN_DEFAULTS
  } catch {
    return LEAN_DEFAULTS
  }
}

// Single global sort hook. App.jsx holds the canonical instance and passes
// field/dir down to GroupLoupe; the Settings modal mounts its own to read
// visibleMetrics. Each instance writes through to localStorage; the storage
// event listener keeps multiple instances in sync within the same window.
export function useSort() {
  const [sort, setSortState] = useState(readSort)
  const [visibleMetrics, setVisibleMetricsState] = useState(readVisible)

  // Persist + cross-component sync. The storage event only fires across tabs,
  // so we also listen for a custom in-window event.
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === SORT_KEY)    setSortState(readSort())
      if (e.key === VISIBLE_KEY) setVisibleMetricsState(readVisible())
    }
    const onLocal = () => {
      setSortState(readSort())
      setVisibleMetricsState(readVisible())
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener('pca:sort-changed', onLocal)
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener('pca:sort-changed', onLocal)
    }
  }, [])

  const writeSort = useCallback((next) => {
    localStorage.setItem(SORT_KEY, JSON.stringify(next))
    setSortState(next)
    window.dispatchEvent(new Event('pca:sort-changed'))
  }, [])

  const setSortField = useCallback((field) => {
    writeSort({ field, dir: sort.dir })
  }, [writeSort, sort.dir])

  const toggleSortDir = useCallback(() => {
    writeSort({ field: sort.field, dir: sort.dir === 'desc' ? 'asc' : 'desc' })
  }, [writeSort, sort.field, sort.dir])

  const setVisibleMetrics = useCallback((next) => {
    const filtered = next.filter(id => VALID_METRIC_IDS.has(id))
    localStorage.setItem(VISIBLE_KEY, JSON.stringify(filtered))
    setVisibleMetricsState(filtered)
    window.dispatchEvent(new Event('pca:sort-changed'))
  }, [])

  return {
    sortField: sort.field,
    sortDir:   sort.dir,
    setSortField,
    toggleSortDir,
    visibleMetrics,
    setVisibleMetrics,
  }
}
