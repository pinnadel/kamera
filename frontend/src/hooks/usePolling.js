// Polling side-effects for App.jsx.
//
// Two intervals run here:
//   1. /analyze-progress (400 ms) — routes in-flight batch progress + live
//      image refreshes to the correct tab. Reads tabs through a ref to avoid
//      stale closure issues under StrictMode + initial-load races (see memory:
//      "Polling reads tabs via ref").
//   2. /model-status (1 000 ms) — drives the DownloadToast component.
//
// The hook returns nothing; all updates happen via setTabs / setModelStatus.

import { useEffect, useRef } from 'react'
import { API } from '../api'

// How often (in analyzed-photo count) to refresh similarity groups + kick
// the prerank queue while a batch is still running. The backend filters
// already-cached and too-small groups itself, so re-posting the full list
// is cheap; pick a cadence that gives the user visible "AI ranking is
// happening" feedback without spamming the backend. 50 photos at the
// observed throughput (≈ 1.5s/NEF, ≈ 16s/RAF) is roughly one refresh per
// 1–13 min — frequent enough to feel live, sparse enough to be a no-op
// network-wise.
const MID_BATCH_PRERANK_EVERY = 50

export function usePolling({ tabs, setTabs, setModelStatus, onBatchComplete, onPrerankAdvance }) {
  // Keep a ref that always points at the current tabs array so the polling
  // closure never reads a stale snapshot. The effect that writes it is
  // intentionally NOT in this hook — App.jsx owns tabs state and syncs it.
  const tabsRef = useRef(tabs)
  useEffect(() => { tabsRef.current = tabs }, [tabs])

  // Ref-based callback: lets the long-lived polling closure call the latest
  // onBatchComplete without re-binding the interval each time the callback
  // identity changes. This is how we invoke loadGroupsAndPrerank() — both
  // mid-batch (every MID_BATCH_PRERANK_EVERY photos, so AI ranking warms
  // up alongside analysis) AND at the running:true→false transition.
  // loadGroupsAndPrerank is a useCallback in useGroups that gets a new
  // identity whenever its threshold deps change, but the polling effect
  // owns a single setInterval for the app's lifetime.
  const onBatchCompleteRef = useRef(onBatchComplete)
  useEffect(() => { onBatchCompleteRef.current = onBatchComplete }, [onBatchComplete])

  // Same ref pattern for the prerank-advance callback. Called when the
  // backend reports that the worker either finished an item (completed +
  // skipped + failed bumped) or switched to a new in-flight hash. Used by
  // the grid to re-fetch /similarity-groups so per-tile markers flip from
  // pending → ready as the worker drains its queue.
  const onPrerankAdvanceRef = useRef(onPrerankAdvance)
  useEffect(() => { onPrerankAdvanceRef.current = onPrerankAdvance }, [onPrerankAdvance])

  // /analyze-progress — 400 ms
  useEffect(() => {
    let lastDone = -1
    // Track whether we saw running:true in a previous tick so we can detect
    // the running:true → running:false transition and move the tab to 'ready'.
    let wasRunning = false
    // Remember the tab id we're tracking so the completion handler can find
    // it even after source_folder is cleared.
    let trackingTabId = null
    // Highest "photos analyzed" milestone we've already used to trigger a
    // mid-batch prerank refresh, so we fire at most once per MID_BATCH_PRERANK_EVERY.
    // Reset when wasRunning flips back to false (new batch starts clean).
    let lastPrerankBucket = 0

    const id = setInterval(() => {
      fetch(`${API}/analyze-progress`)
        .then(r => r.json())
        .then(data => {
          if (!data.running) {
            // Batch just finished (or was never running).
            if (wasRunning && trackingTabId !== null) {
              // Transition the tab from 'analyzing' to 'ready'. Build a minimal
              // analyzeResult from the final progress snapshot so the result
              // banner has totals. The skipped list was stashed on the tab as
              // _pendingSkipped when /analyze-folder returned "started".
              const capturedTabId = trackingTabId
              setTabs(prev => prev.map(t => {
                if (t.id !== capturedTabId) return t
                const finalResult = {
                  status: 'done',
                  analyzed: data.analyzed_count ?? data.done ?? 0,
                  errors: [],
                  skipped: t._pendingSkipped || [],
                  total_found: data.total ?? 0,
                  // Wall-clock duration captured by the backend's _run_batch
                  // finally block. Float seconds; null only on legacy backends
                  // that haven't been restarted to pick up the new field.
                  elapsed_seconds: data.elapsed_seconds ?? null,
                }
                return { ...t, status: 'ready', analyzeResult: finalResult, progress: null, loaded: false, _pendingSkipped: undefined }
              }))
              // Trigger one final image load so the grid is fully populated.
              const tab = tabsRef.current.find(t => t.id === capturedTabId)
              if (tab?.folderPath) {
                fetch(`${API}/images?source_folder=${encodeURIComponent(tab.folderPath)}`)
                  .then(r => r.ok ? r.json() : Promise.reject())
                  .then(images => {
                    setTabs(prev => prev.map(t =>
                      t.id === capturedTabId ? { ...t, images, loaded: true } : t
                    ))
                  })
                  .catch(() => {})
              }
              // Refresh similarity groups too. /images polled while the batch
              // ran but /similarity-groups did not — it's only fetched on mount
              // and when the user changes thresholds. Without this call the
              // groups view stays empty (or stale) until the user hits Refresh
              // manually, which makes it look like clustering never happened.
              if (typeof onBatchCompleteRef.current === 'function') {
                try { onBatchCompleteRef.current() } catch { /* host-side error, don't break polling */ }
              }
            }
            wasRunning = false
            trackingTabId = null
            if (lastDone !== -1) lastDone = -1
            lastPrerankBucket = 0
            return
          }

          wasRunning = true
          const targetFolder = data.source_folder
          if (!targetFolder) return

          const currentTabs = tabsRef.current
          // Prefer the tab explicitly in 'analyzing' status (Analyze button
          // path), then fall back to any tab whose folderPath matches.
          let target = currentTabs.find(t => t.status === 'analyzing')
          if (!target) target = currentTabs.find(t => t.folderPath === targetFolder)
          if (!target) return

          const targetId = target.id
          trackingTabId = targetId
          setTabs(prev => prev.map(t =>
            t.id === targetId ? { ...t, progress: data } : t
          ))
          if (data.done !== lastDone) {
            lastDone = data.done
            fetch(`${API}/images?source_folder=${encodeURIComponent(targetFolder)}`)
              .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
              .then(images => {
                setTabs(prev => prev.map(t =>
                  t.id === targetId ? { ...t, images, loaded: true } : t
                ))
              })
              .catch(() => {})

            // Mid-batch prerank: every MID_BATCH_PRERANK_EVERY photos, refresh
            // /similarity-groups AND POST /prerank-groups so the AI burst
            // ranker starts warming the cache as bursts become detectable —
            // not only after the whole batch finishes. Uses the same callback
            // as onBatchComplete; enqueue_groups is idempotent on cached rows
            // so re-posting growing supersets is safe.
            const bucket = Math.floor(data.done / MID_BATCH_PRERANK_EVERY)
            if (bucket > lastPrerankBucket && data.done > 0) {
              lastPrerankBucket = bucket
              if (typeof onBatchCompleteRef.current === 'function') {
                try { onBatchCompleteRef.current() } catch { /* non-fatal */ }
              }
            }
          }
        })
        .catch(() => {})
    }, 400)
    return () => clearInterval(id)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  // Intentionally empty deps: this interval must live for the entire app
  // lifetime. Tab state is read through tabsRef, never from a closure capture.

  // /model-status — 1 000 ms while a model is actively loading,
  // 10 000 ms when everything is settled. Models are loaded once at startup
  // and stay resident until process exit, so the loading window is short
  // (~10-15s during cold start of model warm-up). Polling at 1s for the
  // app's entire lifetime previously sent ~3,600 requests/hour to a
  // backend endpoint that returns "nothing is loading" 99% of the time.
  // The backoff is implemented with a self-rescheduling setTimeout chain
  // because setInterval can't change cadence without tearing down.
  //
  // /prerank-status piggybacks on this tick: same fetch cadence, single
  // network round-trip pair, no new intervals. When the worker advances
  // (completed/skipped/failed counter or current_job_hash changed), we
  // call onPrerankAdvance so the grid can re-fetch /similarity-groups
  // and flip pending→ready markers.
  useEffect(() => {
    let cancelled = false
    let timeoutId = null
    // Track previous prerank snapshot so we only notify on real change.
    // Stored as a plain object — not React state — because nothing else
    // re-renders on these values.
    let prevPrerank = null

    function scheduleNext(delay) {
      if (cancelled) return
      timeoutId = setTimeout(tick, delay)
    }

    function tick() {
      // Pull both endpoints in parallel — no need to await one to start
      // the other. The cadence decision uses model-status; prerank just
      // fires its callback on change.
      Promise.all([
        fetch(`${API}/model-status`).then(r => r.json()).catch(() => null),
        fetch(`${API}/prerank-status`).then(r => r.json()).catch(() => null),
      ])
        .then(([model, prerank]) => {
          if (cancelled) return
          if (model) setModelStatus(model)
          // Loading = at least one model is downloading or initializing.
          // The backend's /model-status returns `models: []` when idle.
          const hasActive = Array.isArray(model?.models) && model.models.length > 0
          // Detect prerank progress: any of the three terminal counters
          // ticked up, OR the in-flight hash changed (worker moved to
          // a new group). Both signals mean per-group states may have
          // flipped — fire the callback to refresh the grid.
          if (prerank) {
            const advanced = prevPrerank && (
              prerank.completed !== prevPrerank.completed ||
              prerank.skipped !== prevPrerank.skipped ||
              prerank.failed !== prevPrerank.failed ||
              prerank.current_job_hash !== prevPrerank.current_job_hash
            )
            if (advanced && typeof onPrerankAdvanceRef.current === 'function') {
              try { onPrerankAdvanceRef.current() } catch { /* host-side error, don't break polling */ }
            }
            prevPrerank = prerank
          }
          scheduleNext(hasActive ? 1000 : 10000)
        })
        .catch(() => {
          // Network blip or backend restart — back off but keep trying.
          if (!cancelled) scheduleNext(5000)
        })
    }

    // Kick off the first request immediately so initial state is fresh.
    tick()
    return () => {
      cancelled = true
      if (timeoutId) clearTimeout(timeoutId)
    }
  }, [setModelStatus])

  // Expose the ref so App.jsx can pass it to this hook (App.jsx creates the
  // single source of truth for tabs; ref is written here on every render).
  return { tabsRef }
}
