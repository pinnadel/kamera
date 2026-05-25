// TopBar — sticky top app bar with safe-area top inset, title, and a slot
// for trailing controls. Always blurred so content peeks through underneath
// without losing legibility.

import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, MoreHorizontal } from 'lucide-react'
import { ViewModePill } from '../../ViewModePill'

export function TopBar({
  title,
  subtitle,
  onBack,
  trailing,
  menu, // optional: array of { label, icon: Component, onClick, danger }
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  // Close on outside tap
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('touchstart', onDown)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('touchstart', onDown)
    }
  }, [menuOpen])

  return (
    <header
      className="sticky top-0 z-40 m-blur-surface border-b"
      style={{ paddingTop: 'var(--safe-top)' }}
    >
      <div
        className="flex items-center px-2 gap-2"
        style={{ height: 'var(--m-topbar-h)' }}
      >
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
            aria-label="Back"
          >
            <ChevronLeft size={22} aria-hidden="true" />
          </button>
        ) : (
          <div className="w-2" />
        )}

        <div className="flex-1 min-w-0 px-1">
          {subtitle ? (
            <>
              <p className="text-[11px] font-medium text-[#5BB8D4] truncate uppercase tracking-wide">{subtitle}</p>
              <h1 className="text-[17px] font-semibold text-[#f9f9f9] truncate -mt-0.5">{title}</h1>
            </>
          ) : (
            <h1 className="text-[17px] font-semibold text-[#f9f9f9] truncate">{title}</h1>
          )}
        </div>

        <div className="flex items-center gap-1">
          {trailing}
          {/* Mobile→desktop toggle, mirrored in the same icon row across every
              view so the user never has to hunt for it. Same visual weight
              as Help/Settings on desktop — discreet, always reachable. */}
          <ViewModePill mode="mobile" compact="mobile" />
          {menu && menu.length > 0 && (
            <div ref={menuRef} className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen(o => !o)}
                aria-label="More actions"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
              >
                <MoreHorizontal size={22} aria-hidden="true" />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 mt-2 min-w-[220px] rounded-2xl bg-[#1f2022] border border-white/10 shadow-2xl overflow-hidden p-1"
                >
                  {menu.map((item, i) => {
                    const Icon = item.icon
                    return (
                      <button
                        key={i}
                        type="button"
                        role="menuitem"
                        onClick={() => { setMenuOpen(false); item.onClick?.() }}
                        className={`w-full inline-flex items-center gap-3 h-12 px-3 rounded-xl text-[15px] focus-visible:outline-none focus-visible:bg-white/5 ${item.danger ? 'text-[#E8A0A0]' : 'text-[#f9f9f9]'} hover:bg-white/5`}
                      >
                        {Icon ? <Icon size={18} aria-hidden="true" className={item.danger ? '' : 'text-[#5BB8D4]'} /> : null}
                        <span className="flex-1 text-left">{item.label}</span>
                        {item.right}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
