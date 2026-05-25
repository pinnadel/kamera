// PickFolderView — list of previously analyzed folders + native picker
// for new folders. Replaces the desktop multi-tab folder model with a
// single-active-folder pattern that fits better on small screens.

import { useState } from 'react'
import { Folder, FolderOpen, FolderPlus, ChevronRight } from 'lucide-react'
import { API } from '../../api'
import { TopBar } from '../components/TopBar'
import { EmptyState } from '../components/EmptyState'

function pathTail(p) {
  if (!p) return ''
  const segs = p.split('/').filter(Boolean)
  return segs[segs.length - 1] || p
}

export function PickFolderView({ folders, pickFolder, setActiveFolder, activeFolder, addToast, back, reloadFolders }) {
  const [analyzing, setAnalyzing] = useState(false)

  const onPick = async () => {
    const path = await pickFolder()
    if (!path) return
    setAnalyzing(true)
    try {
      const res = await fetch(`${API}/analyze-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_folder: path, watch_live: false }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        addToast({ type: 'error', message: body.detail || `Couldn't start analysis (${res.status})`, duration: 6000 })
        return
      }
      setActiveFolder(path)
      addToast({ type: 'info', message: `Analyzing ${pathTail(path)}…`, duration: 4000 })
      await reloadFolders()
      back()
    } finally {
      setAnalyzing(false)
    }
  }

  const onSelectExisting = (path) => {
    setActiveFolder(path)
    back()
  }

  return (
    <>
      <TopBar title="Folders" subtitle="Pick or analyze new" onBack={back} />

      <main className="flex-1 overflow-y-auto pb-32">
        <div className="px-4 pt-4">
          <button
            type="button"
            onClick={onPick}
            disabled={analyzing}
            className="ai-border w-full text-left disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#07080a]"
          >
            <span className="ai-border-inner rounded-[10.5px] flex items-center gap-3 p-3.5">
              <span className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-[rgba(91,184,212,0.10)]" aria-hidden="true">
                <FolderPlus size={20} className="text-[#5BB8D4]" />
              </span>
              <div className="flex-1">
                <p className="text-[15px] font-semibold text-[#f9f9f9]">{analyzing ? 'Analyzing…' : 'Analyze new folder'}</p>
                <p className="text-[12px] text-[#9c9c9d] mt-0.5">Pick a folder of RAW or JPEG photos</p>
              </div>
              <ChevronRight size={18} className="text-[#9c9c9d]" aria-hidden="true" />
            </span>
          </button>
        </div>

        <section className="mt-5 px-4">
          <h2 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] mb-2">Recent folders</h2>
          {!folders?.length ? (
            <EmptyState
              icon={Folder}
              title="No analyzed folders yet"
              body="Pick a folder above to get started. The app analyses sharpness, exposure, faces, aesthetics, and groups similar shots."
            />
          ) : (
            <ul className="rounded-2xl bg-[#101111] border border-white/5 overflow-hidden">
              {folders.map(f => {
                const path = typeof f === 'string' ? f : f.path
                const count = typeof f === 'string' ? null : f.count
                const isActive = path === activeFolder
                return (
                  <li key={path}>
                    <button
                      type="button"
                      onClick={() => onSelectExisting(path)}
                      aria-current={isActive ? 'page' : undefined}
                      className={`flex items-center gap-3 w-full text-left h-16 px-3 border-b border-white/5 last:border-0 hover:bg-white/5 focus-visible:outline-none focus-visible:bg-white/5 ${isActive ? 'bg-[rgba(91,184,212,0.08)]' : ''}`}
                    >
                      <span className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-[rgba(91,184,212,0.10)] flex-shrink-0">
                        {isActive ? <FolderOpen size={18} className="text-[#5BB8D4]" /> : <Folder size={18} className="text-[#5BB8D4]" />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[15px] font-medium text-[#f9f9f9] truncate">{pathTail(path)}</p>
                        <p className="text-[12px] text-[#9c9c9d] truncate" title={path}>{path}</p>
                      </div>
                      {count != null && (
                        <span className="text-[12px] font-mono text-[#9c9c9d] m-tabular flex-shrink-0">{count}</span>
                      )}
                      <ChevronRight size={16} className="text-[#9c9c9d] flex-shrink-0" aria-hidden="true" />
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </main>
    </>
  )
}
