// SettingsView — mobile-native settings. Grouped scrollable sections with
// a sticky-footer Apply when changes are pending. All knobs reach the same
// backend (/settings, /folder-settings, etc.) as desktop's SettingsModal,
// so a setting changed on mobile reflects on desktop and vice versa.

import { useEffect, useMemo, useState } from 'react'
import {
  Save, Wrench, Eye, FileImage, Trash2, RefreshCw, AlertTriangle, ChevronRight,
} from 'lucide-react'
import { API } from '../../api'
import { TopBar } from '../components/TopBar'
import { MobileInfo } from '../components/MobileInfo'

// Setting categories. Render order is intentionally tuned for mobile use:
// Display first (visual signal user wants now), then Decision thresholds
// (touched only occasionally), then Files & previews, then Maintenance.

export function SettingsView({ back, addToast, reloadImages }) {
  const [settings, setSettings] = useState(null)
  const [pending, setPending]   = useState({})
  const [saving, setSaving]     = useState(false)

  useEffect(() => {
    fetch(`${API}/settings`).then(r => r.ok ? r.json() : null).then(setSettings).catch(() => {})
  }, [])

  const dirty = Object.keys(pending).length > 0
  const merged = useMemo(() => ({ ...(settings || {}), ...pending }), [settings, pending])

  const set = (key, value) => setPending(p => ({ ...p, [key]: value }))

  const save = async () => {
    if (!dirty) return
    setSaving(true)
    try {
      const res = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pending),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Status ${res.status}`)
      }
      setSettings(s => ({ ...(s || {}), ...pending }))
      setPending({})
      addToast({ type: 'success', message: 'Settings saved' })
      await reloadImages?.().catch(() => {})
    } catch (err) {
      addToast({ type: 'error', message: err.message, duration: 6000 })
    } finally {
      setSaving(false)
    }
  }

  const clearAnalysis = async () => {
    if (!window.confirm('Clear ALL analysis and decisions for every folder? Files on disk are untouched.')) return
    try {
      const res = await fetch(`${API}/clear`, { method: 'POST' })
      if (!res.ok) throw new Error(`Status ${res.status}`)
      addToast({ type: 'success', message: 'Analysis cleared' })
      await reloadImages?.().catch(() => {})
      back()
    } catch (err) {
      addToast({ type: 'error', message: err.message, duration: 6000 })
    }
  }

  return (
    <>
      <TopBar title="Settings" subtitle="Mobile preferences" onBack={back} />

      <main className="flex-1 overflow-y-auto pb-32">
        {!settings ? (
          <div className="flex items-center justify-center h-64 text-[#9c9c9d]">Loading settings…</div>
        ) : (
          <>
            <Section title="Appearance" icon={Eye}>
              <ScaleRow value={localStorage.getItem('pca.uiScale') || 'M'} />
              <ToggleRow
                label="Reduce motion"
                tooltip="Disables sheet snap springs, the AI border animation, and the post-decision flash. Honors the OS-level prefers-reduced-motion as well."
                checked={!!merged.reduce_motion}
                onChange={v => set('reduce_motion', v)}
              />
            </Section>

            <Section title="Sharpness vs. exposure weight" icon={Wrench}>
              <SliderRow
                label="Sharpness weight"
                tooltip="The Overall score is sharpness × w + exposure × (1 − w). Default 0.65 puts more emphasis on sharpness because it's non-recoverable in post."
                value={merged.sharpness_weight ?? 0.65}
                min={0.30} max={0.90} step={0.05}
                onChange={v => set('sharpness_weight', v)}
                format={v => v.toFixed(2)}
              />
            </Section>

            <Section title="Auto-cull thresholds" icon={Wrench}>
              <SliderRow
                label="Personal · Keep above"
                tooltip="If your model is ready, photos with a personal score above this number are auto-Kept by Auto-cull."
                value={merged.personal_keep_threshold ?? 70}
                min={50} max={95} step={1}
                onChange={v => set('personal_keep_threshold', v)}
                format={v => Math.round(v)}
              />
              <SliderRow
                label="Personal · Maybe above"
                tooltip="Photos between Maybe and Keep thresholds become Maybe. Below this becomes Reject."
                value={merged.personal_maybe_threshold ?? 50}
                min={30} max={70} step={1}
                onChange={v => set('personal_maybe_threshold', v)}
                format={v => Math.round(v)}
              />
              <ToggleRow
                label="Reject closed eyes (single subject)"
                tooltip="Auto-rejects portraits where the lone face has its eyes closed. Group photos use the next setting."
                checked={!!merged.reject_closed_eyes}
                onChange={v => set('reject_closed_eyes', v)}
              />
              <ToggleRow
                label="Reject only when ALL faces have eyes closed"
                tooltip="In group photos, only auto-reject if every detected face has eyes closed. Photos with at least one open-eyed subject survive."
                checked={!!merged.reject_closed_eyes_all_faces}
                onChange={v => set('reject_closed_eyes_all_faces', v)}
              />
            </Section>

            <Section title="Files & previews" icon={FileImage}>
              <ToggleRow
                label="Send rejected photos to system Trash"
                tooltip="When enabled, R presses move photos to the OS Trash via send2trash instead of the local _Trash/ subfolder. Restorable from Finder/Explorer."
                checked={!!merged.reject_to_system_trash}
                onChange={v => set('reject_to_system_trash', v)}
              />
              <ToggleRow
                label="Prefer camera JPEG/HIF preview over RAW demosaic"
                tooltip="Shows the camera's baked-in preview when available. Faster, sometimes more flattering. Doesn't affect any score — analysis still uses the RAW pixels."
                checked={!!merged.prefer_sidecar_preview}
                onChange={v => set('prefer_sidecar_preview', v)}
              />
            </Section>

            <Section title="Maintenance" icon={Trash2}>
              <ActionRow
                label="Reload AI models"
                description="Restart the lazy-loaded TOPIQ-NR / TOPIQ-IAA / SigLIP scorers. Useful after clearing the cache."
                onClick={async () => {
                  try {
                    await fetch(`${API}/reload-models`, { method: 'POST' })
                    addToast({ type: 'success', message: 'Models reloaded' })
                  } catch {
                    addToast({ type: 'error', message: 'Reload failed' })
                  }
                }}
                Icon={RefreshCw}
              />
              <ActionRow
                label="Clear all analysis and decisions"
                description="Wipes the SQLite database and preview cache. Files on disk are untouched. Doesn't delete the AI model weights."
                onClick={clearAnalysis}
                Icon={Trash2}
                danger
              />
            </Section>
          </>
        )}
      </main>

      {dirty && (
        <div
          className="fixed left-0 right-0 z-30 px-4 pt-3 pb-4 m-blur-surface border-t border-white/5"
          style={{ bottom: 0, paddingBottom: 'calc(16px + var(--safe-bottom))' }}
          role="region"
          aria-label="Unsaved changes"
        >
          <div className="flex items-center gap-3">
            <p className="flex-1 text-[14px] text-[#cecece]">{Object.keys(pending).length} change{Object.keys(pending).length === 1 ? '' : 's'} pending</p>
            <button
              type="button"
              onClick={() => setPending({})}
              className="m-btn m-btn-ghost"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="m-btn m-btn-primary disabled:opacity-50"
            >
              <Save size={16} aria-hidden="true" />
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}

function Section({ title, icon: Icon, children }) {
  return (
    <section className="mt-5 px-4">
      <div className="flex items-center gap-2 mb-2">
        {Icon && <Icon size={14} className="text-[#9c9c9d]" aria-hidden="true" />}
        <h2 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d]">{title}</h2>
      </div>
      <div className="rounded-2xl bg-[#101111] border border-white/5 overflow-hidden">
        {children}
      </div>
    </section>
  )
}

function Row({ children, last }) {
  return (
    <div className={`flex items-center gap-3 px-4 py-3 ${last ? '' : 'border-b border-white/5'}`}>
      {children}
    </div>
  )
}

function ToggleRow({ label, tooltip, checked, onChange }) {
  return (
    <Row>
      <div className="flex-1">
        <div className="flex items-center gap-2">
          {tooltip ? (
            <MobileInfo content={<p className="text-[13px] leading-snug">{tooltip}</p>}>
              <span className="text-[15px] text-[#f9f9f9] font-medium">{label}</span>
            </MobileInfo>
          ) : (
            <span className="text-[15px] text-[#f9f9f9] font-medium">{label}</span>
          )}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative w-12 h-7 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] ${
          checked ? 'bg-[#5BB8D4]' : 'bg-[#3a3a3a]'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-6 h-6 rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-5' : ''
          }`}
        />
      </button>
    </Row>
  )
}

