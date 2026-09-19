import { useEffect, useState } from 'react'
import { Folder, Images, Plus, X } from 'lucide-react'
import { tabLabel } from '../tabs'

function TabItem({ tab, isActive, isFocusStop, isDragOver, dragHandlers, onSelect, onClose, watchingTabName }) {
  const ringClass = isDragOver ? 'ring-1 ring-[#5BB8D4] ring-inset' : ''

  const baseClass = isActive
    ? 'bg-[#1a1b1d] text-[#f9f9f9]'
    : 'text-[#8a8b8c] hover:text-[#9c9c9d] hover:bg-[rgba(255,255,255,0.03)]'

  const baseTitle = tab.folderPath || 'New analysis'
  const tabTitle = tab.watchLive
    ? `${baseTitle}\nWatch live: on`
    : watchingTabName
      ? `${baseTitle}\nWatch live: on "${watchingTabName}"`
      : baseTitle

  return (
    <div
      role="tab"
      id={`tab-${tab.id}`}
      aria-selected={isActive}
      tabIndex={isFocusStop ? 0 : -1}
      draggable={tab.status !== 'analyzing'}
      {...(dragHandlers || {})}
      onClick={() => onSelect(tab.id)}
      onKeyDown={(e) => {
        // Only the tab itself — Enter/Space on the nested close button must
        // keep meaning "close", not "select".
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(tab.id) }
      }}
      className={`group flex items-center gap-2 px-3 py-1.5 rounded-[6px] cursor-pointer flex-shrink-0 max-w-[220px] text-[14px] font-medium transition-colors ${baseClass} ${ringClass}`}
      title={tabTitle}
    >
      {/* Status dot — analyzing > liveAnalyzing > watching > error precedence */}
      {tab.status === 'analyzing' ? (
        <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4] animate-pulse flex-shrink-0" />
      ) : tab.liveAnalyzing ? (
        <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4] animate-pulse flex-shrink-0" title="Analyzing imported photos" />
      ) : tab.watchLive ? (
        <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4] animate-pulse flex-shrink-0" title="Watching folder live" />
      ) : tab.status === 'error' ? (
        <span className="w-1.5 h-1.5 rounded-full bg-[#C97B7B] flex-shrink-0" />
      ) : null}

      <span className="truncate">{tabLabel(tab)}</span>

      {/* Live-analysis chip — shows while the watcher is still analyzing the
          imported photos (the /watch path, e.g. Provenance's Import & Analyze).
          Stays up across the JPG→RAF gap until the folder is truly done. */}
      {tab.liveAnalyzing && (
        <span className="px-1.5 py-0.5 rounded text-[12px] flex-shrink-0 bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] whitespace-nowrap">
          analyzing…{tab.liveLeft ? ` ${tab.liveLeft}` : ''}
        </span>
      )}

      {/* Image count chip */}
      {tab.images.length > 0 && (
        <span className={`px-1.5 py-0.5 rounded text-[14px] font-mono flex-shrink-0 ${
          isActive ? 'bg-[rgba(255,255,255,0.06)] text-[#f9f9f9]' : 'bg-[#1b1c1e] text-[#9c9c9d]'
        }`}>
          {tab.images.length}
        </span>
      )}

      {/* Close button — visible on hover or when active. Hidden during analysis. */}
      {tab.status !== 'analyzing' && (
        <button
          onClick={(e) => { e.stopPropagation(); onClose(tab.id) }}
          className={`w-6 h-6 rounded flex items-center justify-center flex-shrink-0 ${
            isActive ? 'opacity-60' : 'opacity-0 group-hover:opacity-60'
          } hover:opacity-100 hover:bg-[rgba(255,255,255,0.06)] text-[#9c9c9d] transition-opacity`}
          title="Close tab"
          aria-label="Close tab"
        >
          <X size={14} strokeWidth={2.25} />
        </button>
      )}
    </div>
  )
}

// TabBar — slots inline into the app bar row as a flex-1 child.
// Closable tabs scroll horizontally; the new-analysis button sits
// inline at the end rather than pinned to the far right.

