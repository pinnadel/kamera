// BottomNav — primary navigation bar. Five destinations max. Each item has
// icon + visible label per WCAG 2.4.4 Link Purpose. Items are real <button>s
// with `aria-current="page"` to broadcast the active route to screen readers.

import { ImageIcon, Layers, GraduationCap, LayoutDashboard } from 'lucide-react'

const ITEMS = [
  { id: 'browse',    label: 'Browse',    Icon: ImageIcon },
  { id: 'groups',    label: 'Groups',    Icon: Layers },
  { id: 'train',     label: 'Train',     Icon: GraduationCap },
  { id: 'dashboard', label: 'Dashboard', Icon: LayoutDashboard },
]

export function BottomNav({ active, onSelect, badges = {} }) {
  return (
    <nav
      aria-label="Primary"
      className="fixed left-0 right-0 z-40 m-blur-surface border-t"
      style={{ bottom: 0, paddingBottom: 'var(--safe-bottom)' }}
    >
      <ul className="flex items-stretch">
        {ITEMS.map(({ id, label, Icon }) => {
          const isActive = active === id
          const badge = badges[id]
          return (
            <li key={id} className="flex-1">
              <button
                type="button"
                onClick={() => onSelect(id)}
                aria-current={isActive ? 'page' : undefined}
                className="m-nav-item relative w-full"
              >
                <span className="relative">
                  <Icon size={22} strokeWidth={isActive ? 2.4 : 2} aria-hidden="true" />
                  {badge ? (
                    <span
                      aria-label={`${badge} pending`}
                      className="absolute -top-1 -right-2 min-w-[16px] h-4 px-1 rounded-full bg-[#5BB8D4] text-[#07080a] text-[10px] font-bold flex items-center justify-center"
                    >
                      {badge > 99 ? '99+' : badge}
                    </span>
                  ) : null}
                </span>
                <span>{label}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
