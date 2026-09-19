// MoveIntoGroupModal — pick an existing group (manual or auto) to move the
// current multi-selection into. The chosen group's hero is the target_image_id
// passed to /set-manual-group with mode="join_group" — the backend resolves
// the rest. Closes on success, leaves selection in place on error (toast).

import { useMemo, useState } from 'react'
import { Search, X } from 'lucide-react'
import { API } from '../api'
import { useDialog } from '../hooks/useDialog'

export function MoveIntoGroupModal({ groups, selectionCount, onPick, onClose }) {
  const dialogRef = useDialog(onClose)
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return groups
    return groups.filter(g =>
      g.images.some(img => (img.filename || '').toLowerCase().includes(q))
    )
  }, [groups, query])

  return (
    <div
      className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Move into a group"
        tabIndex={-1}
        className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl shadow-2xl flex flex-col focus:outline-none"
        style={{ width: 'min(900px, 92vw)', maxHeight: '82vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[rgba(255,255,255,0.06)]">
          <div className="flex-1">
            <p className="text-sm font-semibold text-[#f0f0f0]">
              Move {selectionCount} photo{selectionCount === 1 ? '' : 's'} into a group
            </p>
            <p className="text-xs text-[#9c9c9d] mt-0.5">
              Click a group to add the selection
            </p>
          </div>
          <div className="relative">
            <Search
              size={13}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#8a8b8c] pointer-events-none"
            />
            <input
              type="text"
              autoFocus
              aria-label="Filter groups by filename"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by filename…"
              className="pl-7 pr-2 py-1 rounded text-xs bg-[#1a1b1d] border border-[rgba(255,255,255,0.08)] text-[#f0f0f0] placeholder-[#8a8b8c] focus:outline-none focus:border-[rgba(91,184,212,0.40)] w-56"
            />
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1 rounded text-[#9c9c9d] hover:bg-[rgba(255,255,255,0.05)]"
          >
            <X size={16} />
          </button>
        </div>

        {/* Group grid */}
        <div className="flex-1 overflow-y-auto p-4">
          {filtered.length === 0 ? (
            <p className="text-center text-xs text-[#8a8b8c] py-8">
              {groups.length === 0
                ? 'No groups exist yet. Use "New group from selection" instead.'
                : 'No groups match that filter.'}
            </p>
          ) : (
            <div
              className="grid gap-3"
              style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))' }}
            >
              {filtered.map((g) => (
                <button
                  key={g.best_image_id}
                  onClick={() => onPick(g)}
                  className="group flex flex-col items-stretch text-left rounded-lg border border-[rgba(255,255,255,0.06)] bg-[#0d0e0f] hover:border-[rgba(91,184,212,0.40)] hover:bg-[#13141a] transition-colors overflow-hidden"
                >
                  <div className="aspect-square bg-[#0a0b0c] relative">
                    <img
                      src={`${API}/previews/${g.best_image_id}`}
                      alt=""
                      className="absolute inset-0 w-full h-full object-cover"
                      draggable={false}
                    />
                    <div className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/70 text-[10px] text-[#f0f0f0] font-mono">
                      {g.size}
                    </div>
                  </div>
                  <div className="px-2 py-1.5 text-xs text-[#cecece] truncate">
                    {(g.images?.[0]?.filename || '').split('/').pop()}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
