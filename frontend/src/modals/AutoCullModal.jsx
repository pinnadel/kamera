import { useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, Sparkles, WandSparkles, X } from 'lucide-react'
import { API } from '../api'
import { BTN_ICON, BTN_PRIMARY, BTN_PRIMARY_DANGER, BTN_SECONDARY } from '../ui/buttons'
import { DecisionWord } from '../ui/primitives'
import { useDialog } from '../hooks/useDialog'

// RuleBreakdown — collapsible "Why rejected?" section shown below the K/M/X bars.
// Only rendered when there are rejects and rule_breakdown is present in the preview.
function RuleBreakdown({ breakdown, total }) {
  const [open, setOpen] = useState(false)

  const rules = [
    { key: 'closed_eyes',     label: 'Closed eyes' },
    { key: 'soft_face',       label: 'Face not sharp' },
    { key: 'blurry_frame',    label: 'Blurry frame' },
    { key: 'burst_duplicate', label: 'Redundant burst frame' },
    { key: 'low_score',       label: 'Low overall score' },
  ].filter(r => breakdown[r.key] > 0)

  // Highlight the dominant rule if it accounts for ≥60% of rejects
  const dominant = rules.find(r => breakdown[r.key] / total >= 0.6)

  return (
    <div className="border border-[rgba(255,255,255,0.07)] rounded-lg mb-4 overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:opacity-80 transition-opacity"
      >
        <span className="text-xs text-[#9c9c9d]">Why rejected?</span>
        <span className="text-[#8a8b8c] flex items-center">
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-1.5 border-t border-[rgba(255,255,255,0.07)]">
          {dominant && (
            <p className="text-xs text-[#E8B84A] mt-2 mb-1">
              {breakdown[dominant.key]} of {total} rejects ({Math.round(breakdown[dominant.key] / total * 100)}%) from: {dominant.label.toLowerCase()}
            </p>
          )}
          {rules.map(r => (
            <div key={r.key} className="flex items-center justify-between">
              <span className="text-xs text-[#9c9c9d]">{r.label}</span>
              <div className="flex items-center gap-2">
                <div className="w-20 bg-[#1b1c1e] rounded-[3px] h-1">
                  <div
                    className="bg-[#C97B7B] h-1 rounded-[3px]"
                    style={{ width: `${(breakdown[r.key] / total) * 100}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-[#8a8a8a] w-5 text-right">{breakdown[r.key]}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// Consequence — the one line that answers "what could I regret?".
//
// This used to read "Files move immediately", which was true only with the
// default settings. `reject_to_system_trash` routes EVERY reject through
// send2trash, auto-cull's included (sources/folder.py), so with that toggle on
// one click sent N files to the OS Trash while the modal still promised a move.
// The preview now resolves the real destination server-side and we state it,
// together with the thing the user actually wants to know before committing:
// whether U can take it back.
function Consequence({ dest, rejects }) {
  // Older backend (launcher not restarted since the field shipped) — say the
  // safe, always-true part rather than inventing a destination.
  if (!dest) {
    return (
      <p className="text-xs text-[#9c9c9d] mb-5">
        Files are committed immediately. Only undecided photos are affected.
      </p>
    )
  }

  const trashing = dest.mode === 'trash'

  if (!trashing) {
    return (
      <p className="text-xs text-[#9c9c9d] mb-5 leading-relaxed">
        Files move immediately — {rejects}{' '}
        <DecisionWord kind="reject">{rejects === 1 ? 'Reject' : 'Rejects'}</DecisionWord>
        {' into '}<span className="font-mono text-[#cecece]">{dest.label}</span>, the rest
        alongside it. Nothing is deleted, and every photo can be taken back with{' '}
        <kbd className="px-1 py-0.5 rounded bg-[#1b1c1e] border border-[rgba(255,255,255,0.12)] text-[10px] font-mono text-[#cecece]">U</kbd>
      </p>
    )
  }

  return (
    <div className="mb-5 px-3 py-2.5 rounded-lg border border-[rgba(201,123,123,0.35)] bg-[rgba(201,123,123,0.07)]">
      <div className="flex items-start gap-2">
        <AlertTriangle size={14} className="text-[#C97B7B] mt-0.5 shrink-0" />
        <div>
          <p className="text-xs text-[#f0f0f0] font-medium mb-1">
            {rejects} {rejects === 1 ? 'file goes' : 'files go'} to {dest.label}
          </p>
          <p className="text-xs text-[#cecece] leading-relaxed">
            <DecisionWord kind="reject">Reject</DecisionWord> is set to the system Trash in
            Settings → Workflow, and auto-cull uses the same route as a manual R. You can restore them
            from the Trash, but{' '}
            <kbd className="px-1 py-0.5 rounded bg-[#1b1c1e] border border-[rgba(255,255,255,0.12)] text-[10px] font-mono text-[#cecece]">U</kbd>
            {' '}cannot — once a file leaves the folder, KaMeRa can no longer address it.
          </p>
        </div>
      </div>
    </div>
  )
}

// Human-readable duration for the AI-rank estimate. Minutes only — the
// estimate is ~60s per group, so second-level precision would be false.
function formatEta(seconds) {
  const mins = Math.max(1, Math.round(seconds / 60))
  if (mins < 60) return `${mins} min`
  const hours = Math.floor(mins / 60)
  const rest = mins % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}

// AiRankNotice — what the AI-rank phase will cost before the user commits.
// Auto-cull asks the vision model which frame of each contested group is the
// best one; groups it has already ranked (opened in the loupe, or drained by
// the background worker) are free, the rest are ~60s each. That is the whole
// reason this run is no longer instant, so it gets stated up front rather than
// discovered as a stalled progress bar.
function AiRankNotice({ ai }) {
  if (!ai?.enabled || ai.groups === 0) return null
  const pending = ai.to_rank ?? 0
  return (
    <div className="flex items-start gap-2 px-3 py-2 mb-2 border border-[rgba(255,255,255,0.07)] rounded-lg">
      <Sparkles size={13} className="text-[#5BB8D4] mt-[1px] shrink-0" />
      <p className="text-[11px] leading-relaxed text-[#9c9c9d]">
        {pending > 0 ? (
          <>
            <span className="text-[#cecece] font-mono tabular-nums">{pending}</span>
            {pending === 1 ? ' group needs' : ' groups need'} AI ranking to pick their best
            photo — about <span className="text-[#cecece]">{formatEta(ai.estimated_seconds)}</span>.
            {ai.cached > 0 && (
              <>
                {' '}
                <span className="text-[#cecece] font-mono tabular-nums">{ai.cached}</span>
                {' already ranked.'}
              </>
            )}
            {' '}You can stop while it runs — nothing moves until ranking finishes.
          </>
        ) : (
          <>
            All <span className="text-[#cecece] font-mono tabular-nums">{ai.groups}</span>
            {' contested '}{ai.groups === 1 ? 'group is' : 'groups are'} already AI-ranked — the
            best photo of each is the vision model's pick, not a score guess.
          </>
        )}
      </p>
    </div>
  )
}

// AutoCullModal — preview and execute automatic K/M/X assignment
// Scoped to `folderPath` when provided, so tab-triggered auto-cull only
// previews and acts on photos from the active tab's folder.
export function AutoCullModal({ folderPath, groupThreshold, onClose, onComplete, onToast }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [previewError, setPreviewError] = useState(null)
  // Live AI-ranking progress. The POST stays a single blocking request (the
  // decisions have to be applied in one transaction-ish pass), so progress
  // comes from a side channel the backend publishes into.
  const [rankProgress, setRankProgress] = useState(null)
  const [stopping, setStopping] = useState(false)

  // Build the shared query string. Auto-cull's burst-dedup post-pass groups by
  // VISUAL SIMILARITY only — it deliberately does NOT use the grouping time-gap
  // (that slider is inert on fast bursts: continuous frames share an EXIF
  // second, so no gap can split them; see _group_undecided_into_bursts). Only
  // the Similarity threshold is passed. Falls back to the backend default when
  // absent.
  const params = new URLSearchParams()
  if (folderPath) params.set('source_folder', folderPath)
  if (groupThreshold != null) params.set('threshold', String(groupThreshold))
  const qs = params.toString()
  const query = qs ? `?${qs}` : ''

  useEffect(() => {
    fetch(`${API}/auto-cull/preview${query}`)
      .then(async r => {
        const data = await r.json().catch(() => ({}))
        // The refusal carries the reason — a Photos tab whose model has not
        // reached the ready tier reports how many decisions are still missing.
        if (!r.ok) throw new Error(data.detail || `${r.status}`)
        return data
      })
      .then(setPreview)
      .catch(err => setPreviewError(err.message || 'Could not load preview'))
      .finally(() => setLoading(false))
  }, [query])

  // Poll the ranking phase while the cull request is in flight. Stops as soon
  // as `running` goes false, so an idle modal makes no requests.
  useEffect(() => {
    if (!running) { setRankProgress(null); return }
    let alive = true
    const tick = () => {
      fetch(`${API}/auto-cull/progress`)
        .then(r => r.ok ? r.json() : null)
        .then(p => { if (alive && p) setRankProgress(p) })
        .catch(() => {})
    }
    tick()
    const id = setInterval(tick, 1500)
    return () => { alive = false; clearInterval(id) }
  }, [running])

  async function stopAutoCull() {
    setStopping(true)
    try {
      await fetch(`${API}/auto-cull/stop`, { method: 'POST' })
    } catch (e) {
      console.error('Stop auto-cull failed:', e)
    }
  }

  async function runAutoCull() {
    setRunning(true)
    setStopping(false)
    try {
      const res = await fetch(`${API}/auto-cull${query}`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `${res.status}`)
      }
      const data = await res.json()
      onComplete(data)
    } catch (e) {
      console.error('Auto-cull failed:', e)
      onToast?.({ type: 'error', message: `Auto-cull failed: ${e.message}` })
    } finally {
      setRunning(false)
      setStopping(false)
    }
  }

  // Destination is resolved server-side (settings + this folder's overrides),
  // so the modal never has to guess which route a reject will take.
  const trashing = preview?.reject_destination?.mode === 'trash'

  const bars = [
    { key: 'keep',   label: 'Keeps',   fill: 'bg-[#7DB89A]',  text: 'text-[#7DB89A]' },
    { key: 'maybe',  label: 'Maybes',  fill: 'bg-[#E8B84A]',  text: 'text-[#E8B84A]' },
    { key: 'reject', label: 'Rejects', fill: 'bg-[#C97B7B]',  text: 'text-[#C97B7B]' },
  ]

  const dialogRef = useDialog(onClose)
  return (
    <div className="fixed inset-0 z-50 bg-[rgba(7,8,10,0.80)] flex items-center justify-center" onClick={onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="Auto-cull" tabIndex={-1} className="focus:outline-none bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl p-6 w-96 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)]" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-[#f9f9f9] font-semibold text-base">Auto-cull</h2>
          <button onClick={onClose} className={BTN_ICON} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {loading && (
          <div className="text-[#9c9c9d] text-sm text-center py-8">Calculating preview…</div>
        )}

        {!loading && previewError && (
          <div className="text-sm text-[#C97B7B] text-center py-6 mb-2">
            Couldn't load preview — {previewError}.
            <p className="text-xs text-[#8a8b8c] mt-1">Check the backend is running, then close and retry.</p>
          </div>
        )}

        {!loading && preview && (
          <>
            {preview.scoring_mode === 'personal' ? (
              <div className="mb-5">
                <span className="inline-block px-2 py-0.5 rounded text-xs bg-[rgba(123,130,201,0.20)] text-[#7B82C9]">
                  Personal model · {preview.scoring_info}
                </span>
                <p className="text-xs text-[#8a8b8c] mt-1.5">
                  Decisions reflect what you previously kept and rejected.
                </p>
              </div>
            ) : (
              // Condensed tradeoff strip — surfaced when auto-cull cannot use
              // the personal model yet (untrained, learning, or under review).
              // Tells the user *why* generic thresholds are being used and
              // hints at the next step without burying it in Settings.
              <div className="mb-5 px-3 py-2.5 rounded-lg border border-[rgba(232,184,74,0.30)] bg-[rgba(232,184,74,0.06)]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="inline-block px-2 py-0.5 rounded text-xs bg-[rgba(232,184,74,0.18)] text-[#E8B84A] font-medium">
                    Quality thresholds
                  </span>
                  <span className="text-xs text-[#9c9c9d]">
                    not your personal taste
                  </span>
                </div>
                <p className="text-xs text-[#cecece] leading-relaxed">
                  {preview.scoring_info && preview.scoring_info !== 'quality thresholds'
                    ? preview.scoring_info.charAt(0).toUpperCase() + preview.scoring_info.slice(1) + '.'
                    : 'Generic sharpness + exposure rules — they don\'t know what you actually like.'}
                  {' '}Expect more false rejects on unconventional shots (intentional blur, low-key, off-center subjects).
                </p>
              </div>
            )}

            <div className="space-y-3 mb-5">
              {bars.map(({ key, label, fill, text }) => {
                const count = preview.counts[key] ?? 0
                const pct   = preview.total > 0 ? (count / preview.total) * 100 : 0
                return (
                  <div key={key} className="flex items-center gap-3">
                    <span className={`text-xs w-14 ${text}`}>{label}</span>
                    <div className="flex-1 bg-[#1b1c1e] rounded-[3px] h-1.5">
                      <div className={`${fill} h-1.5 rounded-[3px] transition-all`} style={{ width: `${pct}%` }} />
                    </div>
                    <span className="text-xs font-mono text-[#8a8a8a] w-8 text-right">{count}</span>
                  </div>
                )
              })}
            </div>

            {/* Dedup-vs-model split: makes the grouping slider's effect legible.
                Burst-dedup rejects (redundant frames of a burst) are the only
                ones the time-gap / similarity slider can move; everything else
                is the model's own per-photo quality call, which grouping never
                touches. Shown whenever there are any rejects so the two sources
                are always visible side by side. */}
            {preview.counts.reject > 0 && (() => {
              const dedup = preview.rule_breakdown?.burst_duplicate ?? 0
              const model = Math.max(0, preview.counts.reject - dedup)
              return (
                <div className="px-3 py-2 mb-2 border border-[rgba(255,255,255,0.07)] rounded-lg text-[11px] leading-relaxed text-[#9c9c9d]">
                  <span className="text-[#cecece] font-mono tabular-nums">{dedup}</span>
                  {' rejected as redundant burst frames '}
                  <span className="text-[#8a8b8c]">(Similarity slider + burst budgets)</span>
                  {' · '}
                  <span className="text-[#cecece] font-mono tabular-nums">{model}</span>
                  {' from per-photo quality '}
                  <span className="text-[#8a8b8c]">(the model's own call)</span>
                </div>
              )
            })()}

            <AiRankNotice ai={preview.ai_rank} />

            {(preview.rule_breakdown?.uncertain ?? 0) > 0 && (
              <div className="flex items-center justify-between px-3 py-2 mb-2 border border-[rgba(255,255,255,0.07)] rounded-lg">
                <span className="text-xs text-[#9c9c9d]">Uncertain — routed to Maybe</span>
                <span className="text-xs font-mono text-[#E8B84A] tabular-nums">
                  {preview.rule_breakdown.uncertain}
                </span>
              </div>
            )}

            {preview.counts.reject > 0 && preview.rule_breakdown && (
              <RuleBreakdown breakdown={preview.rule_breakdown} total={preview.counts.reject} />
            )}

            <Consequence
              dest={preview.reject_destination}
              rejects={preview.counts.reject ?? 0}
            />
          </>
        )}

        {!loading && preview && preview.total === 0 && (
          <p className="text-sm text-[#8a8a8a] text-center py-4 mb-4">No undecided photos to cull.</p>
        )}

        {running && rankProgress?.phase === 'ranking' && rankProgress.total > 0 && (
          <div className="px-3 py-2.5 mb-4 border border-[rgba(91,184,212,0.30)] bg-[rgba(91,184,212,0.06)] rounded-lg">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-[#cecece]">
                {stopping ? 'Stopping after this group…' : 'AI ranking groups'}
              </span>
              <span className="text-xs font-mono text-[#5BB8D4] tabular-nums">
                {rankProgress.done}/{rankProgress.total}
              </span>
            </div>
            <div className="bg-[#1b1c1e] rounded-[3px] h-1.5">
              <div
                className="bg-[#5BB8D4] h-1.5 rounded-[3px] transition-all"
                style={{ width: `${(rankProgress.done / rankProgress.total) * 100}%` }}
              />
            </div>
            <p className="text-[11px] text-[#8a8b8c] mt-1.5">
              No photo has moved yet — stopping now leaves everything as it is
            </p>
          </div>
        )}

        <div className="flex justify-end gap-3">
          {running ? (
            <button onClick={stopAutoCull} disabled={stopping} className={BTN_SECONDARY}>
              <X size={16} /> {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button onClick={onClose} className={BTN_SECONDARY}>
              <X size={16} /> Cancel
            </button>
          )}
          <button
            onClick={runAutoCull}
            disabled={running || loading || !preview || preview.total === 0}
            className={trashing ? BTN_PRIMARY_DANGER : BTN_PRIMARY}
          >
            {trashing ? <AlertTriangle size={16} /> : <WandSparkles size={16} />}
            {running
              ? 'Running…'
              : trashing
                // With the Trash route on, the number that matters is the one
                // that leaves the shoot — not the one that merely gets sorted.
                ? `Cull ${preview?.total ?? '…'} · trash ${preview?.counts?.reject ?? 0}`
                : `Cull ${preview?.total ?? '…'} photos`}
          </button>
        </div>
      </div>
    </div>
  )
}
