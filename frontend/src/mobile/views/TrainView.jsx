// TrainView — score-blind cull queue. Surfaces only EXIF and the photo,
// hiding all AI scores so the model trains on the user's eye, not on
// agreement-with-self. Re-shows photos at random [5,25] intervals to
// detect drift.

import { useEffect, useMemo, useState } from 'react'
import { RotateCcw, GraduationCap } from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { BottomNav } from '../components/BottomNav'
import { PhotoPager } from '../components/PhotoPager'
import { DecisionBar } from '../components/DecisionBar'
import { EmptyState } from '../components/EmptyState'
import { buildTrainingQueue } from '../../training'
import { formatShutter } from '../../ui/format'
import { useHaptic } from '../hooks/useHaptic'
import { useReducedMotion } from '../hooks/useReducedMotion'

export function TrainView(props) {
  const { images, setDecision, undoLast, addToast, goTo } = props
  const haptic = useHaptic()
  const reduced = useReducedMotion()

  const queue = useMemo(() => buildTrainingQueue(images), [images])
  const [idx, setIdx] = useState(0)
  const [reveal, setReveal] = useState(null) // { decision, agree }

  const item = queue[idx]
  const image = item?.image
  const totalUndecided = images.filter(img => !img.decision).length

  const handleDecide = async (decision) => {
    if (!image) return
    haptic(decision === 'reject' ? 'medium' : 'light')

    // Compute agreement vs the AI's overall_score band before sending.
    // This is purely for the post-decision reveal flash.
    const overall = image.overall_score
    const aiSays = overall == null ? null : (overall >= 65 ? 'keep' : overall >= 45 ? 'maybe' : 'reject')
    const agree = aiSays && aiSays === decision

    const ok = await setDecision(image.id, decision)
    if (!ok) return
    setReveal({ decision, agree, score: overall })
    const t = setTimeout(() => {
      setReveal(null)
      setIdx(i => i + 1)
    }, reduced ? 0 : 1500)
    return () => clearTimeout(t)
  }

  // Auto-skip past photos that already have a decision (shouldn't happen via
  // training queue but guards against stale local state).
  useEffect(() => {
    if (!item) return
    if (item.image?.decision) setIdx(i => i + 1)
  }, [item])

  if (!images?.length || queue.length === 0 || idx >= queue.length) {
    return (
      <>
        <TopBar title="Training" subtitle="Score-blind cull" />
        <main className="flex-1 flex flex-col">
          <EmptyState
            icon={GraduationCap}
            title={totalUndecided === 0 ? 'All caught up' : 'Training queue ready'}
            body={
              totalUndecided === 0
                ? 'Everything in this folder has a decision. Browse to revisit, or analyze another folder to keep training your taste model.'
                : 'Score-blind cull hides all AI signals so the model learns from your eye. Tap Start to begin a fresh queue of undecided photos.'
            }
            action={
              totalUndecided > 0 && (
                <button type="button" onClick={() => setIdx(0)} className="m-btn m-btn-primary">
                  Start training
                </button>
              )
            }
          />
        </main>
        <BottomNav active="train" onSelect={goTo} />
      </>
    )
  }

  return (
    <>
      <TopBar
        title="Training mode"
        subtitle={`${idx + 1} of ${queue.length}`}
        trailing={
          <button
            type="button"
            onClick={() => undoLast()}
            aria-label="Undo last decision"
            className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
          >
            <RotateCcw size={20} aria-hidden="true" />
          </button>
        }
      />

      <main
        className="flex-1 flex flex-col"
        style={{ paddingBottom: 'calc(112px + var(--safe-bottom))' }}
      >
        <div className="flex-1 relative">
          <PhotoPager image={image} alt={image?.filename} decisionTint={null} />

          {reveal && (
            <div
              className="m-reveal absolute inset-x-0 top-1/2 -translate-y-1/2 z-10 flex flex-col items-center pointer-events-none"
              role="status"
              aria-live="polite"
            >
              <span
                className="px-4 py-2 rounded-full bg-black/65 backdrop-blur-md text-[15px] font-semibold"
                style={{ color: reveal.agree ? '#9DD0B5' : '#F0CD7A' }}
              >
                {reveal.agree ? '✓ Agreed with AI' : '↻ Trains your eye'}
              </span>
              {reveal.score != null && (
                <span className="mt-2 text-[13px] font-mono text-[#cecece] m-tabular px-3 py-1 rounded-full bg-black/55 backdrop-blur-md">
                  AI overall: {Math.round(reveal.score)}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Score-blind EXIF strip — only camera context, no scores. */}
        {image && (
          <div className="px-4 py-2 text-[12px] font-mono text-[#9c9c9d] m-tabular flex flex-wrap gap-x-3 gap-y-1">
            {image.aperture != null && <span>f/{image.aperture}</span>}
            {image.shutter_speed != null && <span>{formatShutter(image.shutter_speed)}</span>}
            {image.iso != null && <span>ISO {image.iso}</span>}
            {image.focal_length_mm != null && <span>{image.focal_length_mm}mm</span>}
            {image.camera_model && <span>· {image.camera_model}</span>}
          </div>
        )}
      </main>

      <DecisionBar current={image?.decision} onDecide={handleDecide} disabled={!image || !!reveal} />
    </>
  )
}
