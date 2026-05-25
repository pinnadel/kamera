// useDashboard — fetches all six /dashboard/* endpoints in parallel.
//
// The dashboard is a static read of durable state (training_samples,
// shooting_log, personal_model.pkl), so we don't need fast polling like
// the analysis grid does. Refresh on mount + on user-triggered refetch.
// 30s background poll keeps it warm if the user leaves the view open
// while a batch analyze is running in the background.
//
// `since` (optional ISO date `YYYY-MM-DD`) windows decisions & shooting
// stats. model-card accepts the param but ignores it — the personal
// model is a global artifact, not windowed. Kept in the same loop so
// every dashboard fetch goes through one shape.

import { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../api'

const ENDPOINTS = {
  modelCard:        '/dashboard/model-card',
  decisionTimeline: '/dashboard/decisions/timeline?bucket=week',
  featureDeltas:    '/dashboard/decisions/feature-deltas',
  cameras:          '/dashboard/shooting/cameras',
  distributions:    '/dashboard/shooting/distributions',
  shootingTimeline: '/dashboard/shooting/timeline?bucket=week',
}

function withSince(path, since) {
  if (!since) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}since=${encodeURIComponent(since)}`
}

export function useDashboard(since = null) {
  const [data, setData] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const cancelledRef = useRef(false)

  const refetch = useCallback(async () => {
    try {
      setError(null)
      const entries = await Promise.all(
        Object.entries(ENDPOINTS).map(async ([key, path]) => {
          const res = await fetch(`${API}${withSince(path, since)}`)
          if (!res.ok) throw new Error(`${path}: ${res.status}`)
          const json = await res.json()
          return [key, json]
        }),
      )
      if (cancelledRef.current) return
      setData(Object.fromEntries(entries))
    } catch (e) {
      if (!cancelledRef.current) setError(e.message || String(e))
    } finally {
      if (!cancelledRef.current) setLoading(false)
    }
  }, [since])

  useEffect(() => {
    cancelledRef.current = false
    refetch()
    const interval = setInterval(refetch, 30_000)
    return () => {
      cancelledRef.current = true
      clearInterval(interval)
    }
  }, [refetch])

  return { ...data, loading, error, refetch }
}
