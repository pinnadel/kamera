// AutoCullView — preview + apply auto-cull. Same backend endpoints as
// desktop. Shows preset chips (Conservative / Balanced / Aggressive),
// rule breakdown, and a sticky-footer Apply.

import { useEffect, useMemo, useState } from 'react'
import { Sparkles, Wand2, AlertTriangle, ChevronRight } from 'lucide-react'
import { API } from '../../api'
import { TopBar } from '../components/TopBar'
import { MobileInfo } from '../components/MobileInfo'

const PRESETS = [
  { id: 'conservative', label: 'Conservative', subtitle: 'Reject only the obvious failures',
    rationale: 'Only photos that clearly fail (closed eyes, technically broken).' },
  { id: 'balanced',     label: 'Balanced',     subtitle: 'Recommended starting point',
    rationale: 'Reject the bad, mark borderline as Maybe.' },
  { id: 'aggressive',   label: 'Aggressive',   subtitle: 'Strict — keep only the strongest',
    rationale: 'Higher quality bar; pushes more borderline photos to Reject/Maybe.' },
]

export function AutoCullView({ activeFolder, addToast, back, reloadImages }) {
  const [preset, setPreset]       = useState('balanced')
  const [preview, setPreview]     = useState(null)
  const [loading, setLoading]     = useState(false)
  const [applying, setApplying]   = useState(false)
  const [error, setError]         = useState(null)

  useEffect(() => {
    if (!activeFolder) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetch(`${API}/auto-cull/preview?source_folder=${encodeURIComponent(activeFolder)}&preset=${preset}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`Status ${r.status}`)))
      .then(data => { if (!cancelled) setPreview(data) })
      .catch(err => { if (!cancelled) setError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [activeFolder, preset])

  const apply = async () => {
    if (!activeFolder || !preview) return
    setApplying(true)
    try {
      const res = await fetch(`${API}/auto-cull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_folder: activeFolder, preset }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `Status ${res.status}`)
      addToast({
        type: 'success',
        message: `Auto-cull applied: ${body.kept || 0} keep · ${body.maybe || 0} maybe · ${body.rejected || 0} reject`,
        duration: 5000,
      })
      await reloadImages?.()
      back()
    } catch (err) {
      addToast({ type: 'error', message: err.message, duration: 6000 })
    } finally {
      setApplying(false)
    }
  }

  return (
    <>
      <TopBar
        title="Auto-cull"
        subtitle="AI suggestion"
        onBack={back}
      />

      <main className="flex-1 overflow-y-auto pb-32">
        <div className="px-4 pt-4">
          <div className="flex items-start gap-3">
            <span
              className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-[rgba(91,184,212,0.10)] flex-shrink-0"
              aria-hidden="true"
            >
              <Sparkles size={22} className="text-[#5BB8D4]" />
            </span>
            <div className="flex-1">
              <h2 className="m-h2 mb-1">Let the model triage</h2>
              <p className="m-body text-[#9c9c9d] leading-snug">
                Auto-cull combines technical scores, AI quality signals, and your personal taste model into a recommended decision per photo. Preview below — nothing moves until you Apply.
              </p>
            </div>
          </div>
        </div>

        <section className="px-4 mt-5">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d]">Strictness</h3>
            <MobileInfo
              icon
              label="About auto-cull strictness"
              content={
                <div className="space-y-2 text-[13px]">
                  <p><strong>Conservative</strong> rejects only the photos that clearly fail (closed eyes in portraits, severe motion blur).</p>
                  <p><strong>Balanced</strong> rejects bad photos and marks borderline ones as Maybe — recommended starting point.</p>
                  <p><strong>Aggressive</strong> applies a stricter quality bar; pushes more borderline photos toward Reject.</p>
                  <p>You can fine-tune the rule thresholds in Settings → Decision thresholds.</p>
                </div>
              }
            />
          </div>
          <div className="grid grid-cols-1 gap-2">
            {PRESETS.map(p => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPreset(p.id)}
                aria-pressed={preset === p.id}
                className={`flex items-center gap-3 p-3 rounded-2xl border text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] ${
                  preset === p.id
                    ? 'border-[rgba(91,184,212,0.55)] bg-[rgba(91,184,212,0.08)]'
                    : 'border-white/5 bg-[#101111]'
                }`}
              >
                <span
                  className={`w-5 h-5 rounded-full flex items-center justify-center border ${
                    preset === p.id ? 'border-[#5BB8D4] bg-[#5BB8D4]' : 'border-white/20'
                  }`}
                  aria-hidden="true"
                >
                  {preset === p.id && <span className="w-2 h-2 rounded-full bg-[#07080a]" />}
                </span>
                <div className="flex-1">
                  <p className="text-[15px] font-semibold text-[#f9f9f9]">{p.label}</p>
                  <p className="text-[13px] text-[#9c9c9d]">{p.subtitle}</p>
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="px-4 mt-5">
          <h3 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] mb-2">Outcome</h3>
          {loading ? (
            <div className="grid grid-cols-3 gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="rounded-xl bg-[#101111] h-20 shimmer" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-start gap-2 text-[13px] text-[#E8A0A0] p-3 rounded-2xl bg-[rgba(201,123,123,0.06)] border border-[rgba(201,123,123,0.20)]">
              <AlertTriangle size={16} className="mt-0.5" aria-hidden="true" />
              <span>{error}</span>
            </div>
          ) : preview ? (
            <>
              <div className="grid grid-cols-3 gap-2">
                <OutcomeChip label="Keep"   count={preview.counts?.keep   ?? 0} tint="keep"   />
                <OutcomeChip label="Maybe"  count={preview.counts?.maybe  ?? 0} tint="maybe"  />
                <OutcomeChip label="Reject" count={preview.counts?.reject ?? 0} tint="reject" />
              </div>

              {Array.isArray(preview.rule_breakdown) && preview.rule_breakdown.length > 0 && (
                <div className="mt-3 rounded-2xl bg-[#101111] border border-white/5">
                  <h4 className="px-3 py-2 text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] border-b border-white/5">
                    Rules that fired
                  </h4>
                  <ul>
                    {preview.rule_breakdown.map((r, i) => (
                      <li key={i} className="flex items-center gap-3 px-3 py-2 border-b border-white/5 last:border-0">
                        <ChevronRight size={14} className="text-[#5BB8D4]" aria-hidden="true" />
                        <span className="flex-1 text-[14px] text-[#cecece]">{r.label || r.rule}</span>
                        <span className="text-[13px] font-mono text-[#9c9c9d] m-tabular">{r.count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : null}
        </section>
      </main>

      <div
        className="fixed left-0 right-0 z-30 px-4 pt-3 pb-4 m-blur-surface border-t border-white/5"
        style={{ bottom: 0, paddingBottom: 'calc(16px + var(--safe-bottom))' }}
      >
        <button
          type="button"
          onClick={apply}
          disabled={!preview || applying}
          className="m-btn m-btn-primary w-full disabled:opacity-50"
        >
          <Wand2 size={18} aria-hidden="true" />
          {applying ? 'Applying…' : 'Apply auto-cull'}
        </button>
      </div>
    </>
  )
}

function OutcomeChip({ label, count, tint }) {
  const tints = {
    keep:    { bg: 'rgba(125,184,154,0.10)', border: 'rgba(125,184,154,0.30)', text: '#9DD0B5' },
    maybe:   { bg: 'rgba(232,184,74,0.10)',  border: 'rgba(232,184,74,0.30)',  text: '#F0CD7A' },
    reject:  { bg: 'rgba(201,123,123,0.10)', border: 'rgba(201,123,123,0.30)', text: '#E8A0A0' },
  }
  const t = tints[tint]
  return (
    <div
      className="rounded-2xl py-3 px-3 border text-center"
      style={{ backgroundColor: t.bg, borderColor: t.border }}
    >
      <p className="text-[26px] font-mono font-semibold m-tabular leading-none" style={{ color: t.text }}>
        {count}
      </p>
      <p className="text-[11px] uppercase tracking-wide font-medium mt-1" style={{ color: t.text }}>
        {label}
      </p>
    </div>
  )
}
