// CollapsibleSection — chevron-only collapsible with bordered card chrome.
// Used by DetailView (EXIF, Explanation, Histogram) and the Dashboard
// (Keep vs reject groups). State persists in localStorage keyed by `storageKey`.
//
// `storageKey` is the FULL key (callers compose their own namespace, e.g.
// `pca.detail.section.exif` or `pca.dashboard.section.technical`) so this
// primitive doesn't lock callers into a single namespace.

import { useLocalStorageState } from '../hooks/useLocalStorageState'

export function Chevron({ open }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`text-[#9c9c9d] transition-transform duration-150 flex-shrink-0 ${open ? 'rotate-180' : ''}`}
    >
      <polyline points="4,6 8,10 12,6" />
    </svg>
  )
}

export function CollapsibleSection({ storageKey, label, headerRight, defaultOpen = true, children }) {
  const [open, setOpen] = useLocalStorageState(storageKey, defaultOpen)
  return (
    <div className={`-mx-3 rounded-lg border p-3 transition-colors ${open ? 'border-[rgba(255,255,255,0.10)]' : 'border-transparent hover:border-[rgba(255,255,255,0.10)]'}`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 text-left hover:opacity-80 transition-opacity"
        aria-expanded={open}
      >
        <p className="label flex-shrink-0">{label}</p>
        {headerRight && <span className="ml-auto mr-2">{headerRight}</span>}
        <span className={headerRight ? '' : 'ml-auto'}>
          <Chevron open={open} />
        </span>
      </button>
      {open && <div className="space-y-2 mt-3">{children}</div>}
    </div>
  )
}