function SliderRow({ label, tooltip, value, min, max, step, onChange, format }) {
  return (
    <Row>
      <div className="flex-1">
        <div className="flex items-center gap-2 mb-1">
          {tooltip ? (
            <MobileInfo content={<p className="text-[13px] leading-snug">{tooltip}</p>}>
              <span className="text-[15px] text-[#f9f9f9] font-medium">{label}</span>
            </MobileInfo>
          ) : (
            <span className="text-[15px] text-[#f9f9f9] font-medium">{label}</span>
          )}
          <span className="ml-auto text-[14px] font-mono font-semibold text-[#5BB8D4] m-tabular">
            {format(value)}
          </span>
        </div>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          aria-label={label}
          className="w-full accent-[#5BB8D4]"
        />
      </div>
    </Row>
  )
}

function ActionRow({ label, description, onClick, Icon, danger }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-start gap-3 w-full text-left px-4 py-3 border-b border-white/5 last:border-0 hover:bg-white/5 focus-visible:outline-none focus-visible:bg-white/5 ${danger ? 'text-[#E8A0A0]' : 'text-[#f9f9f9]'}`}
    >
      <span className={`inline-flex items-center justify-center w-9 h-9 rounded-full ${danger ? 'bg-[rgba(201,123,123,0.10)]' : 'bg-[rgba(91,184,212,0.10)]'} flex-shrink-0`}>
        {Icon ? <Icon size={16} aria-hidden="true" /> : null}
      </span>
      <div className="flex-1">
        <p className="text-[15px] font-medium">{label}</p>
        <p className="text-[13px] text-[#9c9c9d] mt-0.5 leading-snug">{description}</p>
      </div>
      <ChevronRight size={16} className="text-[#9c9c9d] mt-3" aria-hidden="true" />
    </button>
  )
}

function ScaleRow({ value: initial }) {
  const [value, setValue] = useState(initial)
  const apply = (v) => {
    setValue(v)
    document.documentElement.dataset.uiScale = v
    try { localStorage.setItem('pca.uiScale', v) } catch {}
  }
  return (
    <Row>
      <div className="flex-1">
        <div className="flex items-center gap-2 mb-2">
          <MobileInfo content={<p className="text-[13px] leading-snug">Zooms every UI value (text, icons, controls). The same setting is honored on desktop. Recommended <strong>M</strong> for phones, <strong>L</strong> for low-vision users.</p>}>
            <span className="text-[15px] text-[#f9f9f9] font-medium">UI scale</span>
          </MobileInfo>
        </div>
        <div role="radiogroup" aria-label="UI scale" className="grid grid-cols-3 gap-2">
          {['S', 'M', 'L'].map(s => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={value === s}
              onClick={() => apply(s)}
              className={`h-12 rounded-xl border text-[15px] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] ${
                value === s
                  ? 'border-[rgba(91,184,212,0.55)] bg-[rgba(91,184,212,0.08)] text-[#f9f9f9]'
                  : 'border-white/5 bg-[#1b1c1e] text-[#cecece]'
              }`}
            >
              {s === 'S' ? 'Small' : s === 'M' ? 'Medium' : 'Large'}
            </button>
          ))}
        </div>
      </div>
    </Row>
  )
}