// Two-item source picker hung off the "+" button.
//
// Closes on a document mousedown rather than a `fixed inset-0` backdrop: the
// tab bar sits inside a transformed ancestor, where a fixed backdrop is
// positioned against that ancestor instead of the viewport and stops
// receiving the clicks it is supposed to catch. The data-dropdown marker is
// how the listener recognises its own trigger and menu.
function SourceMenu({ onClose, onFolder, onPhotos }) {
  useEffect(() => {
    const onDown = (e) => {
      if (e.target.closest('[data-dropdown="true"]')) return
      onClose()
    }
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return (
    <div
      data-dropdown="true"
      className="absolute left-0 top-full mt-1 z-50 w-44 bg-[#0d0e10] border border-[rgba(255,255,255,0.10)] rounded-lg py-1 shadow-xl"
    >
      <button
        onClick={onFolder}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-[#151618] transition-colors"
      >
        <Folder size={14} className="text-[#8a8b8c]" />
        <span className="text-xs text-[#f0f0f0]">Folder…</span>
      </button>
      <button
        onClick={onPhotos}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-[#151618] transition-colors"
      >
        <Images size={14} className="text-[#8a8b8c]" />
        <span className="text-xs text-[#f0f0f0]">Photos album…</span>
      </button>
    </div>
  )
}

export function TabBar({ tabs, activeTabId, onSelect, onClose, onReorder, onTrailingClick, onTrailingPhotosClick }) {
  const [dragId, setDragId] = useState(null)
  // Source menu on '+'. Only shown when a Photos handler is wired, so the
  // button keeps its original one-click behaviour everywhere else.
  const [sourceMenuOpen, setSourceMenuOpen] = useState(false)
  const [dragOverId, setDragOverId] = useState(null)
  const trailingTab  = tabs[tabs.length - 1]
  const closableTabs = tabs.slice(0, -1)
  const watchingTab  = tabs.find(t => t.watchLive)
  const watchingTabName = watchingTab ? tabLabel(watchingTab) : null

  // Roving tabindex: one Tab stop for the whole list. The "new analysis" page
  // is the trailing tab, so when it is the active one no closable tab is
  // selected and the first tab takes the stop instead.
  const focusStopId = closableTabs.some(t => t.id === activeTabId)
    ? activeTabId
    : closableTabs[0]?.id
  const onTabListKeyDown = (e) => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    const edge = e.key === 'Home' ? 0 : e.key === 'End' ? closableTabs.length - 1 : null
    if (!step && edge === null) return
    const i = closableTabs.findIndex(t => `tab-${t.id}` === document.activeElement?.id)
    if (i < 0) return
    e.preventDefault()
    const next = edge !== null
      ? closableTabs[edge]
      : closableTabs[(i + step + closableTabs.length) % closableTabs.length]
    if (next) document.getElementById(`tab-${next.id}`)?.focus()
  }

  const buildDragHandlers = (tab) => ({
    onDragStart: (e) => {
      if (tab.status === 'analyzing') { e.preventDefault(); return }
      setDragId(tab.id)
      try { e.dataTransfer.effectAllowed = 'move' } catch { /* not all browsers support this */ }
    },
    onDragOver: (e) => {
      if (!dragId || dragId === tab.id) return
      e.preventDefault()
      setDragOverId(tab.id)
    },
    onDragLeave: () => {
      if (dragOverId === tab.id) setDragOverId(null)
    },
    onDrop: (e) => {
      e.preventDefault()
      if (dragId && dragId !== tab.id) onReorder(dragId, tab.id)
      setDragId(null); setDragOverId(null)
    },
    onDragEnd: () => { setDragId(null); setDragOverId(null) },
  })

  return (
    <div className="flex items-center gap-1 flex-1 min-w-0">
      {/* New analysis — small icon button to the left of the tab pills */}
      {trailingTab && (
        <div className="relative flex-shrink-0 mr-1">
          <button
            onClick={() => {
              if (onTrailingPhotosClick) { setSourceMenuOpen(o => !o); return }
              return onTrailingClick ? onTrailingClick(trailingTab.id) : onSelect(trailingTab.id)
            }}
            className="new-analysis-btn w-8 h-8 rounded-[6px] flex items-center justify-center"
            title="Start a new analysis"
            aria-label="Start a new analysis"
            data-dropdown="true"
          >
            <Plus size={16} strokeWidth={2} />
          </button>

          {sourceMenuOpen && (
            <SourceMenu
              onClose={() => setSourceMenuOpen(false)}
              onFolder={() => {
                setSourceMenuOpen(false)
                return onTrailingClick ? onTrailingClick(trailingTab.id) : onSelect(trailingTab.id)
              }}
              onPhotos={() => {
                setSourceMenuOpen(false)
                onTrailingPhotosClick(trailingTab.id)
              }}
            />
          )}
        </div>
      )}

      {/* Scrollable tab pills */}
      <div
        role="tablist"
        aria-label="Analyses"
        onKeyDown={onTabListKeyDown}
        className="flex items-center gap-0.5 overflow-x-auto flex-1 min-w-0"
        style={{ scrollbarWidth: 'none' }}
      >
        {closableTabs.map(tab => (
          <TabItem
            key={tab.id}
            tab={tab}
            isActive={tab.id === activeTabId}
            isFocusStop={tab.id === focusStopId}
            isDragOver={dragOverId === tab.id && dragId && dragId !== tab.id}
            dragHandlers={buildDragHandlers(tab)}
            onSelect={onSelect}
            onClose={onClose}
            watchingTabName={tab.watchLive ? null : watchingTabName}
          />
        ))}
      </div>
    </div>
  )
}
