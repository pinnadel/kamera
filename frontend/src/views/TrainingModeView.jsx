import { useCallback, useState } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import { ArrowLeft, GitCompareArrows, LogOut, Pause, Play } from 'lucide-react'
import { API } from '../api'
import { BTN_DANGER, BTN_PRIMARY, BTN_SECONDARY } from '../ui/buttons'
import { formatShutter } from '../ui/format'
import { DecisionWord } from '../ui/primitives'
import { ConflictModal } from '../modals/ConflictModal'
import { PersonalModelPanel } from './PersonalModelPanel'
import { RevealOverlay } from './RevealOverlay'

// TrainingModeView — fullscreen score-blind culling session with pause support
export function TrainingModeView({ queue, currentIdx, onDecide, onExit, modelInfo, onTrain, training, onEnterPairwise }) {
  const [reveal, setReveal]           = useState(null)
  const [conflict, setConflict]       = useState(null)
  const [paused, setPaused]           = useState(false)
  const [exitConfirm, setExitConfirm] = useState(false)
  const [showIntro, setShowIntro]     = useState(() => {
    try { return !localStorage.getItem('pca.trainingIntroSeen') } catch { return true }
  })

  const dismissIntro = useCallback(() => {
    try { localStorage.setItem('pca.trainingIntroSeen', '1') } catch { /* localStorage may be unavailable */ }
    setShowIntro(false)
  }, [])

  const item         = queue[currentIdx]
  const image        = item?.image
  const primaryTotal = queue.filter(q => !q.isReshow).length
  const primaryDone  = queue.slice(0, currentIdx).filter(q => !q.isReshow).length
  const reshowCount  = queue.slice(0, currentIdx).filter(q => q.isReshow).length

  const handleExit = useCallback(() => {
    if (primaryDone > 0 && !exitConfirm) { setExitConfirm(true); return }
    onExit()
  }, [primaryDone, exitConfirm, onExit])

  const decide = useCallback(async (decision) => {
    if (!image || reveal?.visible || conflict || paused || showIntro) return

    // Re-show conflict check
    if (item.isReshow && image.decision && image.decision !== decision) {
      setConflict({ image, previous: image.decision, latest: decision })
      return
    }

    // POST decision (fire and forget — optimistic)
    fetch(`${API}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_id: image.id, decision }),
    }).catch(() => {})

    const aiScore   = image.overall_score
    const aiKeep    = aiScore != null && aiScore >= 50
    const userKeep  = decision === 'keep'
    const isOverride = aiScore != null && aiKeep !== userKeep

    setReveal({ visible: true, decided: decision, aiScore, isOverride })
    setTimeout(() => {
      setReveal(null)
      onDecide()
    }, 1500)
  }, [image, item, reveal, conflict, paused, showIntro, onDecide])

  const resolveConflict = useCallback(async (decision) => {
    if (!conflict) return
    await fetch(`${API}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_id: conflict.image.id, decision }),
    }).catch(() => {})
    setConflict(null)

    const aiScore  = conflict.image.overall_score
    const aiKeep   = aiScore != null && aiScore >= 50
    const userKeep = decision === 'keep'
    setReveal({ visible: true, decided: decision, aiScore, isOverride: aiScore != null && aiKeep !== userKeep })
    setTimeout(() => {
      setReveal(null)
      onDecide()
    }, 1500)
  }, [conflict, onDecide])

  useHotkeys('k', () => decide('keep'),   [decide])
  useHotkeys('r', () => decide('reject'), [decide])
  useHotkeys('m', () => decide('maybe'),  [decide])
  useHotkeys('escape', () => {
    if (showIntro) { dismissIntro(); return }
    if (paused) { setPaused(false); return }
    if (exitConfirm) { setExitConfirm(false); return }
    if (!reveal?.visible && !conflict) handleExit()
  }, [showIntro, dismissIntro, paused, exitConfirm, reveal, conflict, handleExit])

  if (!image) return null

  return (
    <div className="fixed inset-0 z-50 bg-[#07080a] flex flex-col">

      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#2f3031] flex-shrink-0">
        <div>
          <span className="text-xs text-[#8a8a8a] font-mono">
            Training mode · {primaryDone} / {primaryTotal} decided
          </span>
          {modelInfo?.model_status === 'ready' && (
            <p className="text-[10px] text-[#5BB8D4] mt-0.5">
              Active learning — most uncertain photos first
            </p>
          )}
          {reshowCount > 0 && (
            <p className="text-[10px] text-[#9c9c9d] mt-0.5">
              +{reshowCount} re-shown for consistency check
            </p>
          )}
        </div>
        <div className="w-40 bg-[#1b1c1e] rounded-full h-1">
          <div
            className="bg-[#7B82C9] h-1 rounded-full transition-all"
            style={{ width: `${primaryTotal > 0 ? (primaryDone / primaryTotal) * 100 : 0}%` }}
          />
        </div>
        <div className="flex items-center gap-4">
          {onEnterPairwise && (
            <button
              onClick={onEnterPairwise}
              className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[11px] border border-[rgba(91,184,212,0.25)] text-[#5BB8D4] hover:opacity-70 transition-opacity"
              title="Switch to A/B pairwise training — pick your preferred photo from two at a time"
            >
              <GitCompareArrows size={14} /> A/B mode
            </button>
          )}
          <button
            onClick={() => setPaused(p => !p)}
            className="inline-flex items-center gap-1.5 text-xs text-[#6a6b6c] hover:opacity-70 transition-opacity"
          >
            {paused ? <><Play size={14} /> Resume</> : <><Pause size={14} /> Pause</>}
          </button>
          <button onClick={handleExit} className="inline-flex items-center gap-1.5 text-xs text-[#6a6b6c] hover:opacity-70 transition-opacity">
            <LogOut size={14} /> Exit training
          </button>
        </div>
      </div>

      {/* Main area */}
      <div className="flex flex-1 min-h-0">
        {/* Photo */}
        <div className="flex-1 flex items-center justify-center p-8 relative">
          <img
            src={`${API}/previews/${image.id}`}
            alt={image.filename}
            className={`max-h-full max-w-full object-contain rounded shadow-2xl transition-opacity ${paused ? 'opacity-30' : 'opacity-100'}`}
          />
          {paused && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl px-6 py-4 text-center shadow-2xl">
                <p className="text-sm text-[#f9f9f9] font-medium">Paused</p>
                <p className="text-xs text-[#6a6b6c] mt-1">Press Esc or Resume to continue</p>
              </div>
            </div>
          )}
        </div>

        {/* Info panel — EXIF only, scores hidden */}
        <div className="w-60 border-l border-[rgba(255,255,255,0.06)] p-5 flex flex-col gap-4">
          <div>
            <p className="text-xs text-[#9c9c9d] mb-1">File</p>
            <p className="text-xs text-[#f9f9f9] font-mono break-all">{image.filename}</p>
          </div>
          <div className="space-y-2">
            <p className="label">Camera</p>
            {[
              ['Body',     image.camera],
              ['Shutter',  formatShutter(image.shutter_speed)],
              ['Aperture', image.aperture ? `f/${image.aperture}` : null],
              ['Focal',    image.focal_length_mm ? `${image.focal_length_mm}mm` : null],
              ['ISO',      image.iso],
            ].map(([label, val]) => val != null && (
              <div key={label} className="flex justify-between text-xs">
                <span className="text-[#9c9c9d]">{label}</span>
                <span className="text-[#f9f9f9] font-mono">{val}</span>
              </div>
            ))}
          </div>
          <div className="mt-auto">
            <PersonalModelPanel modelInfo={modelInfo} onTrain={onTrain} training={training} />
            <p className="text-xs text-[#6a6b6c] italic">Scores hidden — pure gut call</p>
          </div>
        </div>
      </div>

      {/* Decision buttons */}
      <div className={`flex items-center justify-center gap-4 px-6 py-5 border-t border-[#2f3031] transition-opacity ${paused ? 'opacity-30 pointer-events-none' : ''}`}>
        <button
          onClick={() => decide('keep')}
          className="px-8 py-2.5 rounded-lg text-sm bg-transparent border border-[rgba(125,184,154,0.50)] text-[#7DB89A] hover:opacity-70 transition-opacity"
        >
          Keep <span className="ml-1 opacity-60">K</span>
        </button>
        <button
          onClick={() => decide('maybe')}
          className="px-8 py-2.5 rounded-lg text-sm bg-transparent border border-[rgba(232,184,74,0.50)] text-[#E8B84A] hover:opacity-70 transition-opacity"
        >
          Maybe <span className="ml-1 opacity-60">M</span>
        </button>
        <button
          onClick={() => decide('reject')}
          className="px-8 py-2.5 rounded-lg text-sm bg-transparent border border-[rgba(201,123,123,0.50)] text-[#C97B7B] hover:opacity-70 transition-opacity"
        >
          Reject <span className="ml-1 opacity-60">R</span>
        </button>
      </div>

      <RevealOverlay reveal={reveal} />
      <ConflictModal conflict={conflict} onResolve={resolveConflict} />

      {/* Exit confirmation */}
      {exitConfirm && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(7,8,10,0.80)]">
          <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl p-6 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)] max-w-xs w-full text-center space-y-4">
            <p className="text-sm font-medium text-[#f9f9f9]">Exit training?</p>
            <p className="text-xs text-[#cecece]">
              Your {primaryDone} decision{primaryDone !== 1 ? 's' : ''} are saved. The queue will reset next session.
            </p>
            <div className="flex gap-3 justify-center">
              <button onClick={() => setExitConfirm(false)} className={BTN_SECONDARY}>
                <ArrowLeft size={16} /> Keep going
              </button>
              <button onClick={onExit} className={BTN_DANGER}>
                <LogOut size={16} /> Exit
              </button>
            </div>
          </div>
        </div>
      )}

      {/* First-entry intro — shown once, gated by localStorage */}
      {showIntro && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-[rgba(7,8,10,0.92)]">
          <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl p-7 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)] max-w-md w-full">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-full bg-[#7B82C9]" />
              <p className="label">Training mode</p>
            </div>
            <h2 className="text-base font-semibold text-[#f9f9f9] mb-4">Teach the model your taste</h2>
            <div className="space-y-3 text-sm text-[#cecece] leading-relaxed mb-5">
              <p>
                Scores are <span className="text-[#f9f9f9] font-medium">hidden</span>. You'll see only the photo and EXIF — decide on instinct.
              </p>
              <p>
                Some photos will reappear later as a consistency check. If you change your mind, the app asks which decision to keep.
              </p>
              <p className="text-xs text-[#9c9c9d] pt-1">
                After {Math.max(30 - (modelInfo?.decided_count ?? 0), 0)} more decision{(30 - (modelInfo?.decided_count ?? 0)) === 1 ? '' : 's'}, you can train a personal model that adjusts every score to match your taste.
              </p>
            </div>
            <div className="flex items-center justify-between">
              <p className="text-[11px] text-[#6a6b6c]">
                K = <DecisionWord kind="keep">Keep</DecisionWord>
                {' · '}M = <DecisionWord kind="maybe">Maybe</DecisionWord>
                {' · '}R = <DecisionWord kind="reject">Reject</DecisionWord>
                {' · '}Esc = exit
              </p>
              <button onClick={dismissIntro} className={BTN_PRIMARY}>
                <Play size={16} /> Start culling
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
