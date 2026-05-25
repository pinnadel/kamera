// ViewModePill — bidirectional toggle between desktop (`/index.html`) and
// mobile (`/mobile.html`) bundles. Two render modes:
//
//   <ViewModePill mode="desktop" />            full pill (legacy floating)
//   <ViewModePill mode="desktop" compact />    icon-only button, slot-friendly
//
// Active folder is preserved across the swap by the `pca.activeFolderPath`
// localStorage key (already maintained by desktop tab restore + read by
// MobileApp on mount).
//
// WCAG 2.2 notes:
//   - Compact mode uses w-9 h-9 (36px) which is below the 44pt comfort
//     guideline but well above SC 2.5.8 (24×24 minimum). Lives in the
//     desktop top-right icon row alongside Settings and Help — same target
//     size as those.
//   - 2px solid focus-visible ring at the canonical cyan, 2px offset.
//   - aria-label fully describes destination; icon decorative.

import { Smartphone, Monitor } from 'lucide-react'

export function ViewModePill({ mode = 'desktop', compact = false, hidden = false }) {
  if (hidden) return null

  const target = mode === 'desktop' ? '/mobile.html' : '/index.html'
  const Icon = mode === 'desktop' ? Smartphone : Monitor
  const label = mode === 'desktop' ? 'Switch to mobile view' : 'Switch to desktop view'
  const shortLabel = mode === 'desktop' ? 'Mobile' : 'Desktop'

  const handleClick = () => {
    try {
      const stored = localStorage.getItem('pca.activeFolderPath')
      if (stored) localStorage.setItem('pca.activeFolderPath', stored)
    } catch {}
    window.location.href = target
  }

  if (compact) {
    // `compact` defaults to desktop sizing (36×36, matches BTN_ICON).
    // Mobile callers pass `mobile` for 44×44 to match TopBar icon row.
    const isMobile = compact === 'mobile'
    const sizing = isMobile
      ? 'inline-flex items-center justify-center w-11 h-11 rounded-full text-[#cecece] hover:text-[#f9f9f9]'
      : 'inline-flex items-center justify-center w-9 h-9 rounded-lg text-[#9c9c9d] hover:text-[#cecece] hover:opacity-100'
    return (
      <button
        type="button"
        onClick={handleClick}
        aria-label={label}
        title={label}
        className={`${sizing} transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]`}
      >
        <Icon size={isMobile ? 20 : 18} aria-hidden="true" />
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={label}
      title={label}
      className="
        group fixed bottom-4 right-4 z-[60]
        inline-flex items-center gap-2
        h-12 px-4 rounded-full
        bg-[#161718]/85 backdrop-blur-md
        border border-[rgba(255,255,255,0.10)]
        text-[#cecece] text-sm font-medium
        shadow-[0_8px_24px_rgba(0,0,0,0.45),0_0_0_1px_rgba(91,184,212,0.0)]
        hover:text-[#f9f9f9] hover:border-[rgba(91,184,212,0.35)]
        hover:shadow-[0_8px_24px_rgba(0,0,0,0.55),0_0_0_1px_rgba(91,184,212,0.35),0_0_18px_-4px_rgba(91,184,212,0.45)]
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#07080a]
        motion-safe:transition-[transform,box-shadow,border-color,color] motion-safe:duration-200
        motion-safe:active:scale-[0.97]
      "
    >
      <Icon size={18} strokeWidth={2} aria-hidden="true" className="text-[#5BB8D4]" />
      <span>{shortLabel}</span>
    </button>
  )
}
