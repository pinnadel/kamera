import { useEffect, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Filter,
  GraduationCap,
  Monitor,
  RefreshCw,
  RotateCcw,
  Scale,
  Shield,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { API } from '../api'
import { BTN_ICON, BTN_PRIMARY, BTN_SECONDARY } from '../ui/buttons'
import { Toggle, InfoTooltip, HoverPopover, DecisionWord } from '../ui/primitives'
import { ConfirmModal } from './ConfirmModal'
import { PersonalModelBanner } from '../views/PersonalModelBanner'
import { ALL_METRICS, SCORE_GROUPS } from '../sortMetrics'
import { useSort } from '../hooks/useSort'
import { PullVisionModelButton } from '../ui/PullVisionModelButton'
import { InstallOllamaCTA } from '../ui/InstallOllamaCTA'

// Presets — apply a named bundle of decision settings in one click.
// "balanced" mirrors DECISION_DEFAULTS exactly. "review_heavy" widens the
// Maybe band so fewer borderline keeps get silently rejected — best when
// the user actively reviews the Maybe pile (manual + Auto-cull experiment
// 2026-05-23 showed maybe=45 silently loses ~12% of keeps, maybe=30 cuts
// that to ~5% in exchange for a larger Maybe pile to triage).
const PRESETS = {
  conservative: {
    sharpness_weight:         0.70,
    keep_threshold:           75,
    maybe_threshold:          55,
    fallback_keep:            65,
    fallback_maybe:           45,
    fallback_sharpness_floor: 35,
    face_sharpness_floor:     15,
    reject_soft_face:         true,
    reject_blurry_frame:      true,
    reject_closed_eyes:       true,
  },
  balanced: {
    sharpness_weight:         0.65,
    keep_threshold:           70,
    maybe_threshold:          45,
    fallback_keep:            60,
    fallback_maybe:           40,
    fallback_sharpness_floor: 40,
    face_sharpness_floor:     20,
    reject_soft_face:         true,
    reject_blurry_frame:      true,
    reject_closed_eyes:       true,
  },
  review_heavy: {
    sharpness_weight:         0.65,
    keep_threshold:           70,
    maybe_threshold:          30,
    fallback_keep:            60,
    fallback_maybe:           30,
    fallback_sharpness_floor: 40,
    face_sharpness_floor:     20,
    reject_soft_face:         true,
    reject_blurry_frame:      true,
    reject_closed_eyes:       true,
  },
  aggressive: {
    sharpness_weight:         0.60,
    keep_threshold:           60,
    maybe_threshold:          35,
    fallback_keep:            50,
    fallback_maybe:           30,
    fallback_sharpness_floor: 50,
    face_sharpness_floor:     25,
    reject_soft_face:         true,
    reject_blurry_frame:      true,
    reject_closed_eyes:       true,
  },
}

// Defaults — must match backend constants in phase1_technical/quality_analyzer.py
const DECISION_DEFAULTS = {
  sharpness_weight:           0.65,
  keep_threshold:             70,
  maybe_threshold:            45,
  fallback_keep:              60,
  fallback_maybe:             40,
  fallback_sharpness_floor:   40,
  face_sharpness_floor:       20,
  reject_soft_face:           true,
  reject_blurry_frame:        true,
  reject_closed_eyes:         true,
  reject_closed_eyes_all_faces: false,
  reject_reciprocal_rule:     false,
  reject_above_iso_ceiling:   false,
  iso_ceiling:                0,
  // PR3 boundary routing.
  auto_cull_uncertain_to_maybe: true,
  uncertainty_threshold:        8.0,
}

export function SettingsModal({ settings, onSave, onClose, onToast, onClear, clearing, onResetModel, onResetDashboard, autoGenerate, onAutoGenerateChange, modelInfo, onTrain, training, onStartTraining, undecidedCount, bannerDismissed, onSetBannerDismissed, uiScale, onUiScaleChange, showFilenames, onToggleFilenames, advanceDir, onAdvanceDirChange,
  // Grouping sliders (Model tab). Live-updating — change triggers an
  // immediate /similarity-groups refetch via useGroups in the parent.
  // They aren't part of the Apply/draft flow because they aren't
  // server-persisted settings (localStorage only) and they recluster
  // the library on every change.
  groupThreshold, setGroupThreshold,
  groupTimeGapSeconds, setGroupTimeGapSeconds,
}) {
  // Two tabs left: Model + Display. Folders moved to a per-tab popover in the
  // bottom toolbar (2026-05-04) so destinations are scoped to one analysis.
  const [activeTab, setActiveTab] = useState('model')
  // Display tab — single bool, saved immediately on change (no draft/dirty state).
  const [preferSidecar, setPreferSidecar] = useState(settings?.prefer_sidecar_preview ?? false)
  const [savingDisplay, setSavingDisplay] = useState(false)
  useEffect(() => {
    setPreferSidecar(settings?.prefer_sidecar_preview ?? false)
  }, [settings?.prefer_sidecar_preview])
  const [rejectToTrash, setRejectToTrash] = useState(settings?.reject_to_system_trash ?? false)
  const [savingTrash, setSavingTrash] = useState(false)
  useEffect(() => {
    setRejectToTrash(settings?.reject_to_system_trash ?? false)
  }, [settings?.reject_to_system_trash])
  // Advanced sort-options visibility (localStorage). Lean defaults preselected;
  // user can opt into the rest. Section collapsed by default — these are
  // power-user toggles, not first-run choices.
  const { visibleMetrics, setVisibleMetrics } = useSort()
  const [sortOptionsOpen, setSortOptionsOpen] = useState(false)
  const toggleMetric = (id) => {
    const set = new Set(visibleMetrics)
    if (set.has(id)) set.delete(id); else set.add(id)
    setVisibleMetrics(Array.from(set))
  }
  // Decision settings draft — all numeric thresholds + boolean toggles.
  // Initialised from server settings, then locally edited; saved as a single
  // POST when the user clicks Apply.
  const initDecision = () => ({
    sharpness_weight:         settings?.sharpness_weight         ?? DECISION_DEFAULTS.sharpness_weight,
    keep_threshold:           settings?.keep_threshold           ?? DECISION_DEFAULTS.keep_threshold,
    maybe_threshold:          settings?.maybe_threshold          ?? DECISION_DEFAULTS.maybe_threshold,
    fallback_keep:            settings?.fallback_keep            ?? DECISION_DEFAULTS.fallback_keep,
    fallback_maybe:           settings?.fallback_maybe           ?? DECISION_DEFAULTS.fallback_maybe,
    fallback_sharpness_floor: settings?.fallback_sharpness_floor ?? DECISION_DEFAULTS.fallback_sharpness_floor,
    face_sharpness_floor:     settings?.face_sharpness_floor     ?? DECISION_DEFAULTS.face_sharpness_floor,
    reject_soft_face:           settings?.reject_soft_face           ?? DECISION_DEFAULTS.reject_soft_face,
    reject_blurry_frame:        settings?.reject_blurry_frame        ?? DECISION_DEFAULTS.reject_blurry_frame,
    reject_closed_eyes:         settings?.reject_closed_eyes         ?? DECISION_DEFAULTS.reject_closed_eyes,
    reject_closed_eyes_all_faces: settings?.reject_closed_eyes_all_faces ?? DECISION_DEFAULTS.reject_closed_eyes_all_faces,
    reject_reciprocal_rule:     settings?.reject_reciprocal_rule     ?? DECISION_DEFAULTS.reject_reciprocal_rule,
    reject_above_iso_ceiling:   settings?.reject_above_iso_ceiling   ?? DECISION_DEFAULTS.reject_above_iso_ceiling,
    iso_ceiling:                settings?.iso_ceiling                ?? DECISION_DEFAULTS.iso_ceiling,
    auto_cull_uncertain_to_maybe: settings?.auto_cull_uncertain_to_maybe ?? DECISION_DEFAULTS.auto_cull_uncertain_to_maybe,
    uncertainty_threshold:        settings?.uncertainty_threshold        ?? DECISION_DEFAULTS.uncertainty_threshold,
  })
  const [decision, setDecision]               = useState(initDecision)
  const [savedDecision, setSavedDecision]     = useState(initDecision)
  const [savingDecision, setSavingDecision]   = useState(false)
  const [showValidationHint, setShowValidationHint] = useState(false)
  const decisionDirty = JSON.stringify(decision) !== JSON.stringify(savedDecision)

  // Cross-field validation. Maybe cutoff must sit strictly below Keep cutoff
  // for both the personal-model and the fallback pair — otherwise the Maybe
  // band collapses. Errors are surfaced inline on the offending Maybe field
  // and block the Apply button.
  const decisionErrors = {}
  if (decision.maybe_threshold >= decision.keep_threshold) {
    decisionErrors.maybe_threshold = `Must be lower than Keep cutoff (${decision.keep_threshold})`
  }
  if (decision.fallback_maybe >= decision.fallback_keep) {
    decisionErrors.fallback_maybe = `Must be lower than Keep cutoff (${decision.fallback_keep})`
  }
  const decisionValid = Object.keys(decisionErrors).length === 0
  // Advanced accordion — fallback cutoffs hidden by default
  const [advancedOpen, setAdvancedOpen] = useState(
    () => localStorage.getItem('pca.settings.advancedOpen') === 'true'
  )
  const [reloading, setReloading] = useState(false)
  const [reloadConfirm, setReloadConfirm]   = useState(false)
  const [clearConfirm, setClearConfirm] = useState(false)
  const [clearChallenge, setClearChallenge] = useState(null)
  const [clearAnswer, setClearAnswer] = useState('')
  const [resetModelConfirm, setResetModelConfirm] = useState(false)
  const [resetModelRunning, setResetModelRunning] = useState(false)
  const [resetDashboardConfirm, setResetDashboardConfirm] = useState(false)
  const [resetDashboardRunning, setResetDashboardRunning] = useState(false)
  const [discardConfirm, setDiscardConfirm] = useState(false)

  function openClearConfirm() {
    const a = Math.floor(Math.random() * 9) + 1
    const b = Math.floor(Math.random() * 9) + 1
    setClearChallenge({ a, b, answer: String(a + b) })
    setClearAnswer('')
    setClearConfirm(true)
  }

  async function handleSaveDecision() {
    if (!decisionValid) {
      setShowValidationHint(true)
      return
    }
    setShowValidationHint(false)
    setSavingDecision(true)
    try {
      const res = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(decision),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      setSavedDecision(decision)
      await onSave()
      onToast({ type: 'success', message: 'Decision settings saved' })
    } catch (err) {
      console.error('Decision save failed:', err)
      onToast({ type: 'error', message: 'Could not save decision settings — server error' })
    } finally {
      setSavingDecision(false)
    }
  }

  function resetDecisionToDefaults() {
    setDecision({ ...DECISION_DEFAULTS })
  }

  // Display: save the single bool immediately. Optimistic UI — flip the
  // toggle right away, roll back if the request fails.
  async function handleSidecarToggle(next) {
    const prev = preferSidecar
    setPreferSidecar(next)
    setSavingDisplay(true)
    try {
      const res = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefer_sidecar_preview: next }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      await onSave()
      onToast({
        type: 'success',
        message: next
          ? 'Showing camera-baked previews when available'
          : 'Showing RAW previews',
      })
    } catch (err) {
      console.error('Display preference save failed:', err)
      setPreferSidecar(prev)
      onToast({ type: 'error', message: 'Could not save preference — server error' })
    } finally {
      setSavingDisplay(false)
    }
  }

  // Reject-to-Trash toggle. When ON, future R presses send the file to the
  // system Trash via send2trash instead of moving it to _Trash/. Switching
  // this mid-session doesn't relocate already-rejected photos — applies to
  // future decisions only. Optimistic write with rollback on failure.
  async function handleRejectTrashToggle(next) {
    const prev = rejectToTrash
    setRejectToTrash(next)
    setSavingTrash(true)
    try {
      const res = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reject_to_system_trash: next }),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      await onSave()
      onToast({
        type: 'success',
        message: next
          ? 'Rejected photos will be sent to system Trash'
          : 'Rejected photos will be moved to the _Trash/ folder',
      })
    } catch (err) {
      console.error('Reject-to-trash preference save failed:', err)
      setRejectToTrash(prev)
      onToast({ type: 'error', message: 'Could not save preference — server error' })
    } finally {
      setSavingTrash(false)
    }
  }

  // Helpers for individual rows
  const updField = (k, v) => {
    setDecision(d => ({ ...d, [k]: v }))
    setShowValidationHint(false)
  }

  async function handleReloadModels() {
    setReloading(true)
    try {
      const res = await fetch(`${API}/reload-models`, { method: 'POST' })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      onToast({ type: 'success', message: 'AI models reloading in background…' })
    } catch (err) {
      console.error('Model reload failed:', err)
      onToast({ type: 'error', message: 'Could not reload models — server error' })
    } finally {
      setReloading(false)
    }
  }

  const TAB_ACTIVE   = 'text-[#f9f9f9] border-b border-[#5BB8D4]'
  const TAB_INACTIVE = 'text-[#6a6b6c] hover:opacity-70 border-b border-transparent transition-opacity'

  // Guard against silently dropping unsaved decision changes when the user
  // clicks the backdrop or × — Display saves auto-commit so only the decision
  // draft needs protection.
  const handleClose = () => {
    if (decisionDirty) {
      setDiscardConfirm(true)
      return
    }
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 bg-[rgba(7,8,10,0.80)] flex items-start justify-center pt-[5vh]" onClick={handleClose} onWheel={e => e.stopPropagation()}>
      <div
        className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl w-[440px] max-w-full max-h-[90vh] flex flex-col overflow-hidden shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)]"
        onClick={e => e.stopPropagation()}
      >
        {/* Upper section — darker band, matches DetailView header */}
        <div className="bg-[#161718] border-b border-[#2f3031] px-6 pt-5 pb-0 rounded-t-xl flex-shrink-0">
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-[#f9f9f9]">Settings</h2>
            <button onClick={handleClose} className={BTN_ICON} aria-label="Close">
              <X size={18} />
            </button>
          </div>

          {/* Tab bar */}
          <div className="flex gap-5">
            <button
              onClick={() => setActiveTab('model')}
              className={`inline-flex items-center gap-1.5 pb-2 text-xs font-medium transition-colors ${activeTab === 'model' ? TAB_ACTIVE : TAB_INACTIVE}`}
            >
              <Brain size={15} /> Model
            </button>
            <button
              onClick={() => setActiveTab('display')}
              className={`inline-flex items-center gap-1.5 pb-2 text-xs font-medium transition-colors ${activeTab === 'display' ? TAB_ACTIVE : TAB_INACTIVE}`}
            >
              <Monitor size={15} /> Display
            </button>
          </div>
        </div>

        {/* Body — scrollable middle region */}
        <div className="px-6 pt-5 pb-6 overflow-y-auto flex-1 min-h-0">

        {/* ── Model tab ── */}
        {activeTab === 'model' && (
          <div className="space-y-6">

            {/* ── Presets ───────────────────────────────────────────────── */}
            {(() => {
              // Detect which preset (if any) matches the current draft state.
              const activePreset = Object.keys(PRESETS).find(key => {
                const p = PRESETS[key]
                return Object.keys(p).every(k => {
                  // Floats compared with a small epsilon to tolerate slider rounding
                  const a = decision[k]
                  const b = p[k]
                  if (typeof b === 'boolean') return a === b
                  return Math.abs(Number(a) - Number(b)) < 0.001
                })
              }) ?? null

              const pills = [
                { key: 'conservative', label: 'Conservative', Icon: Shield, desc: 'Fewer keeps, stricter rules.' },
                { key: 'balanced',     label: 'Balanced',     Icon: Scale,  desc: 'The defaults.' },
                { key: 'review_heavy', label: 'Review-heavy', Icon: Filter, desc: 'Wider Maybe band so fewer borderline keeps slip through; pairs with manual Maybe review.' },
                { key: 'aggressive',   label: 'Aggressive',   Icon: Zap,    desc: 'More keeps, looser rules.' },
              ]

              return (
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs text-[#9c9c9d]">Presets</span>
                    {activePreset === null && (
                      <span className="text-[10px] text-[#9c9c9d]">· Custom settings</span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {pills.map(({ key, label, Icon, desc }) => {
                      const isActive = activePreset === key
                      return (
                        <HoverPopover key={key} content={desc}>
                          <button
                            onClick={() => {
                              setDecision({ ...PRESETS[key] })
                              setShowValidationHint(false)
                            }}
                            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-md border text-xs transition-opacity ${
                              isActive
                                ? 'border-[#5BB8D4] text-[#5BB8D4] bg-[rgba(91,184,212,0.10)]'
                                : 'border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70'
                            }`}
                          >
                            <Icon size={14} />
                            {label}
                          </button>
                        </HoverPopover>
                      )
                    })}
                  </div>
                  {activePreset === null ? (
                    <p className="text-[11px] text-[#6a6b6c] mt-2 leading-relaxed">
                      Your thresholds below don't match any preset — that's fine, they're yours.
                      Presets are just starting points; pick one to reset everything to it.
                    </p>
                  ) : (
                    <p className="text-[11px] text-[#6a6b6c] mt-2 leading-relaxed">
                      A preset is selected. Adjusting any threshold below makes it custom.
                    </p>
                  )}
                </div>
              )
            })()}

            {/* ── Decision thresholds ───────────────────────────────────── */}
            <div>
              <p className="label mb-1">Decision thresholds</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-5">
                These rules control how scores translate into{' '}
                <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
                <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
                <DecisionWord kind="reject">Reject</DecisionWord>
                {' '}when you run Auto-cull. They also drive the score values shown in the grid.
              </p>

              {/* Scoring weights */}
              <div className="mb-5">
                <div className="flex items-center gap-1.5 mb-3">
                  <p className="text-[11px] uppercase tracking-widest text-[#9c9c9d]">Scoring weights</p>
                  <InfoTooltip>
                    Sharpness vs. exposure contribution to the overall quality score. Sharpness is non-recoverable in post; exposure can be fixed in RAW. Higher sharpness weight = stricter on blur. Saving re-scores every analyzed photo instantly.
                  </InfoTooltip>
                </div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-[#cecece] font-mono">Sharpness {Math.round(decision.sharpness_weight * 100)}%</span>
                  <span className="text-xs text-[#9c9c9d] font-mono">Exposure {100 - Math.round(decision.sharpness_weight * 100)}%</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round(decision.sharpness_weight * 100)}
                  onChange={e => updField('sharpness_weight', Number(e.target.value) / 100)}
                  className="w-full accent-[#5BB8D4]"
                />
              </div>

              {/* Personal-model cutoffs. When the model isn't Ready, these
                  controls are still editable (so the user can prepare a
                  preferred config), but a dormant-state hint tells them
                  why nothing apparent changes yet — and surfaces the live
                  model accuracy when it IS ready, so the user has any
                  basis for deciding whether tuning is worth it. */}
              <div className="pt-4 border-t border-[rgba(255,255,255,0.05)] mb-5">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-[11px] uppercase tracking-widest text-[#9c9c9d]">Personal-model cutoffs</p>
                  {modelInfo?.ready && modelInfo?.validation?.model_accuracy != null && (
                    <span
                      className="text-[10px] font-mono text-[#9c9c9d]"
                      title={`Your trained model agrees with your decisions on ${Math.round(modelInfo.validation.model_accuracy * 100)}% of held-out test cases. Baseline (technical scoring alone) is ${Math.round((modelInfo.validation.baseline_accuracy ?? 0) * 100)}%. The bigger the gap, the more your personal-model thresholds matter relative to fallback ones.`}
                    >
                      Model accuracy{' '}
                      <span className="text-[#7B82C9]">{Math.round(modelInfo.validation.model_accuracy * 100)}%</span>
                      {modelInfo.validation.baseline_accuracy != null && (
                        <span className="text-[#6a6b6c]"> vs baseline {Math.round(modelInfo.validation.baseline_accuracy * 100)}%</span>
                      )}
                    </span>
                  )}
                </div>

                {!modelInfo?.ready && (
                  <p className="text-[11px] text-[#E8B84A] bg-[rgba(232,184,74,0.08)] border border-[rgba(232,184,74,0.20)] rounded px-2 py-1.5 mb-3 leading-relaxed">
                    These cutoffs are dormant until the personal model trains. Auto-cull is currently using the fallback cutoffs below.
                    {modelInfo?.decided_count != null && modelInfo?.min_decisions != null && modelInfo.decided_count < modelInfo.min_decisions && (
                      <> Decide {modelInfo.min_decisions - modelInfo.decided_count} more photos to start training.</>
                    )}
                  </p>
                )}

                <NumberRow
                  label="Keep when score ≥"
                  tooltip={`Personal-model score at or above this number → auto-assigned Keep. Default ${DECISION_DEFAULTS.keep_threshold}. Raise to be pickier about what lands in auto-Keep; lower to silently include more photos as Keeps without review.`}
                  value={decision.keep_threshold}
                  min={50} max={95} step={1}
                  onChange={v => updField('keep_threshold', v)}
                />
                <NumberRow
                  label="Maybe when score ≥"
                  tooltip={`Below the Keep cutoff but at or above this number → Maybe (you'll review it). Anything below this number → silently Rejected. Default ${DECISION_DEFAULTS.maybe_threshold}. Lower this to catch more borderline photos in Maybe before they're silently rejected — the cost is a bigger Maybe pile to review.`}
                  value={decision.maybe_threshold}
                  min={20} max={70} step={1}
                  onChange={v => updField('maybe_threshold', v)}
                  error={decisionErrors.maybe_threshold}
                />
                <p className="text-xs text-[#9c9c9d] leading-relaxed mt-2">
                  Active once the personal model reaches the Ready tier (≥50 samples and validated to beat the quality-threshold baseline).
                </p>

                <ToggleRow
                  label="Route uncertain decisions to Maybe"
                  tooltip="When the personal model's prediction has high variance across an internal ensemble of 20 sub-trained models AND the score lands near a Keep or Maybe boundary, route the photo to Maybe instead of committing to a hard decision. Avoids flip-flopping at the cutoffs. Requires the model to be Ready."
                  enabled={decision.auto_cull_uncertain_to_maybe}
                  onChange={v => updField('auto_cull_uncertain_to_maybe', v)}
                />
                {decision.auto_cull_uncertain_to_maybe && (
                  <NumberRow
                    label="Uncertainty threshold"
                    tooltip={`Minimum ensemble std_dev (in personal_score points) that counts as 'uncertain'. Photos at or above this AND within ±std of a boundary route to Maybe. Default ${DECISION_DEFAULTS.uncertainty_threshold}. Higher = more confident the model has to be before routing kicks in.`}
                    value={decision.uncertainty_threshold}
                    min={0} max={50} step={1}
                    onChange={v => updField('uncertainty_threshold', v)}
                    indent
                  />
                )}
              </div>

              {/* Fallback cutoffs — collapsed behind Advanced accordion */}
              <div className="pt-4 border-t border-[rgba(255,255,255,0.05)] mb-5">
                <button
                  onClick={() => {
                    const next = !advancedOpen
                    setAdvancedOpen(next)
                    localStorage.setItem('pca.settings.advancedOpen', next)
                  }}
                  className="w-full flex items-center justify-between py-2 text-[11px] uppercase tracking-widest text-[#9c9c9d] hover:opacity-70 transition-opacity"
                >
                  <span className="inline-flex items-center gap-1.5">
                    {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    Advanced — fallback cutoffs
                  </span>
                </button>
                {advancedOpen && (
                  <div className="pl-2">
                    <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                      Used before the personal model is trained.
                    </p>
                    <NumberRow
                      label="Keep when overall ≥"
                      tooltip={`Used before the personal model is trained. Photos with overall_score at or above this number get auto-assigned Keep. Default ${DECISION_DEFAULTS.fallback_keep}.`}
                      value={decision.fallback_keep}
                      min={40} max={90} step={1}
                      onChange={v => updField('fallback_keep', v)}
                    />
                    <NumberRow
                      label="Maybe when overall ≥"
                      tooltip={`Below the keep cutoff but at or above this number (and frame sharpness ≥ 60) → Maybe. Default ${DECISION_DEFAULTS.fallback_maybe}.`}
                      value={decision.fallback_maybe}
                      min={20} max={60} step={1}
                      onChange={v => updField('fallback_maybe', v)}
                      error={decisionErrors.fallback_maybe}
                    />
                  </div>
                )}
              </div>

              {/* Instant rejects */}
              <div className="pt-4 border-t border-[rgba(255,255,255,0.05)] mb-5">
                <p className="text-[11px] uppercase tracking-widest text-[#9c9c9d] mb-3">Instant rejects (apply in both modes)</p>
                <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                  These rules run before the cutoffs above. When the condition is true, the photo is auto-rejected — overriding both personal-model and fallback scoring.
                </p>

                <ToggleRow
                  label="Reject when eyes are closed"
                  tooltip="When a face is detected and the blink-detector says the eyes are closed, the photo is auto-rejected. Detection runs at analysis time."
                  enabled={decision.reject_closed_eyes}
                  onChange={v => updField('reject_closed_eyes', v)}
                />
                {decision.reject_closed_eyes && (
                  <ToggleRow
                    label="Only when ALL faces are closed"
                    tooltip="When ON, group photos only get auto-rejected if every detected face has closed eyes. Photos analyzed before this feature shipped (no per-face data) fall back to never rejecting multi-face frames — re-analyse the folder to enable strict checking on those rows."
                    enabled={decision.reject_closed_eyes_all_faces}
                    onChange={v => updField('reject_closed_eyes_all_faces', v)}
                    indent
                  />
                )}
                <ToggleRow
                  label="Reject when face is soft"
                  tooltip="When a face is detected and its sharpness is below the floor below — but the overall frame is sharp — the photo is auto-rejected (the photographer focused on the wrong subject)."
                  enabled={decision.reject_soft_face}
                  onChange={v => updField('reject_soft_face', v)}
                />
                {decision.reject_soft_face && (
                  <NumberRow
                    label="Face sharpness floor"
                    tooltip={`Faces with sharpness below this number trigger a soft-face reject. Default ${DECISION_DEFAULTS.face_sharpness_floor}. Only applies when the frame itself is sharp (≥ critical-blur floor).`}
                    value={decision.face_sharpness_floor}
                    min={5} max={50} step={1}
                    onChange={v => updField('face_sharpness_floor', v)}
                    indent
                  />
                )}
                <ToggleRow
                  label="Reject when frame is critically blurry"
                  tooltip="Photos whose frame sharpness is below the floor below are auto-rejected. Sharpness is non-recoverable in post, so these can't be saved by RAW edits."
                  enabled={decision.reject_blurry_frame}
                  onChange={v => updField('reject_blurry_frame', v)}
                />
                {decision.reject_blurry_frame && (
                  <NumberRow
                    label="Critical-blur floor"
                    tooltip={`Frames with sharpness below this number are rejected. Default ${DECISION_DEFAULTS.fallback_sharpness_floor}.`}
                    value={decision.fallback_sharpness_floor}
                    min={10} max={70} step={1}
                    onChange={v => updField('fallback_sharpness_floor', v)}
                    indent
                  />
                )}
                <ToggleRow
                  label="Reject above ISO ceiling"
                  tooltip="Photos shot at ISO higher than the ceiling below are auto-rejected. Useful if you know your camera becomes too noisy above a certain sensitivity."
                  enabled={decision.reject_above_iso_ceiling}
                  onChange={v => updField('reject_above_iso_ceiling', v)}
                />
                {decision.reject_above_iso_ceiling && (
                  <NumberRow
                    label="ISO ceiling"
                    tooltip="Photos with ISO above this value are instantly rejected. Set to your camera's usable-noise threshold (e.g. 6400 for Z6 III, 3200 for X100VI)."
                    value={decision.iso_ceiling}
                    min={100} max={204800} step={100}
                    onChange={v => updField('iso_ceiling', v)}
                    indent
                  />
                )}
                <ToggleRow
                  label="Reject reciprocal-rule violations"
                  tooltip="If the shutter speed is slower than 1÷focal-length (e.g. 1/30 at 50mm), the shot risks camera shake. Enable when shooting handheld without image stabilisation."
                  enabled={decision.reject_reciprocal_rule}
                  onChange={v => updField('reject_reciprocal_rule', v)}
                />
              </div>

            </div>

            <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
              <p className="label mb-3">Grouping</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-4">
                Controls how similar-photo bursts are formed. Most users won't need to touch this — manually moving photos in and out of groups is usually the right answer. Changes take effect immediately and re-cluster the whole library.
              </p>

              <div className="mb-4">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-[#cecece]">Similarity</span>
                  <span className="text-xs text-[#9c9c9d] tabular-nums">{Math.round((groupThreshold ?? 0.9) * 100)}%</span>
                </div>
                <input
                  type="range"
                  min="0.80"
                  max="0.99"
                  step="0.01"
                  value={groupThreshold ?? 0.9}
                  onChange={e => setGroupThreshold?.(parseFloat(e.target.value))}
                  className="w-full accent-[#5BB8D4]"
                />
                <p className="text-[10px] text-[#777] mt-1 leading-tight">
                  Higher = stricter grouping. Lower = more photos per burst.
                </p>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-[#cecece]">Time gap</span>
                  <span className="text-xs text-[#9c9c9d] tabular-nums">
                    {(() => {
                      const total = Math.round(groupTimeGapSeconds ?? 60)
                      if (total < 60) return `${total} seconds`
                      const m = Math.floor(total / 60)
                      const s = total % 60
                      return `${m}:${String(s).padStart(2, '0')} minutes`
                    })()}
                  </span>
                </div>
                <input
                  type="range"
                  min="15"
                  max="600"
                  step="15"
                  value={groupTimeGapSeconds ?? 60}
                  onChange={e => setGroupTimeGapSeconds?.(parseFloat(e.target.value))}
                  className="w-full accent-[#5BB8D4]"
                />
                <p className="text-[10px] text-[#777] mt-1 leading-tight">
                  Max pause between shots in the same burst. Longer pauses start a new burst.
                </p>
              </div>
            </div>

            <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
              <p className="label mb-3">AI vision model</p>

              <OllamaStatusPanel />

              {/* Auto-generate toggle */}
              <div className="flex items-start justify-between gap-4 mt-4">
                <div>
                  <p className="text-xs text-[#f0f0f0] mb-1">Auto-generate explanation</p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed">
                    When on, opening a photo in detail view automatically asks the model to explain the rating. When off, a "Generate" button appears instead. The burst pick is independent of this toggle and runs automatically when a vision model is installed.
                  </p>
                </div>
                <Toggle enabled={autoGenerate} onChange={onAutoGenerateChange} />
              </div>
            </div>

            {/* Personal model status + manual retrain.
                The banner below is the same component shown above the grid.
                Without onDismiss it can't be closed from here — Settings is
                the permanent home for this view once the user hides it
                from the grid. */}
            {modelInfo && (
              <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
                <p className="label mb-3">Personal scoring model</p>
                {bannerDismissed && <PersonalModelBanner modelInfo={modelInfo} />}

                {/* Retrain now — non-destructive, manual override.
                    Auto-train fires every 10 new decisions on its own; this
                    button is just for users who want to force it sooner.
                    Disabled when there's nothing new to learn from. */}
                {(() => {
                  const canRetrain =
                    modelInfo.decided_count >= modelInfo.min_decisions &&
                    (modelInfo.pending_samples > 0 || !modelInfo.ready) &&
                    !training &&
                    !modelInfo.auto_running
                  const hint = !canRetrain
                    ? modelInfo.auto_running
                      ? 'Auto-training is currently running — try again in a moment.'
                      : modelInfo.decided_count < modelInfo.min_decisions
                        ? `Available once you have ${modelInfo.min_decisions} decisions.`
                        : modelInfo.pending_samples === 0 && modelInfo.ready
                          ? 'Already up to date — make more decisions first.'
                          : ''
                    : ''
                  return (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-xs text-[#9c9c9d] leading-relaxed flex-1">
                          The model retrains itself in the background every {modelInfo.retrain_delta ?? 10} new decisions.
                          Force an immediate retrain if you can't wait.
                        </p>
                        <button
                          onClick={onTrain}
                          disabled={!canRetrain}
                          title={hint || 'Retrain on all current samples'}
                          className={`flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-opacity ${
                            canRetrain
                              ? 'border border-[rgba(123,130,201,0.50)] text-[#7B82C9] hover:opacity-70'
                              : 'border border-[rgba(255,255,255,0.08)] text-[#434345] cursor-default'
                          }`}
                        >
                          <Brain size={16} />
                          {training || modelInfo.auto_running ? 'Training…' : 'Retrain now'}
                        </button>
                      </div>
                      {hint && (
                        <p className="text-[11px] text-[#6a6b6c] leading-relaxed">{hint}</p>
                      )}
                    </div>
                  )
                })()}

                {/* Optional: manual training session — kept as secondary CTA. */}
                {undecidedCount > 0 && (
                  <div className="mt-3 pt-3 border-t border-[rgba(255,255,255,0.05)]">
                    <button
                      onClick={() => { onClose(); onStartTraining() }}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-opacity border border-[rgba(255,255,255,0.10)] text-[#cecece] hover:opacity-70"
                    >
                      <GraduationCap size={16} />
                      Start training session · {undecidedCount}
                    </button>
                    <p className="text-[11px] text-[#6a6b6c] leading-relaxed mt-1.5">
                      Score-blind cull mode for fast taste calibration.
                    </p>
                  </div>
                )}
              </div>
            )}

            <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 space-y-4">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-red-500/70">Danger zone</p>

              {/* Reload AI scoring models. The personal-model retrain
                  control lives in the model section above (it's a normal,
                  non-destructive action now that auto-training is the
                  default — it doesn't belong in the Danger Zone). */}
              {reloadConfirm ? (
                <div className="space-y-3">
                  <p className="text-xs text-[#cecece] leading-relaxed">
                    This drops <span className="text-[#f0f0f0]">TOPIQ-NR</span>, <span className="text-[#f0f0f0]">TOPIQ-IAA</span>, and <span className="text-[#f0f0f0]">SigLIP</span> from RAM and re-loads them from disk in the background. Existing scores in the database are not changed.
                  </p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed">
                    Loading takes ~25–30 seconds. Any analysis you start in that window will block until the models are ready again.
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setReloadConfirm(false)}
                      className={`${BTN_SECONDARY} text-xs`}
                    >
                      <X size={14} /> Cancel
                    </button>
                    <button
                      onClick={() => { setReloadConfirm(false); handleReloadModels() }}
                      disabled={reloading}
                      className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <RefreshCw size={15} />
                      {reloading ? 'Reloading…' : 'Yes, reload models'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <p className="text-xs text-[#cecece] leading-relaxed mb-1">Reload AI scoring models</p>
                    <p className="text-xs text-[#9c9c9d] leading-relaxed">
                      The TOPIQ-NR, TOPIQ-IAA, and SigLIP models load once at startup and stay warm in RAM. You only need this if you've manually replaced a model file on disk (pulled a new version, swapped weights) and want the running app to pick it up without restarting the backend. Existing scores aren't recomputed — only future analyses use the new weights.
                    </p>
                  </div>
                  <button
                    onClick={() => setReloadConfirm(true)}
                    className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors"
                  >
                    <RefreshCw size={15} /> Reload models
                  </button>
                </div>
              )}

              {/* Clear analysis */}
              {onClear && (
                <>
                  <div className="border-t border-red-900/40 pt-4" />
                  {clearConfirm ? (
                    <div className="space-y-3">
                      <p className="text-xs text-[#cecece] leading-relaxed">
                        This removes every analyzed photo from the app — including your{' '}
                        <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
                        <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
                        <DecisionWord kind="reject">Reject</DecisionWord>
                        {' '}decisions, similarity groups, and cached previews. Your RAW files on disk are not touched.
                      </p>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[#cecece] font-mono shrink-0">
                          {clearChallenge?.a} + {clearChallenge?.b} =
                        </span>
                        <input
                          type="text"
                          inputMode="numeric"
                          value={clearAnswer}
                          onChange={e => setClearAnswer(e.target.value)}
                          placeholder="?"
                          autoFocus
                          className="w-14 px-2 py-1 rounded bg-[#1b1c1e] border border-[rgba(255,255,255,0.08)] text-xs text-[#f9f9f9] font-mono text-center focus:outline-none focus:border-red-800/60"
                        />
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={() => { setClearConfirm(false); setClearAnswer('') }}
                          className={`${BTN_SECONDARY} text-xs`}
                        >
                          <X size={14} /> Cancel
                        </button>
                        <button
                          onClick={() => { setClearConfirm(false); setClearAnswer(''); onClear() }}
                          disabled={clearing || clearAnswer.trim() !== clearChallenge?.answer}
                          className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <Trash2 size={15} />
                          {clearing ? 'Clearing…' : 'Yes, clear everything'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <p className="text-xs text-[#cecece] leading-relaxed mb-1">Clear analysis</p>
                        <p className="text-xs text-[#9c9c9d] leading-relaxed">
                          Remove all analyzed photos, decisions, and cached previews from the app. RAW files on disk are not touched.
                        </p>
                      </div>
                      <button
                        onClick={openClearConfirm}
                        disabled={clearing}
                        className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <Trash2 size={15} />
                        {clearing ? 'Clearing…' : 'Clear analysis'}
                      </button>
                    </div>
                  )}
                </>
              )}

              {/* Reset personal model — drops training samples + pairwise
                  comparisons + the persisted .pkl. Decisions on photos are
                  preserved. Two-step confirm, no typed challenge. */}
              {onResetModel && (
                <>
                  <div className="border-t border-red-900/40 pt-4" />
                  {resetModelConfirm ? (
                    <div className="space-y-3">
                      <p className="text-xs text-[#cecece] leading-relaxed">
                        This wipes everything the personal taste model has learned: all training samples, all pairwise A/B comparisons, and the saved model file. The banner returns to 0 samples. Your{' '}
                        <DecisionWord kind="keep">Keep</DecisionWord>{' / '}
                        <DecisionWord kind="maybe">Maybe</DecisionWord>{' / '}
                        <DecisionWord kind="reject">Reject</DecisionWord>
                        {' '}decisions on photos are kept — only the learned model is reset.
                      </p>
                      <div className="flex gap-2">
                        <button
                          onClick={() => setResetModelConfirm(false)}
                          disabled={resetModelRunning}
                          className={`${BTN_SECONDARY} text-xs`}
                        >
                          <X size={14} /> Cancel
                        </button>
                        <button
                          onClick={async () => {
                            setResetModelRunning(true)
                            try { await onResetModel() } finally {
                              setResetModelRunning(false)
                              setResetModelConfirm(false)
                            }
                          }}
                          disabled={resetModelRunning}
                          className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <Trash2 size={15} />
                          {resetModelRunning ? 'Resetting…' : 'Yes, reset model'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <p className="text-xs text-[#cecece] leading-relaxed mb-1">Reset personal taste model</p>
                        <p className="text-xs text-[#9c9c9d] leading-relaxed">
                          Clear all training samples, pairwise comparisons, and the saved model. The banner restarts at 0 samples and auto-cull falls back to quality-only scoring until the model retrains. Photo decisions on disk are untouched.
                        </p>
                      </div>
                      <button
                        onClick={() => setResetModelConfirm(true)}
                        disabled={resetModelRunning}
                        className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <Trash2 size={15} /> Reset model
                      </button>
                    </div>
                  )}
                </>
              )}

              {/* Reset dashboard — wipes shooting_log so the Dashboard's
                  camera / lens / focal-length / aperture / ISO / timeline
                  cards start over. Does not touch training_samples; the
                  personal model and its decision-history cards are
                  preserved. Two-step confirm. */}
              {onResetDashboard && (
                <>
                  <div className="border-t border-red-900/40 pt-4" />
                  {resetDashboardConfirm ? (
                    <div className="space-y-3">
                      <p className="text-xs text-[#cecece] leading-relaxed">
                        This clears the shooting history that powers the Dashboard's camera, lens, film-simulation, focal-length, aperture, ISO, and shots-per-week cards. The personal taste model and its decision-history cards are preserved — use <span className="text-[#f0f0f0]">Reset personal taste model</span> too if you also want to wipe the model.
                      </p>
                      <div className="flex gap-2">
                        <button
                          onClick={() => setResetDashboardConfirm(false)}
                          disabled={resetDashboardRunning}
                          className={`${BTN_SECONDARY} text-xs`}
                        >
                          <X size={14} /> Cancel
                        </button>
                        <button
                          onClick={async () => {
                            setResetDashboardRunning(true)
                            try { await onResetDashboard() } finally {
                              setResetDashboardRunning(false)
                              setResetDashboardConfirm(false)
                            }
                          }}
                          disabled={resetDashboardRunning}
                          className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <Trash2 size={15} />
                          {resetDashboardRunning ? 'Resetting…' : 'Yes, reset dashboard'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <p className="text-xs text-[#cecece] leading-relaxed mb-1">Reset dashboard</p>
                        <p className="text-xs text-[#9c9c9d] leading-relaxed">
                          Clear the camera / lens / film-sim / focal-length / aperture / ISO histograms and the shots-per-week timeline. Does not affect the personal model or your photo decisions. shooting_log rebuilds as you analyze new photos.
                        </p>
                      </div>
                      <button
                        onClick={() => setResetDashboardConfirm(true)}
                        disabled={resetDashboardRunning}
                        className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-red-800/60 text-red-400/80 hover:bg-red-900/40 hover:text-red-300 hover:border-red-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <Trash2 size={15} /> Reset dashboard
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>

          </div>
        )}

        {/* ── Display tab ── */}
        {activeTab === 'display' && (
          <div className="space-y-6">

            {/* ── UI Scale ── */}
            <div>
              <p className="label mb-1">Interface size</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                Scale the entire interface — text, buttons, icons, and spacing — to match your display and preference.
              </p>
              <div className="inline-flex rounded-lg border border-[rgba(255,255,255,0.10)] overflow-hidden">
                {[
                  { key: 'S', label: 'S', title: 'Compact — original size' },
                  { key: 'M', label: 'M', title: 'Medium — 15% larger (recommended)' },
                  { key: 'L', label: 'L', title: 'Large — 30% larger' },
                ].map(({ key, label, title }, i) => (
                  <button
                    key={key}
                    onClick={() => onUiScaleChange(key)}
                    title={title}
                    className={`px-4 py-1.5 text-xs font-medium transition-colors ${i > 0 ? 'border-l border-[rgba(255,255,255,0.10)]' : ''} ${uiScale === key ? 'bg-[#1a1b1d] text-[#f0f0f0]' : 'text-[#9c9c9d] hover:opacity-70'}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-[#6a6b6c] mt-2">
                Takes effect immediately. Saved across sessions.
              </p>
            </div>

            <div className="border-t border-[rgba(255,255,255,0.05)]" />

            {/* ── Grid filenames ── */}
            <div>
              <p className="label mb-1">Grid filenames</p>
              <div className="flex items-start justify-between gap-4 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-[#f0f0f0]">Show filenames under thumbnails</p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed mt-1">
                    Displays the filename row beneath every grid tile. Turn it
                    off for a cleaner, image-first grid — the status chips and
                    group summary stay. Toggle anytime with{' '}
                    <kbd className="px-1 py-0.5 rounded border border-[rgba(255,255,255,0.15)] bg-[rgba(255,255,255,0.06)] font-mono text-[10px] text-[#9c9c9d]">F</kbd>.
                  </p>
                </div>
                <div className="pt-0.5">
                  <Toggle
                    enabled={showFilenames}
                    onChange={() => onToggleFilenames?.()}
                  />
                </div>
              </div>
            </div>

            <div className="border-t border-[rgba(255,255,255,0.05)]" />

            <div>
              <p className="label mb-1">Preview source</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-4">
                When a RAW shot has a camera-baked preview alongside it (Fuji
                .HIF or any .JPG written next to the .RAF / .NEF), choose
                whether to show the RAW or the camera’s rendition in the grid
                and detail view.
              </p>

              <div className="flex items-start justify-between gap-4 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-[#f0f0f0]">Prefer camera JPEG / HIF preview</p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed mt-1">
                    Faster to render and shows the camera’s film simulation or
                    Picture Control. The RAW remains the source of truth for
                    scoring and for the file you keep — only the on-screen
                    preview changes.
                  </p>
                </div>
                <div className={`pt-0.5 ${savingDisplay ? 'opacity-50 pointer-events-none' : ''}`}>
                  <Toggle
                    enabled={preferSidecar}
                    onChange={handleSidecarToggle}
                  />
                </div>
              </div>

              <p className="text-[11px] text-[#9c9c9d] leading-relaxed mt-3">
                Cached previews are regenerated the first time each photo is
                viewed after the toggle changes — there’s a brief delay on the
                next grid scroll.
              </p>
            </div>

            <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
              <p className="label mb-1">Reject destination</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-4">
                Where rejected photos go. By default they live in a per-folder
                <span className="font-mono text-[#cecece]"> _Trash/</span>
                subfolder. Switch on the toggle to send them to the system
                Trash instead — they can be recovered from the Trash
                bin until you empty it.
              </p>

              <div className="flex items-start justify-between gap-4 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-[#f0f0f0]">Send rejected photos to system Trash</p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed mt-1">
                    Applies only to future R presses. Already-rejected photos
                    stay where they are. HIF previews follow the file into
                    the Trash automatically.
                  </p>
                </div>
                <div className={`pt-0.5 ${savingTrash ? 'opacity-50 pointer-events-none' : ''}`}>
                  <Toggle
                    enabled={rejectToTrash}
                    onChange={handleRejectTrashToggle}
                  />
                </div>
              </div>
            </div>

            <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
              <p className="label mb-1">Advance cursor</p>
              <p className="text-xs text-[#9c9c9d] leading-relaxed mb-3">
                Where the focus jumps after each K / M / R. Match it to how you scan the grid.
              </p>
              <div className="flex items-center justify-end">
                <button
                  onClick={() => onAdvanceDirChange?.(advanceDir === 'forward' ? 'backward' : 'forward')}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs text-[#cecece] border border-[rgba(255,255,255,0.10)] bg-[#0d0e10] hover:opacity-70 transition-opacity whitespace-nowrap"
                  aria-label={`Advance cursor: ${advanceDir === 'forward' ? 'to the right' : 'to the left'}`}
                >
                  {advanceDir === 'forward'
                    ? <><ArrowRight size={13} /> to the right</>
                    : <><ArrowLeft size={13} /> to the left</>}
                </button>
              </div>
            </div>

            {/* ── Advanced — Sort options ────────────────────────────────
                Power-user toggles for which metrics show up under the bottom
                pill's Sort → Score submenu. Collapsed by default. */}
            <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
              <button
                onClick={() => setSortOptionsOpen(v => !v)}
                className="w-full flex items-center justify-between gap-2 text-left"
              >
                <div>
                  <p className="label mb-1">Advanced — Sort options</p>
                  <p className="text-xs text-[#9c9c9d] leading-relaxed">
                    Choose which metrics appear under the Sort menu's Score submenu.
                    Lean defaults are pre-selected; turn on more for power-user culling.
                  </p>
                </div>
                {sortOptionsOpen
                  ? <ChevronDown size={16} className="text-[#9c9c9d] flex-shrink-0" />
                  : <ChevronRight size={16} className="text-[#9c9c9d] flex-shrink-0" />}
              </button>

              {sortOptionsOpen && (
                <div className="mt-4 space-y-5">
                  {SCORE_GROUPS.map(groupName => {
                    const items = ALL_METRICS.filter(m => m.group === groupName)
                    if (items.length === 0) return null
                    return (
                      <div key={groupName}>
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-[11px] font-semibold text-[#cecece] tracking-wide whitespace-nowrap">
                            {groupName}
                          </span>
                          <div className="flex-1 h-px bg-[#2a2b2d]" />
                        </div>
                        <div className="space-y-2">
                          {items.map(metric => (
                            <div key={metric.id} className="flex items-center justify-between gap-4 py-1">
                              <p className="text-xs text-[#f0f0f0]">{metric.label}</p>
                              <Toggle
                                enabled={visibleMetrics.includes(metric.id)}
                                onChange={() => toggleMetric(metric.id)}
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Personal-model banner visibility — paired with the × on the
                banner itself. Dismiss confirms via modal; this is the way
                back. Only visible when onSetBannerDismissed is wired. */}
            {onSetBannerDismissed && (
              <div className="pt-4 border-t border-[rgba(255,255,255,0.05)]">
                <p className="label mb-1">Personal model banner</p>
                <div className="flex items-start justify-between gap-4 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-[#f0f0f0]">Show banner above the grid</p>
                    <p className="text-xs text-[#9c9c9d] leading-relaxed mt-1">
                      Shows the personal model progress strip above the photo grid.
                      The full status view is always available here in Settings → Model,
                      so dismiss the grid banner whenever it feels like enough.
                    </p>
                  </div>
                  <div className="pt-0.5">
                    <Toggle
                      enabled={!bannerDismissed}
                      onChange={(next) => onSetBannerDismissed(!next)}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
        </div>

        {/* Sticky footer — Model tab only. Always visible regardless of scroll
            position so the user never misses Apply / Reset. */}
        {activeTab === 'model' && (
          <div className="border-t border-[rgba(255,255,255,0.07)] bg-[#101111] px-6 py-3 rounded-b-xl flex-shrink-0">
            <div className="flex items-center justify-between gap-3">
              <button
                onClick={resetDecisionToDefaults}
                disabled={JSON.stringify(decision) === JSON.stringify(DECISION_DEFAULTS)}
                className="inline-flex items-center gap-1.5 text-xs text-[#9c9c9d] hover:text-[#cecece] transition-colors disabled:opacity-40 disabled:cursor-default"
                title="Reset every decision setting to its default"
              >
                <RotateCcw size={14} /> Reset all to defaults
              </button>
              <div className="flex items-center gap-3">
                {decisionDirty && decisionValid && (
                  <span className="text-xs text-[#E8B84A]">Unsaved changes</span>
                )}
                <button
                  onClick={handleSaveDecision}
                  disabled={savingDecision}
                  className={`${BTN_PRIMARY} text-xs disabled:opacity-40 disabled:cursor-default`}
                >
                  <Check size={14} />
                  {savingDecision ? 'Applying…' : 'Apply changes'}
                </button>
              </div>
            </div>
            {showValidationHint && !decisionValid && (
              <p className="text-xs text-[#C97B7B] text-right mt-2">
                Can't save — fix the highlighted fields above first.
              </p>
            )}
          </div>
        )}
      </div>

      {discardConfirm && (
        <ConfirmModal
          title="Discard unsaved threshold changes?"
          body="Your unsaved changes to the decision thresholds will be lost."
          confirmLabel="Discard"
          confirmTone="danger"
          onCancel={() => setDiscardConfirm(false)}
          onConfirm={() => { setDiscardConfirm(false); onClose() }}
        />
      )}
    </div>
  )
}

// ── Ollama status panel — replaces a static "Requires Ollama" blurb with
// state-aware guidance from /lm-status. Shows a colored dot + label
// (Connected / Not installed / Installed, not running / No models / Backend
// unreachable) and a Refresh action. The install/start/pull hint links only
// surface in the state where they apply, mirroring DetailView's
// OllamaUnavailable component so the user sees consistent advice everywhere.
function OllamaStatusPanel() {
  const [status,  setStatus]  = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(false)

  const refresh = async () => {
    setLoading(true)
    setError(false)
    try {
      const res = await fetch(`${API}/lm-status`)
      const data = await res.json()
      setStatus(data)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const s = status?.status
  // The backend now reports `vision_capable: false` when ready-but-text-only
  // (e.g. only `moondream` or `llama3` installed). The pull button should
  // appear in that case too, not just when no models are installed.
  const needsVisionPull = !loading && !error && (
    s === 'no_models' || (s === 'ready' && status?.vision_capable === false)
  )

  let dotColor = '#6a6b6c'
  let label    = 'Checking…'
  if (!loading) {
    if (error)                      { dotColor = '#C97B7B'; label = 'Backend unreachable' }
    else if (s === 'ready' && status?.vision_capable === false) {
      // Connected but the picked model can't see images — flag with the
      // warning amber so the user knows the burst ranker isn't usable yet.
      dotColor = '#E8B84A'
      label    = `Connected · ${status.model} (text-only)`
    }
    else if (s === 'ready')         { dotColor = '#7DB89A'; label = `Connected · ${status.model}` }
    else if (s === 'not_installed') { dotColor = '#E8B84A'; label = 'Not installed' }
    else if (s === 'not_running')   { dotColor = '#E8B84A'; label = 'Installed, not running' }
    else if (s === 'no_models')     { dotColor = '#E8B84A'; label = 'No models installed' }
  }

  let guidance = null
  if (!loading && !error) {
    if (s === 'not_installed') {
      guidance = <InstallOllamaCTA />
    } else if (s === 'not_running') {
      guidance = (
        <>Run <span className="font-mono text-[#cecece]">ollama serve</span> in a Terminal, then click Refresh.</>
      )
    } else if (needsVisionPull) {
      // Same affordance for both branches: button to pull qwen2.5vl, plus a
      // copy-paste fallback. When the user is in the "ready, text-only"
      // state they're typically upgrading from moondream — add a small
      // cleanup hint so they know how to reclaim the disk.
      const isUpgradeFlow = s === 'ready'
      const installedNames = Array.isArray(status?.models) ? status.models : []
      // Suggest removing only models that are NOT vision-capable per the
      // backend's prefix list — passed implicitly via vision_capable=false
      // on the connected state. We don't have the prefix list on the
      // frontend so we just call out the connected model by name.
      guidance = (
        <div className="space-y-2">
          <PullVisionModelButton onDone={refresh} />
          <p>
            Or run <span className="font-mono text-[#cecece]">ollama pull qwen2.5vl:7b</span> (≈6 GB, vision-capable) in Terminal — or browse{' '}
            <a href="https://ollama.com/library" target="_blank" rel="noreferrer" className="text-[#5BB8D4] hover:opacity-70 underline">ollama.com/library</a>.
          </p>
          {isUpgradeFlow && status?.model && (
            <p className="text-[#9c9c9d]">
              After qwen2.5vl is ready, reclaim disk space with{' '}
              <span className="font-mono text-[#cecece]">ollama rm {status.model}</span>.
            </p>
          )}
        </div>
      )
    }
  }

  const extraModels = s === 'ready' && Array.isArray(status?.models) && status.models.length > 1
    ? `${status.models.length} models available`
    : null

  // Non-active models — everything installed that isn't the auto-picker's
  // current pick. Surfaced as removable rows when we're ready AND there's
  // more than just the active model, so users who upgraded from moondream
  // can reclaim disk in one click instead of dropping to Terminal.
  const unusedModels = (s === 'ready' && Array.isArray(status?.models))
    ? status.models.filter(m => m !== status.model)
    : []

  const [deleting, setDeleting] = useState(null)
  const removeModel = async (name) => {
    // window.confirm keeps the affordance lightweight — building a full
    // modal for "delete a model the user explicitly asked to remove" is
    // overkill. The Settings panel already feels like a control panel,
    // so a system confirm matches the chrome.
    if (!window.confirm(`Remove ${name} from Ollama? This frees disk space; you can re-pull later if needed.`)) return
    setDeleting(name)
    try {
      const res = await fetch(`${API}/ollama-model/${encodeURIComponent(name)}`, { method: 'DELETE' })
      const body = await res.json().catch(() => null)
      if (!res.ok || body?.status !== 'ok') {
        // Surface what the backend told us, fall back to a generic line.
        window.alert(body?.detail || `Failed to remove ${name}`)
      }
    } finally {
      setDeleting(null)
      refresh()
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-[#9c9c9d] leading-relaxed">
        Two features rely on a local AI model that can actually look at your photos:
        <span className="text-[#cecece]"> picking the best shot from a burst</span> (the model compares every frame in the burst at once and chooses the keeper), and
        <span className="text-[#cecece]"> writing a short explanation of each photo's rating</span> in detail view.
        Both require a model that can see images (e.g. <span className="font-mono text-[#cecece]">qwen2.5vl</span>, <span className="font-mono text-[#cecece]">llava</span>). Text-only models can still produce explanations from numeric scores, but the burst pick will fall back to score-based selection.
      </p>

      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-[#cecece] min-w-0">
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: dotColor }} />
          <span className="truncate">{label}</span>
          {extraModels && (
            <span className="text-[#9c9c9d] shrink-0">· {extraModels}</span>
          )}
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="inline-flex items-center gap-1 text-[11px] text-[#9c9c9d] hover:text-[#cecece] transition-colors underline disabled:opacity-50 shrink-0"
        >
          <RefreshCw size={13} />
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {guidance && (
        // Wrapper is <div>, not <p>: some branches (InstallOllamaCTA, the
        // needsVisionPull state) render block-level children, which would
        // be invalid HTML inside a <p> and silently blank in production.
        <div className="text-xs text-[#9c9c9d] leading-relaxed">{guidance}</div>
      )}

      {unusedModels.length > 0 && (
        // Cleanup affordance — only renders when extra models exist alongside
        // the active one. Typical case: user upgraded from moondream → qwen
        // and wants to reclaim 1.6 GB. Each row is independently removable
        // because we don't know which extras the user wants gone vs. kept.
        <div className="pt-1 space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-[#6a6b6c]">
            Other installed models
          </div>
          {unusedModels.map(name => (
            <div key={name} className="flex items-center justify-between text-xs">
              <span className="font-mono text-[#9c9c9d] truncate">{name}</span>
              <button
                onClick={() => removeModel(name)}
                disabled={deleting === name}
                className="inline-flex items-center gap-1 text-[11px] text-[#9c9c9d] hover:text-[#C97B7B] transition-colors underline disabled:opacity-50 shrink-0"
                title={`Remove ${name} from Ollama to free disk space`}
              >
                {deleting === name ? 'Removing…' : 'Remove'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Row helpers used inside Decision thresholds ──────────────────────────────

function NumberRow({ label, tooltip, value, min, max, step = 1, onChange, indent = false, error }) {
  const borderClass = error
    ? 'border-[#C97B7B] focus:border-[#C97B7B]'
    : 'border-[rgba(255,255,255,0.08)] focus:border-[#5BB8D4]'
  // Local buffer so partially-typed values (e.g. "5" on the way to "55") aren't
  // clamped mid-keystroke. Clamp only on blur / Enter.
  const [draft, setDraft] = useState(String(value))
  const [editing, setEditing] = useState(false)
  useEffect(() => { if (!editing) setDraft(String(value)) }, [value, editing])
  const commit = () => {
    const n = Number(draft)
    if (Number.isNaN(n) || draft.trim() === '') {
      setDraft(String(value))
    } else {
      const clamped = Math.max(min, Math.min(max, n))
      onChange(clamped)
      setDraft(String(clamped))
    }
    setEditing(false)
  }
  return (
    <div className={indent ? 'pl-5' : ''}>
      <div className="flex items-center gap-3 mb-1">
        <span className="text-xs text-[#cecece] flex-1 inline-flex items-center gap-1.5">
          {label}
          <InfoTooltip>{tooltip}</InfoTooltip>
        </span>
        <input
          type="number"
          value={draft}
          min={min}
          max={max}
          step={step}
          onFocus={() => setEditing(true)}
          onChange={e => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
          className={`w-16 px-2 py-1 rounded bg-[#1b1c1e] border text-xs text-[#f9f9f9] font-mono text-right focus:outline-none transition-colors ${borderClass}`}
        />
      </div>
      {error && (
        <p className="text-[11px] text-[#C97B7B] mb-2 ml-0">{error}</p>
      )}
      {!error && <div className="mb-1" />}
    </div>
  )
}

function ToggleRow({ label, tooltip, enabled, onChange }) {
  return (
    <div className="flex items-center gap-3 mb-2">
      <span className="text-xs text-[#cecece] flex-1 inline-flex items-center gap-1.5">
        {label}
        <InfoTooltip>{tooltip}</InfoTooltip>
      </span>
      <Toggle enabled={enabled} onChange={onChange} />
    </div>
  )
}
