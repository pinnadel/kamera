import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, WandSparkles, X } from 'lucide-react'
import { API } from '../api'
import { BTN_ICON, BTN_PRIMARY, BTN_SECONDARY } from '../ui/buttons'

// RuleBreakdown — collapsible "Why rejected?" section shown below the K/M/X bars.
// Only rendered when there are rejects and rule_breakdown is present in the preview.
function RuleBreakdown({ breakdown, total }) {
  const [open, setOpen] = useState(false)

  const rules = [
    { key: 'closed_eyes',  label: 'Closed eyes' },
    { key: 'soft_face',    label: 'Face not sharp' },
    { key: 'blurry_frame', label: 'Blurry frame' },
    { key: 'low_score',    label: 'Low overall score' },
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
        <span className="text-[#6a6b6c] flex items-center">
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

// AutoCullModal — preview and execute automatic K/M/X assignment
// Scoped to `folderPath` when provided, so tab-triggered auto-cull only
// previews and acts on photos from the active tab's folder.
export function AutoCullModal({ folderPath, onClose, onComplete, onToast }) {
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [previewError, setPreviewError] = useState(null)

  const folderQuery = folderPath ? `?source_folder=${encodeURIComponent(folderPath)}` : ''

  useEffect(() => {
    fetch(`${API}/auto-cull/preview${folderQuery}`)
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then(setPreview)
      .catch(err => setPreviewError(err.message || 'Could not load preview'))
      .finally(() => setLoading(false))
  }, [folderQuery])

  async function runAutoCull() {
    setRunning(true)
    try {
      const res = await fetch(`${API}/auto-cull${folderQuery}`, { method: 'POST' })
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
    }
  }

  const bars = [
    { key: 'keep',   label: 'Keeps',   fill: 'bg-[#7DB89A]',  text: 'text-[#7DB89A]' },
    { key: 'maybe',  label: 'Maybes',  fill: 'bg-[#E8B84A]',  text: 'text-[#E8B84A]' },
    { key: 'reject', label: 'Rejects', fill: 'bg-[#C97B7B]',  text: 'text-[#C97B7B]' },
  ]

  return (
    <div className="fixed inset-0 z-50 bg-[rgba(7,8,10,0.80)] flex items-center justify-center" onClick={onClose}>
      <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl p-6 w-96 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)]" onClick={e => e.stopPropagation()}>
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
            <p className="text-xs text-[#6a6b6c] mt-1">Check the backend is running, then close and retry.</p>
          </div>
        )}

        {!loading && preview && (
          <>
            {preview.scoring_mode === 'personal' ? (
              <div className="mb-5">
                <span className="inline-block px-2 py-0.5 rounded text-xs bg-[rgba(123,130,201,0.20)] text-[#7B82C9]">
                  Personal model · {preview.scoring_info}
                </span>
                <p className="text-[11px] text-[#6a6b6c] mt-1.5">
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
                  <span className="text-[11px] text-[#9c9c9d]">
                    not your personal taste
                  </span>
                </div>
                <p className="text-[11px] text-[#cecece] leading-relaxed">
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

            <p className="text-xs text-[#9c9c9d] mb-5">
              Files move immediately. Only undecided photos are affected.
            </p>
          </>
        )}

        {!loading && preview && preview.total === 0 && (
          <p className="text-sm text-[#8a8a8a] text-center py-4 mb-4">No undecided photos to cull.</p>
        )}

        <div className="flex justify-end gap-3">
          <button onClick={onClose} className={BTN_SECONDARY}>
            <X size={16} /> Cancel
          </button>
          <button
            onClick={runAutoCull}
            disabled={running || loading || !preview || preview.total === 0}
            className={BTN_PRIMARY}
          >
            <WandSparkles size={16} />
            {running ? 'Running…' : `Cull ${preview?.total ?? '…'} photos`}
          </button>
        </div>
      </div>
    </div>
  )
}
