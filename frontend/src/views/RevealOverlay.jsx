// RevealOverlay — flashes 1.5s after each training decision
export function RevealOverlay({ reveal }) {
  if (!reveal?.visible) return null

  const { decided, aiScore, isOverride } = reveal
  const decisionColor = decided === 'keep' ? 'text-[#7DB89A]' : decided === 'reject' ? 'text-[#C97B7B]' : 'text-[#E8B84A]'
  const decisionLabel = decided === 'keep' ? 'Kept' : decided === 'reject' ? 'Rejected' : 'Maybe'

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center pointer-events-none">
      <div className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-2xl px-8 py-6 shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10),inset_0_1px_0_rgba(255,255,255,0.05)] flex flex-col items-center gap-3 min-w-48">
        <span className={`text-2xl font-bold ${decisionColor}`}>{decisionLabel}</span>
        {aiScore != null ? (
          <>
            <span className="text-sm text-[#cecece]">
              {isOverride ? 'You overruled the AI' : 'AI agrees with you'}
            </span>
            <span className="text-xs text-[#9c9c9d] font-mono">AI score: {aiScore.toFixed(0)}</span>
            {isOverride && (
              <span className="text-xs text-[#7B82C9]">★ great training signal</span>
            )}
          </>
        ) : (
          <span className="text-xs text-[#6a6b6c]">No AI score yet</span>
        )}
      </div>
    </div>
  )
}
