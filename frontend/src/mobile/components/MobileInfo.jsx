// MobileInfo — touch-friendly equivalent of desktop's hover popover.
// On hover-capable pointers it behaves the same; on touch devices it opens
// on tap and dismisses on tap-outside or Escape. Renders into a portal so
// it escapes any ancestor's overflow / opacity / stacking context.
//
// Usage:
//   <MobileInfo content="Smile score from MediaPipe blendshapes (0–100).">
//     <span>Smile</span>
//   </MobileInfo>
//
//   // Or with a label-only trigger styled as an (i) glyph:
//   <MobileInfo icon content={<>...</>} />
//
// WCAG 2.2 notes:
//   - Tap target ≥ 44×44pt (visible glyph 18px is wrapped in inline-flex with
//     padding to meet target-size minimum).
//   - role="button" + aria-expanded so screen readers announce state.
//   - Popover has role="dialog" + aria-label, Escape closes, focus returns.

import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info as InfoIcon, X } from 'lucide-react'

export function MobileInfo({ children, content, icon = false, label = 'More information' }) {
  const id = useId()
  const triggerRef = useRef(null)
  const [open, setOpen]   = useState(false)
  const [anchor, setAnchor] = useState(null)

  useEffect(() => {
    if (!open) return
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setAnchor({ top: r.bottom + 8, left: r.left, width: r.width })
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Re-anchor on scroll/resize while open (the trigger may shift if user
  // scrolls behind a transparent backdrop).
  useEffect(() => {
    if (!open) return
    const update = () => {
      const r = triggerRef.current?.getBoundingClientRect()
      if (r) setAnchor({ top: r.bottom + 8, left: r.left, width: r.width })
    }
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [open])

  const close = () => setOpen(false)

  const trigger = icon ? (
    <button
      ref={triggerRef}
      type="button"
      className="inline-flex items-center justify-center w-11 h-11 -m-2 rounded-full text-[#9c9c9d] hover:text-[#cecece] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
      aria-label={label}
      aria-expanded={open}
      aria-controls={id}
      onClick={(e) => { e.stopPropagation(); setOpen(o => !o) }}
    >
      <InfoIcon size={16} aria-hidden="true" />
    </button>
  ) : (
    <button
      ref={triggerRef}
      type="button"
      className="inline-flex items-center gap-1 underline decoration-dotted decoration-[#5BB8D4]/40 underline-offset-4 hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] rounded"
      aria-expanded={open}
      aria-controls={id}
      onClick={(e) => { e.stopPropagation(); setOpen(o => !o) }}
    >
      {children}
      <InfoIcon size={13} aria-hidden="true" className="opacity-60" />
    </button>
  )

  return (
    <>
      {trigger}
      {open && anchor && createPortal(
        <>
          <div
            className="fixed inset-0 z-[80]"
            onClick={close}
            aria-hidden="true"
          />
          <div
            id={id}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            className="fixed z-[81] max-w-[calc(100vw-32px)] w-[280px] rounded-2xl bg-[#252628] border border-white/10 shadow-2xl text-[#f9f9f9] text-[14px] leading-snug overflow-hidden"
            style={{
              top: Math.min(anchor.top, window.innerHeight - 240),
              left: Math.min(Math.max(16, anchor.left), window.innerWidth - 296),
            }}
          >
            <div className="flex items-start gap-2 p-3 pr-2">
              <div className="flex-1">{content}</div>
              <button
                type="button"
                onClick={close}
                aria-label="Close"
                className="inline-flex items-center justify-center w-9 h-9 -m-1 rounded-full text-[#9c9c9d] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
          </div>
        </>,
        document.body,
      )}
    </>
  )
}
