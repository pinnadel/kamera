import { Check, X } from 'lucide-react'

// ConfirmModal — generic two-button confirmation. `confirmIcon` defaults to
// Check; pass a custom Lucide component (e.g. Trash2 from lucide-react) when
// the action's intent is clearer than a checkmark. `cancelIcon` defaults to X.
export function ConfirmModal({
  title, body, confirmLabel,
  confirmTone = 'danger',
  confirmIcon: ConfirmIcon = Check,
  cancelIcon:  CancelIcon  = X,
  onConfirm, onCancel,
}) {
  const confirmClass = confirmTone === 'danger'
    ? 'bg-[rgba(201,123,123,0.18)] text-[#C97B7B] border border-[rgba(201,123,123,0.40)] hover:opacity-80'
    : 'bg-[rgba(91,184,212,0.18)] text-[#5BB8D4] border border-[rgba(91,184,212,0.40)] hover:opacity-80'

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-6"
      onClick={onCancel}
    >
      <div
        className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl p-5 max-w-md w-full shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-sm font-semibold text-[#f0f0f0] mb-2">{title}</p>
        <p className="text-xs text-[#cecece] leading-relaxed mb-4">{body}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-[#1b1c1e] border border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70 transition-opacity"
          >
            <CancelIcon size={15} />
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-opacity ${confirmClass}`}
          >
            <ConfirmIcon size={15} />
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
