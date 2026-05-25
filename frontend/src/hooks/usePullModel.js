// usePullModel — kicks off an Ollama model pull and tracks completion.
//
// Wraps POST /pull-llm-model + polling of /model-status. Consumers render
// their own button UI around the returned state machine, so the three call
// sites (Settings, DetailView, GroupLoupe) each get to keep their own layout
// without dragging a shared button component everywhere.
//
// Why poll /model-status instead of /lm-status:
//   /model-status is the same surface that already reports SigLIP/TOPIQ/LAION
//   downloads, so an Ollama pull shows up in the existing global download
//   toast for free. /lm-status is for "is the daemon ready", which only
//   flips at the very end and tells us nothing while bytes are in flight.
//
// State lifecycle:
//   idle → pulling → done       (model is now installed; caller refetches /lm-status)
//                  → error      (HTTP error, daemon not running, or poll timeout)
//
// Times out after 30 minutes — a stalled pull shouldn't hang the UI forever.

import { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../api'

const POLL_INTERVAL_MS = 2000
const POLL_TIMEOUT_MS  = 30 * 60 * 1000  // 30 minutes — generous for slow links

export function usePullModel() {
  const [state, setState]     = useState('idle')   // idle | pulling | done | error
  const [detail, setDetail]   = useState(null)
  // Live progress for the active pull. {currentMb, totalMb, etaSeconds}.
  // null when no pull is in flight or when Ollama hasn't emitted size yet.
  const [progress, setProgress] = useState(null)
  const pollRef               = useRef(null)
  const startedAtRef          = useRef(0)
  // Sliding-window byte counter for ETA: store {t, mb} samples from the
  // last few polls and compute MB/s over the window. Stable enough to
  // avoid jitter without lagging an actual speed change.
  const speedSamplesRef       = useRef([])

  // Clear any in-flight polling on unmount so we don't fire setState on a
  // disposed component.
  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current)
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const pull = useCallback(async (name = 'qwen2.5vl:7b') => {
    stopPolling()
    setState('pulling')
    setDetail(null)
    startedAtRef.current = Date.now()

    try {
      const res = await fetch(`${API}/pull-llm-model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) {
        setState('error')
        setDetail(`HTTP ${res.status}`)
        return
      }
      // Backend now fast-fails the pre-flight cases (not_installed /
      // not_running) instead of kicking off a thread that silently dies.
      // Surface those as immediate errors so the button doesn't sit in the
      // pulling state for 30 minutes only to time out.
      const body = await res.json().catch(() => null)
      if (body && body.status && body.status !== 'started') {
        setState('error')
        setDetail(body.detail || `Ollama: ${body.status}`)
        return
      }
    } catch (err) {
      setState('error')
      setDetail('Could not reach backend')
      return
    }

    // Poll /model-status until our Ollama:<name> entry disappears.
    // The backend names the model_status entry "Ollama: <name>", so we
    // match by prefix to stay robust to model-name variants.
    speedSamplesRef.current = []
    setProgress(null)
    const tick = async () => {
      try {
        const r = await fetch(`${API}/model-status`)
        const data = await r.json()
        const ollamaEntry = (data.models || []).find(
          m => typeof m.name === 'string' && m.name.startsWith('Ollama:')
        )
        if (!ollamaEntry) {
          setState('done')
          setProgress(null)
          return
        }
        // Update live progress + compute ETA from a 6-sample sliding window.
        // currentMb/totalMb may be missing on the first few ticks (Ollama
        // hasn't started the digest yet) — keep progress null in that case
        // so the UI shows generic "Downloading…" instead of "0% · ∞".
        const currentMb = ollamaEntry.current_mb
        const totalMb   = ollamaEntry.total_mb
        if (typeof currentMb === 'number' && typeof totalMb === 'number' && totalMb > 0) {
          const now = Date.now()
          const samples = speedSamplesRef.current
          samples.push({ t: now, mb: currentMb })
          while (samples.length > 6) samples.shift()
          let etaSeconds = null
          if (samples.length >= 2) {
            const first = samples[0]
            const last  = samples[samples.length - 1]
            const elapsedS = (last.t - first.t) / 1000
            const deltaMb  = last.mb - first.mb
            if (elapsedS > 0 && deltaMb > 0) {
              const mbPerS = deltaMb / elapsedS
              const remainingMb = Math.max(0, totalMb - currentMb)
              etaSeconds = Math.round(remainingMb / mbPerS)
            }
          }
          setProgress({ currentMb, totalMb, etaSeconds })
        }
      } catch {
        // Transient network blip — keep polling.
      }
      if (Date.now() - startedAtRef.current > POLL_TIMEOUT_MS) {
        setState('error')
        setDetail('Pull timed out')
        return
      }
      pollRef.current = setTimeout(tick, POLL_INTERVAL_MS)
    }
    // Small initial delay so the backend has registered the model_status
    // entry before our first poll — otherwise the FIRST tick can see an
    // empty list and incorrectly flip to "done".
    pollRef.current = setTimeout(tick, 500)
  }, [stopPolling])

  // Reset state — used by callers when they want to retry after an error
  // without remounting the component.
  const reset = useCallback(() => {
    stopPolling()
    setState('idle')
    setDetail(null)
    setProgress(null)
    speedSamplesRef.current = []
  }, [stopPolling])

  return { state, detail, progress, pull, reset }
}
