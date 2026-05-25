import { useCallback, useEffect, useState } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import { ArrowLeft, Check, LogOut } from 'lucide-react'
import { API } from '../api'
import { ScoreBadge, DecisionBadge } from '../ui/primitives'
import { pickHeadlineScore } from '../ui/format'

// PairwiseTrainingView — A/B training mode.
//
// Shows two photos side-by-side. The user picks which one they prefer
// (← / → keys or clicking the photo). Each pick is recorded as a
// pairwise comparison, which feeds the personal model at the next
// training cycle as a soft-weighted synthetic sample (0.4× a K/M/X).
//
// This is the "relative preference" complement to single-card training:
// instead of asking "is this good enough to keep?", it asks "between
// these two, which do you like more?" — a question humans answer more
// reliably and consistently.
export function PairwiseTrainingView({ images, sourceFolder, onExit, modelInfo }) {
  const [pairs, setPairs]     = useState([])   // [[a_id, b_id], …]
  const [idx, setIdx]         = useState(0)
  const [loading, setLoading] = useState(true)
  const [flash, setFlash]     = useState(null) // 'left' | 'right' | null
  const [done, setDone]       = useState(0)

  // Lock background scroll
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // Load candidate pairs on mount
  useEffect(() => {
    const params = new URLSearchParams({ n: '40' })
    if (sourceFolder) params.set('source_folder', sourceFolder)
    fetch(`${API}/pairwise-candidates?${params}`)
      .then(r => r.json())
      .then(data => { setPairs(data.pairs || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [sourceFolder])

  const imageMap = new Map(images.map(img => [img.id, img]))

  const currentPair = pairs[idx] || null
  const imgA = currentPair ? imageMap.get(currentPair[0]) : null
  const imgB = currentPair ? imageMap.get(currentPair[1]) : null

  const pick = useCallback(async (winnerId, loserId, side) => {
    if (!currentPair || flash) return
    setFlash(side)
    setDone(d => d + 1)
    // Fire and forget — non-critical, doesn't block navigation
    fetch(`${API}/pairwise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ winner_id: winnerId, loser_id: loserId, source_folder: sourceFolder }),
    }).catch(() => {})
    setTimeout(() => {
      setFlash(null)
      const next = idx + 1
      if (next >= pairs.length) {
        onExit()
      } else {
        setIdx(next)
      }
    }, 400)
  }, [currentPair, flash, idx, pairs.length, sourceFolder, onExit])

  const pickLeft  = useCallback(() => { if (imgA && imgB) pick(imgA.id, imgB.id, 'left')  }, [imgA, imgB, pick])
  const pickRight = useCallback(() => { if (imgA && imgB) pick(imgB.id, imgA.id, 'right') }, [imgA, imgB, pick])

  useHotkeys('arrowLeft',  pickLeft,  [pickLeft])
  useHotkeys('arrowRight', pickRight, [pickRight])
  useHotkeys('escape', onExit, [onExit])

  const total = pairs.length

  return (
    <div className="fixed inset-0 z-50 bg-[#07080a] flex flex-col">

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#2f3031] flex-shrink-0">
        <div>
          <span className="text-xs text-[#8a8a8a] font-mono">
            A/B training · {done} / {total} compared
          </span>
          <p className="text-[10px] text-[#9c9c9d] mt-0.5">
            Pick the photo you prefer — no right answer, just your taste
          </p>
        </div>
        <div className="w-40 bg-[#1b1c1e] rounded-full h-1">
          <div
            className="bg-[#5BB8D4] h-1 rounded-full transition-all"
            style={{ width: `${total > 0 ? (done / total) * 100 : 0}%` }}
          />
        </div>
        <div className="flex items-center gap-4">
          <span className="text-[10px] text-[#6a6b6c]">← / → to pick · Esc to exit</span>
          <button
            onClick={onExit}
            className="inline-flex items-center gap-1.5 text-xs text-[#6a6b6c] hover:opacity-70 transition-opacity"
          >
            <LogOut size={14} /> Exit
          </button>
        </div>
      </div>

      {/* A/B content */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center text-[#6a6b6c] text-sm">
          Loading pairs…
        </div>
      ) : !currentPair || !imgA || !imgB ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8">
          <p className="text-[#cecece] text-sm">
            {pairs.length === 0
              ? 'No undecided photos to compare — cull more photos first.'
              : 'All pairs reviewed for this session.'}
          </p>
          <button
            onClick={onExit}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded text-xs border border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70 transition-opacity"
          >
            <ArrowLeft size={15} /> Back to grid
          </button>
        </div>
      ) : (
        <div className="flex-1 min-h-0 grid grid-cols-2 gap-2 p-3">
          {[
            { img: imgA, side: 'left',  onClick: pickLeft,  label: 'A', key: '←' },
            { img: imgB, side: 'right', onClick: pickRight, label: 'B', key: '→' },
          ].map(({ img, side, onClick, label, key }) => {
            const selected = flash === side
            return (
              <button
                key={side}
                onClick={onClick}
                disabled={!!flash}
                className={`
                  relative flex flex-col rounded-lg overflow-hidden cursor-pointer transition-all
                  ${selected
                    ? 'ring-1 ring-[#5BB8D4] scale-[1.01]'
                    : 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(91,184,212,0.35)] hover:scale-[1.003]'
                  }
                `}
              >
                {/* Image */}
                <div className="flex-1 min-h-0 bg-[#07080a] flex items-center justify-center overflow-hidden">
                  <img
                    src={`${API}/previews/${img.id}`}
                    alt={img.filename}
                    className="max-h-full max-w-full object-contain select-none pointer-events-none"
                    draggable={false}
                  />
                  {/* Key hint overlay */}
                  {!flash && (
                    <span className="absolute top-3 right-3 w-7 h-7 rounded-full bg-[rgba(0,0,0,0.55)] text-[#cecece] text-[11px] font-mono flex items-center justify-center">
                      {key}
                    </span>
                  )}
                  {/* Pick flash */}
                  {selected && (
                    <div className="absolute inset-0 bg-[rgba(91,184,212,0.08)] flex items-center justify-center">
                      <Check size={32} strokeWidth={3} className="text-[#5BB8D4]" />
                    </div>
                  )}
                </div>
                {/* Footer */}
                <div className="flex items-center gap-2 px-3 py-2 bg-[#101111] border-t border-[rgba(255,255,255,0.04)] flex-shrink-0">
                  <span className="text-[10px] font-mono text-[#6a6b6c] w-4">{label}</span>
                  <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
                  {img.decision && <DecisionBadge decision={img.decision} />}
                  <span className="ml-auto text-[10px] font-mono text-[#9c9c9d] truncate" title={img.filename}>
                    {img.filename}
                  </span>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
