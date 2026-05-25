import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Undo2, X } from 'lucide-react'
import { Spinner } from './primitives'

// DownloadToast — fixed bottom-right, shown while AI models are loading.
// Kept separate from ToastStack because it needs its own elapsed-time ticker.
export function DownloadToast({ models }) {
  const [, setTick] = useState(0)
  const [dismissed, setDismissed] = useState(() => new Set())

  useEffect(() => {
    if (models.length === 0) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [models.length])

  const visible = models.filter(m => !dismissed.has(m.model_id ?? m.name))
  if (visible.length === 0) return null

  const now = Date.now() / 1000

  return createPortal(
    <div className="fixed bottom-5 right-5 z-[60] flex flex-col gap-2 pointer-events-none">
      {visible.map(m => {
        const elapsed = Math.max(0, Math.floor(now - m.started_at))
        const elapsedStr = elapsed < 60
          ? `${elapsed}s elapsed`
          : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s elapsed`
        const isDownloading = m.state === 'downloading'
        const dismissKey = m.model_id ?? m.name

        return (
          <div
            key={m.name}
            className="bg-[#101111] rounded-xl px-4 py-3 shadow-2xl border border-[rgba(255,255,255,0.10)] flex items-start gap-3 min-w-64 max-w-xs pointer-events-auto"
            style={{ boxShadow: 'inset 4px 0 0 0 #94B0C2, 0 25px 50px -12px rgba(0,0,0,0.25)' }}
          >
            <Spinner />
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-medium text-[#f9f9f9] leading-tight">
                {isDownloading ? 'Downloading' : 'Loading'} model
              </p>
              <p className="text-[12px] text-[#cecece] mt-0.5 truncate">{m.name}</p>
              <p className="text-[12px] text-[#9c9c9d] mt-1">
                {isDownloading ? `~${m.size_mb} MB · ${elapsedStr}` : elapsedStr}
              </p>
            </div>
            <button
              onClick={() => setDismissed(prev => new Set(prev).add(dismissKey))}
              className="w-6 h-6 inline-flex items-center justify-center opacity-30 hover:opacity-70 flex-shrink-0 transition-opacity -mt-0.5 -mr-1"
              aria-label="Dismiss"
            >
              <X size={16} />
            </button>
          </div>
        )
      })}
    </div>,
    document.body,
  )
}

// ToastStack — unified notification system replacing decisionError + toastMsg.
// Positioned bottom-center to avoid colliding with DownloadToast (bottom-right).
export function ToastStack({ toasts, onDismiss }) {
  if (toasts.length === 0) return null
  return createPortal(
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-[65] flex flex-col gap-2 pointer-events-none w-full max-w-sm px-4">
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>,
    document.body,
  )
}

// Single toast row. Owns its own auto-dismiss timer so hovering the toast
// pauses dismissal — important for long error messages the user needs time to
// read (and to click into to inspect the path). Hover pauses the countdown;
// mouseleave resumes from the remaining time (not a fresh full duration), so
// a 4s toast you hovered after 3s gives you 1s on exit, not another 4s.
function ToastItem({ toast, onDismiss }) {
  const [paused, setPaused] = useState(false)
  const duration = toast.duration ?? 4000
  // remainingRef holds time left when paused; deadlineRef holds the wall-clock
  // moment we should fire. On pause we freeze remaining = deadline - now; on
  // resume we schedule for `remaining` ms and set a new deadline.
  const remainingRef = useRef(duration)
  const deadlineRef = useRef(0)

  useEffect(() => {
    if (toast.persistent) return
    if (paused) return
    deadlineRef.current = Date.now() + remainingRef.current
    const id = setTimeout(() => onDismiss(toast.id), remainingRef.current)
    return () => {
      clearTimeout(id)
      // Capture how much time was left at the moment we got paused (or unmounted).
      remainingRef.current = Math.max(0, deadlineRef.current - Date.now())
    }
  }, [toast.id, toast.persistent, paused, onDismiss])

  const accent = toast.type === 'error' ? '#C97B7B'
               : toast.type === 'success' ? '#7DB89A'
               : toast.type === 'warning' ? '#E8B84A'
               : '#94B0C2'
  // Bumped alpha 0.12 → 0.92 on tinted variants so the text stays readable
  // against busy backgrounds (DetailView, GroupLoupe filmstrips). The old
  // translucent treatment got lost on top of photos.
  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className={`rounded-lg px-4 py-3 shadow-2xl flex items-center gap-3 pointer-events-auto border text-sm
        ${toast.type === 'error'   ? 'bg-[rgba(46,22,22,0.95)] border-[rgba(201,123,123,0.55)] text-[#f9f9f9]' : ''}
        ${toast.type === 'success' ? 'bg-[rgba(20,38,28,0.95)] border-[rgba(125,184,154,0.55)] text-[#f9f9f9]' : ''}
        ${toast.type === 'info'    ? 'bg-[#101111] border-[rgba(255,255,255,0.15)] text-[#f9f9f9]' : ''}
        ${toast.type === 'warning' ? 'bg-[rgba(46,36,16,0.95)] border-[rgba(232,184,74,0.55)] text-[#f9f9f9]' : ''}
      `}
      style={{ boxShadow: `inset 4px 0 0 0 ${accent}, 0 25px 50px -12px rgba(0,0,0,0.6)` }}
    >
      <span className="flex-1 leading-snug break-words">{toast.message}</span>
      {toast.action && (
        <button
          onClick={toast.action.onClick}
          className="inline-flex items-center gap-1 text-xs underline opacity-70 hover:opacity-100 flex-shrink-0 whitespace-nowrap font-medium"
        >
          <Undo2 size={14} /> {toast.action.label}
        </button>
      )}
      <button
        onClick={() => onDismiss(toast.id)}
        className="w-6 h-6 inline-flex items-center justify-center opacity-40 hover:opacity-90 flex-shrink-0 transition-opacity"
        aria-label="Dismiss"
      >
        <X size={16} />
      </button>
    </div>
  )
}
