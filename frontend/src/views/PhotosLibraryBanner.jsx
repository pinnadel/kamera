import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { API } from '../api'

// PhotosLibraryBanner — "Photos has a different library open".
//
// Decisions are written back through AppleScript, which always addresses
// whatever library Photos.app currently has open. If that is not the library
// this tab was analysed from, every K / M / R will 409.
//
// The keys stay live on purpose. A silently dead keyboard is the worst version
// of this: the 409 toast names the library to open, which is actionable, and
// disabling the keys would leave the user guessing why nothing happens.
// See docs/DESIGN.md on not disabling controls to communicate state.
export function PhotosLibraryBanner({ sourceFolder }) {
  const [status, setStatus] = useState(null)

  useEffect(() => {
    if (!sourceFolder) return
    let cancelled = false

    const check = () => {
      fetch(`${API}/photos-library-status?source_folder=${encodeURIComponent(sourceFolder)}`)
        .then(r => (r.ok ? r.json() : null))
        .then(data => { if (!cancelled) setStatus(data) })
        .catch(() => {})
    }

    check()
    // Re-check when the user comes back: switching libraries happens in
    // Photos, outside this app, so a focus event is the only hint we get.
    window.addEventListener('focus', check)
    return () => { cancelled = true; window.removeEventListener('focus', check) }
  }, [sourceFolder])

  if (!status || status.ok) return null

  return (
    <div className="mb-3 flex items-start gap-2.5 px-3 py-2 rounded-lg text-xs bg-[rgba(232,184,74,0.10)] border border-[rgba(232,184,74,0.30)]">
      <AlertTriangle size={14} className="text-[#E8B84A] shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-[#E8B84A]">
          {status.reason === 'unavailable'
            ? 'Photos is not reachable — decisions cannot be written right now'
            : <>Photos has <span className="font-semibold">{status.current_library_name}</span> open, but this tab came from <span className="font-semibold">{status.expected_library_name}</span></>}
        </p>
        {status.reason !== 'unavailable' && (
          <p className="text-[#9c9c9d] mt-0.5">
            Decisions will be refused until you switch back. Hold Option while opening Photos to choose a library
          </p>
        )}
      </div>
    </div>
  )
}
