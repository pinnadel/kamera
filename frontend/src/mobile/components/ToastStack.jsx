import { createPortal } from 'react-dom'
import { CheckCircle2, AlertCircle, Info, RotateCcw, X } from 'lucide-react'

const ICONS = {
  success: CheckCircle2,
  error:   AlertCircle,
  info:    Info,
  undo:    RotateCcw,
}

const TINTS = {
  success: 'text-[#7DB89A]',
  error:   'text-[#C97B7B]',
  info:    'text-[#5BB8D4]',
  undo:    'text-[#5BB8D4]',
}

export function ToastStack({ toasts, onDismiss }) {
  if (!toasts.length) return null
  return createPortal(
    <div
      role="region"
      aria-live="polite"
      aria-label="Notifications"
      className="fixed left-4 right-4 z-[90] flex flex-col gap-2 pointer-events-none"
      style={{ bottom: 'calc(var(--m-bottom-chrome) + var(--safe-bottom) + 12px)' }}
    >
      {toasts.map(t => {
        const Icon = ICONS[t.type] || Info
        const tint = TINTS[t.type] || 'text-[#cecece]'
        return (
          <div key={t.id} className="m-toast pointer-events-auto" role="status">
            <span className={tint} aria-hidden="true"><Icon size={20} /></span>
            <span className="flex-1">{t.message}</span>
            {t.action && (
              <button
                type="button"
                className="m-toast-action"
                onClick={() => { t.action.onClick(); onDismiss(t.id) }}
              >
                {t.action.label}
              </button>
            )}
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => onDismiss(t.id)}
              className="inline-flex items-center justify-center w-9 h-9 -m-1 rounded-full text-[#9c9c9d] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </div>,
    document.body,
  )
}
