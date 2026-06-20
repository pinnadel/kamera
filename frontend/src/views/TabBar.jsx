import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { tabLabel } from '../tabs'

function TabItem({ tab, isActive, isDragOver, dragHandlers, onSelect, onClose, watchingTabName }) {
  const ringClass = isDragOver ? 'ring-1 ring-[#5BB8D4] ring-inset' : ''

  const baseClass = isActive
    ? 'bg-[#1a1b1d] text-[#f9f9f9]'
    : 'text-[#6a6b6c] hover:text-[#9c9c9d] hover:bg-[rgba(255,255,255,0.03)]'

  const baseTitle = tab.folderPath || 'New analysis'
  const tabTitle = tab.watchLive
    ? `${baseTitle}\nWatch live: on`
    : watchingTabName
      ? `${baseTitle}\nWatch live: on "${watchingTabName}"`
      : baseTitle

  return (
    <div
      draggable={tab.status !== 'analyzing'}
      {...(dragHandlers || {})}
      onClick={() => onSelect(tab.id)}
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
export function TabBar({ tabs, activeTabId, onSelect, onClose, onReorder, onTrailingClick }) {
  const [dragId, setDragId] = useState(null)
  const [dragOverId, setDragOverId] = useState(null)
  const trailingTab  = tabs[tabs.length - 1]
  const closableTabs = tabs.slice(0, -1)
  const watchingTab  = tabs.find(t => t.watchLive)
  const watchingTabName = watchingTab ? tabLabel(watchingTab) : null

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
        <button
          onClick={() => (onTrailingClick ? onTrailingClick(trailingTab.id) : onSelect(trailingTab.id))}
          className="new-analysis-btn w-8 h-8 flex-shrink-0 rounded-[6px] flex items-center justify-center mr-1"
          title="Start a new analysis"
          aria-label="Start a new analysis"
        >
          <Plus size={16} strokeWidth={2} />
        </button>
      )}

      {/* Scrollable tab pills */}
      <div
        className="flex items-center gap-0.5 overflow-x-auto flex-1 min-w-0"
        style={{ scrollbarWidth: 'none' }}
      >
        {closableTabs.map(tab => (
          <TabItem
            key={tab.id}
            tab={tab}
            isActive={tab.id === activeTabId}
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
