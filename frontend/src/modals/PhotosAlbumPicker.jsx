import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronLeft, Images, Library, Search, X } from 'lucide-react'
import { API } from '../api'
import { BTN_ICON, BTN_SECONDARY } from '../ui/buttons'

// PhotosAlbumPicker — choose a Photos library, then an album inside it.
//
// There is no album equivalent of POST /pick-folder (tkinter can only choose
// directories), so the lists are fetched from the backend.
//
// Why the library is a deliberate step rather than pure auto-detection:
// "which library is open" comes from a preference inside Photos' own
// container, gated behind a Files & Folders permission that macOS will not
// always let you grant to a detached backend process. Spotlight-based
// discovery needs no such grant, so picking explicitly always works where
// detection may not. Detection is kept as a hint (the "open" badge).

// Typing the path in works even when nothing can be discovered — reading the
// package needs only the folder permission the backend already has.
function ManualPath({ value, onChange, onSubmit }) {
  return (
    <div className="px-3 pt-3">
      <p className="text-xs text-[#6a6b6c] mb-1.5">Or enter the path to a .photoslibrary</p>
      <div className="flex items-center gap-2">
        <input
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && value.trim()) onSubmit() }}
          placeholder="~/Downloads/KaMeRa-Test.photoslibrary"
          spellCheck={false}
          className="flex-1 min-w-0 bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-lg px-3 py-2 text-xs text-[#f0f0f0] placeholder-[#6a6b6c] outline-none focus:border-[rgba(91,184,212,0.40)]"
        />
        <button className={`${BTN_SECONDARY} !py-2`} onClick={onSubmit}>Use</button>
      </div>
    </div>
  )
}

