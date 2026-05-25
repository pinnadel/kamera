// CullView — single-photo decision screen. Primary surface.
//
// Layout:
//   - TopBar (back · "N of M" · Undo · kebab) — sticky top
//   - PhotoPager (gestures + zoom)            — full viewport between bars
//   - SignalStrip                             — between photo and decisions
//   - DecisionBar                             — fixed bottom (above safe-area)
//   - DetailSheet via BottomSheet             — opens on swipe-up or info kebab
//
// Bottom navigation is hidden in this view — the decision bar takes its place.

import { useEffect, useMemo, useState } from 'react'
import {
  RotateCcw, Info, ZoomIn, X,
  ChevronLeft, ChevronRight,
} from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { PhotoPager } from '../components/PhotoPager'
import { SignalStrip } from '../components/SignalStrip'
import { DecisionBar } from '../components/DecisionBar'
import { BottomSheet } from '../components/BottomSheet'
import { DetailSheet } from './DetailSheet'
import { useHaptic } from '../hooks/useHaptic'

export function CullView(props) {
  const { cullOrder, setDecision, undoLast, addToast, back, extra } = props
  const haptic = useHaptic()

  const [selectedId, setSelectedId] = useState(extra?.selectedId ?? cullOrder[0]?.id ?? null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)

  const idx = useMemo(() => cullOrder.findIndex(img => img.id === selectedId), [cullOrder, selectedId])
  const image = idx >= 0 ? cullOrder[idx] : null

  // Preserve the position when an image's identity changes underneath us
  // (e.g. a sort change). If the selectedId is no longer in the list, snap
  // to the closest valid index.
  useEffect(() => {
    if (idx >= 0) return
    if (cullOrder.length === 0) return
    setSelectedId(cullOrder[0].id)
  }, [idx, cullOrder])

  const goPrev = () => {
    if (idx <= 0) return
    setSelectedId(cullOrder[idx - 1].id)
  }
  const goNext = () => {
    if (idx >= cullOrder.length - 1) {
      addToast({ type: 'info', message: "End of folder — that's everything!" })
      return
    }
    setSelectedId(cullOrder[idx + 1].id)
  }

  const handleDecide = async (decision) => {
    if (!image) return
    haptic(decision === 'reject' ? 'medium' : 'light')
    const ok = await setDecision(image.id, decision)
    if (ok && idx < cullOrder.length - 1) {
      // Auto-advance after a short delay so the decision-tint animation has
      // a moment to read. Reduced-motion users get an instant advance.
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const delay = reduced ? 0 : 240
      setTimeout(() => {
        setSelectedId(cullOrder[idx + 1].id)
      }, delay)
    }
  }

  const handleUndo = async () => {
    haptic('light')
    await undoLast()
  }

  return (
    <>
      <TopBar
        title={image?.filename || 'Photo'}
        subtitle={cullOrder.length ? `${idx + 1} of ${cullOrder.length}` : 'No photos'}
        onBack={back}
        trailing={
          <>
            <button
              type="button"
              onClick={handleUndo}
              aria-label="Undo last decision"
              className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
            >
              <RotateCcw size={20} aria-hidden="true" />
            </button>
          </>
        }
        menu={[
          { label: 'View details',     icon: Info,             onClick: () => setSheetOpen(true) },
          { label: 'Open fullscreen',  icon: ZoomIn,           onClick: () => setFullscreen(true) },
          { label: 'Previous photo',   icon: ChevronLeft,      onClick: goPrev },
          { label: 'Next photo',       icon: ChevronRight,     onClick: goNext },
        ]}
      />

      <main
        className="flex-1 flex flex-col"
        style={{
          // Reserve space for the floating decision bar at the bottom.
          paddingBottom: 'calc(112px + var(--safe-bottom))',
        }}
      >
        <div className="flex-1 relative">
          <PhotoPager
            image={image}
            alt={image?.filename}
            onPrev={goPrev}
            onNext={goNext}
            onSwipeUp={() => setSheetOpen(true)}
            decisionTint={image?.decision || null}
          />

          {/* Edge tap zones for one-handed nav as a non-gesture alternative.
              Invisible 44pt-wide bars at the left/right edges of the photo,
              keeping the gesture region uncluttered while still meeting
              SC 2.5.7. */}
          {idx > 0 && (
            <button
              type="button"
              onClick={goPrev}
              aria-label="Previous photo"
              className="absolute top-0 bottom-0 left-0 w-11 focus-visible:outline-none focus-visible:bg-white/5"
            />
          )}
          {idx < cullOrder.length - 1 && (
            <button
              type="button"
              onClick={goNext}
              aria-label="Next photo"
              className="absolute top-0 bottom-0 right-0 w-11 focus-visible:outline-none focus-visible:bg-white/5"
            />
          )}
        </div>

        <SignalStrip image={image} onOpenDetail={() => setSheetOpen(true)} />
      </main>

      <DecisionBar current={image?.decision} onDecide={handleDecide} disabled={!image} />

      <BottomSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        ariaLabel={`Details for ${image?.filename || 'photo'}`}
        initialSnap={1}
      >
        <DetailSheet
          image={image}
          onOpenFullscreen={() => { setSheetOpen(false); setFullscreen(true) }}
        />
      </BottomSheet>

      {fullscreen && image && (
        <FullscreenView image={image} onClose={() => setFullscreen(false)} />
      )}
    </>
  )
}

function FullscreenView({ image, onClose }) {
  // Body-scroll lock + Escape to close.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Fullscreen ${image.filename}`}
      className="fixed inset-0 z-[100] bg-black flex items-center justify-center"
    >
      <PhotoPager image={image} alt={image.filename} />
      <button
        type="button"
        onClick={onClose}
        aria-label="Close fullscreen"
        className="absolute right-4 z-10 inline-flex items-center justify-center h-12 w-12 rounded-full bg-black/60 backdrop-blur-md text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
        style={{ top: 'calc(var(--safe-top) + 16px)' }}
      >
        <X size={22} aria-hidden="true" />
      </button>
    </div>
  )
}
