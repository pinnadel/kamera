// useDashboard — fetches all six /dashboard/* endpoints in parallel.
//
// The dashboard is a static read of durable state (training_samples,
// shooting_log, personal_model.pkl), so we don't need fast polling like
// the analysis grid does. Refresh on mount + on user-triggered refetch.
// 30s background poll keeps it warm if the user leaves the view open
// while a batch analyze is running in the background.
//
// `since` (optional ISO date `YYYY-MM-DD`) windows decisions & shooting
// stats. `corpus` ('folder' | 'photos') selects which taste corpus the
// three model/decision endpoints answer for; the shooting endpoints ignore
// it (shooting_log has no source_type — see the router's docstring).
//
// Failure is per-endpoint, not all-or-nothing. This used to be Promise.all,
// which meant one failing call (say /shooting/distributions) threw before
// setData ran and blanked the ENTIRE dashboard — including cards whose data
// had already arrived. allSettled keeps every card that loaded and reports
// the rest individually, so a broken corner costs you a corner.

import { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../api'

// Endpoints that take ?corpus= . The shooting ones deliberately don't.
const ENDPOINTS = {
  modelCard:        { path: '/dashboard/model-card',                    corpus: true  },
  decisionTimeline: { path: '/dashboard/decisions/timeline?bucket=week', corpus: true  },
  featureDeltas:    { path: '/dashboard/decisions/feature-deltas',       corpus: true  },
  cameras:          { path: '/dashboard/shooting/cameras',               corpus: false },
  distributions:    { path: '/dashboard/shooting/distributions',         corpus: false },
  shootingTimeline: { path: '/dashboard/shooting/timeline?bucket=week',  corpus: false },
}

function buildUrl({ path, corpus: takesCorpus }, since, corpus) {
  const params = []
  if (since) params.push(`since=${encodeURIComponent(since)}`)
  if (takesCorpus && corpus) params.push(`corpus=${encodeURIComponent(corpus)}`)
  if (params.length === 0) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}${params.join('&')}`
}

export function useDashboard(since = null, corpus = 'folder') {
  const [data, setData] = useState({})
  // Per-key failures: { modelCard: "…: 500" }. A card renders its own error
  // rather than the page swallowing everything behind one banner.
  const [errors, setErrors] = useState({})
  const [loading, setLoading] = useState(true)
  // `refreshing` is true for every fetch AFTER the first paint — the manual
  // Refresh press and the 30s poll both set it. It drives the spin, which is
  // the only signal the user gets that the button did anything when the
  // numbers happen to be unchanged.
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)
  const cancelledRef = useRef(false)
  const loadedOnceRef = useRef(false)

  const refetch = useCallback(async () => {
    if (loadedOnceRef.current) setRefreshing(true)
    const keys = Object.keys(ENDPOINTS)
    const settled = await Promise.allSettled(
      keys.map(async (key) => {
        const res = await fetch(`${API}${buildUrl(ENDPOINTS[key], since, corpus)}`)
        if (!res.ok) throw new Error(`${res.status}`)
        return res.json()
      }),
    )
    if (cancelledRef.current) return

    const nextData = {}
    const nextErrors = {}
    settled.forEach((result, i) => {
      const key = keys[i]
      if (result.status === 'fulfilled') nextData[key] = result.value
      else nextErrors[key] = result.reason?.message || String(result.reason)
    })

    // Merge rather than replace: a card that failed this round keeps the data
    // it last had, so a flaky endpoint doesn't make content disappear and
    // reappear under the user. Its error surfaces next to the stale value.
    setData(prev => ({ ...prev, ...nextData }))
    setErrors(nextErrors)
    setLastUpdated(Date.now())
    setLoading(false)
    setRefreshing(false)
    loadedOnceRef.current = true
  }, [since, corpus])

  useEffect(() => {
    cancelledRef.current = false
    refetch()
    const interval = setInterval(refetch, 30_000)
    return () => {
      cancelledRef.current = true
      clearInterval(interval)
    }
  }, [refetch])

  return { ...data, errors, loading, refreshing, lastUpdated, refetch }
}