export default function PhotosAlbumPicker({ onPick, onClose }) {
  const [step, setStep] = useState('loading')      // loading | libraries | albums | error
  const [libraries, setLibraries] = useState([])
  const [library, setLibrary] = useState(null)     // { path, name }
  const [albums, setAlbums] = useState([])
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  // Last-resort escape hatch. Discovery has now been blocked twice by TCC in
  // ways that were invisible from the outside, so there is always a way in
  // that depends on nothing but the path itself.
  const [manualPath, setManualPath] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const loadAlbums = useCallback(async (lib) => {
    setStep('loading')
    try {
      const res = await fetch(`${API}/photos-albums?library_path=${encodeURIComponent(lib.path)}`)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`)
      setLibrary({ path: data.library_path, name: data.library_name })
      setAlbums(data.albums || [])
      setStep('albums')
    } catch (e) {
      setError(e.message)
      setStep('error')
    }
  }, [])

  // Remember the choice so later sessions and the decision path agree on it.
  const chooseLibrary = useCallback(async (lib) => {
    try {
      await fetch(`${API}/photos-library`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ library_path: lib.path }),
      })
    } catch { /* selection is a convenience; the explicit path below still works */ }
    loadAlbums(lib)
  }, [loadAlbums])

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/photos-libraries`)
      .then(async r => {
        const data = await r.json().catch(() => ({}))
        if (!r.ok) throw Object.assign(new Error(data.detail || `Server error ${r.status}`), { code: r.status })
        return data
      })
      .then(data => {
        if (cancelled) return
        const libs = data.libraries || []
        setLibraries(libs)
        const remembered = libs.find(l => l.path === data.selected)
        const open = libs.find(l => l.is_open)
        // Skip the library step only when the answer is unambiguous.
        const obvious = remembered || (libs.length === 1 ? libs[0] : open)
        if (obvious) loadAlbums(obvious)
        else if (libs.length === 0) { setError('No Photos library found on this Mac'); setStep('error') }
        else setStep('libraries')
      })
      .catch(err => {
        if (cancelled) return
        setError(err.code === 501 ? 'Photos albums are only available on macOS' : err.message)
        setStep('error')
      })
    return () => { cancelled = true }
  }, [loadAlbums])

  useEffect(() => { if (step === 'albums') inputRef.current?.focus() }, [step])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? albums.filter(a => a.path.toLowerCase().includes(q)) : albums
  }, [albums, query])

  const showLibraries = step === 'libraries'

  return (
    <div className="fixed inset-0 z-50 bg-[rgba(7,8,10,0.80)] flex items-center justify-center" onClick={onClose}>
      <div
        className="bg-[#0d0e10] border border-[rgba(255,255,255,0.10)] rounded-xl w-[520px] max-h-[72vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-5 pt-4 pb-3 border-b border-[rgba(255,255,255,0.07)]">
          <div className="min-w-0 flex items-start gap-2">
            {step === 'albums' && libraries.length > 1 && (
              <button
                className={`${BTN_ICON} !w-6 !h-6 -ml-1 mt-0.5`}
                onClick={() => setStep('libraries')}
                title="Choose a different library"
                aria-label="Choose a different library"
              >
                <ChevronLeft size={14} />
              </button>
            )}
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-[#f0f0f0]">
                {showLibraries ? 'Choose a Photos library' : 'Choose a Photos album'}
              </h2>
              {step === 'albums' && library && (
                <p className="text-xs text-[#6a6b6c] mt-0.5 truncate" title={library.path}>
                  {library.name}
                </p>
              )}
            </div>
          </div>
          <button className={BTN_ICON} onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        {step === 'loading' && (
          <div className="px-5 py-8 text-center">
            <p className="text-xs text-[#9c9c9d]">Reading your Photos library…</p>
            <p className="text-xs text-[#6a6b6c] mt-1">The first read parses the whole catalogue and can take a moment</p>
          </div>
        )}

        {step === 'error' && (
          <div className="px-5 py-8 text-center">
            <p className="text-xs text-[#C97B7B]">{error}</p>
            <div className="flex items-center justify-center gap-2 mt-4">
              {libraries.length > 0 && (
                <button className={BTN_SECONDARY} onClick={() => setStep('libraries')}>Choose a library</button>
              )}
              <button className={BTN_SECONDARY} onClick={onClose}>Close</button>
            </div>
            <div className="text-left mt-5">
              <ManualPath
                value={manualPath}
                onChange={setManualPath}
                onSubmit={() => chooseLibrary({ path: manualPath.trim().replace(/\/+$/, '') })}
              />
            </div>
          </div>
        )}

        {showLibraries && (
          <div className="overflow-y-auto px-2 py-2 flex-1">
            {libraries.map(lib => (
              <button
                key={lib.path}
                onClick={() => chooseLibrary(lib)}
                disabled={!lib.readable}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left hover:bg-[#151618] transition-colors disabled:opacity-40"
                title={lib.path}
              >
                <Library size={14} className="text-[#6a6b6c] shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs text-[#f0f0f0] truncate">{lib.name}</span>
                  <span className="block text-xs text-[#6a6b6c] truncate">{lib.path}</span>
                </span>
                {lib.is_open && (
                  <span className="text-xs text-[#5BB8D4] shrink-0 inline-flex items-center gap-1">
                    <Check size={12} /> open
                  </span>
                )}
              </button>
            ))}
            <ManualPath
              value={manualPath}
              onChange={setManualPath}
              onSubmit={() => chooseLibrary({ path: manualPath.trim().replace(/\/+$/, '') })}
            />
            <p className="px-3 pb-3 text-xs text-[#6a6b6c]">
              Decisions are written through Photos, so they land in whichever library Photos itself has open
            </p>
          </div>
        )}

        {step === 'albums' && (
          <>
            <div className="px-5 py-3 border-b border-[rgba(255,255,255,0.07)]">
              <div className="flex items-center gap-2 bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-lg px-3 py-2">
                <Search size={14} className="text-[#6a6b6c] shrink-0" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Filter albums"
                  className="bg-transparent text-xs text-[#f0f0f0] placeholder-[#6a6b6c] outline-none w-full"
                />
              </div>
            </div>

            <div className="overflow-y-auto px-2 py-2 flex-1">
              {filtered.length === 0 && (
                <p className="text-xs text-[#6a6b6c] text-center py-8">
                  {albums.length ? 'No album matches that filter' : 'This library has no albums yet'}
                </p>
              )}
              {filtered.map(album => (
                <button
                  key={album.uuid}
                  onClick={() => onPick(library.path, album.uuid, album.name)}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left hover:bg-[#151618] transition-colors"
                >
                  <Images size={14} className="text-[#6a6b6c] shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs text-[#f0f0f0] truncate">{album.name}</span>
                    {album.folders?.length > 0 && (
                      <span className="block text-xs text-[#6a6b6c] truncate">{album.folders.join(' / ')}</span>
                    )}
                  </span>
                  <span className="text-xs font-mono text-[#6a6b6c] shrink-0">{album.count}</span>
                </button>
              ))}
            </div>

            <p className="px-5 py-3 text-xs text-[#6a6b6c] border-t border-[rgba(255,255,255,0.07)]">
              Photos are analysed where they are. Decisions become
              {' '}<span className="text-[#9c9c9d]">KaMeRa:Keep</span> /
              {' '}<span className="text-[#9c9c9d]">Maybe</span> /
              {' '}<span className="text-[#9c9c9d]">Reject</span> keywords — no file is ever moved
            </p>
          </>
        )}
      </div>
    </div>
  )
}
