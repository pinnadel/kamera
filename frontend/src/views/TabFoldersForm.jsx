import { useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { API } from '../api'
import { FolderInput } from '../ui/FolderInput'

// Per-tab destination folders for K / M / R. The Tab settings popover provides
// the section header + description; this component renders only the rows
// (and a "Reset all" link when at least one row is customised).

const ROWS = [
  { key: 'keeps_folder',  decision: 'keep',   label: 'Keep',   borderColor: 'bg-[#7DB89A]', defaultSubfolder: '_Keeps'  },
  { key: 'maybes_folder', decision: 'maybe',  label: 'Maybe',  borderColor: 'bg-[#E8B84A]', defaultSubfolder: '_Maybes' },
  { key: 'trash_folder',  decision: 'reject', label: 'Reject', borderColor: 'bg-[#C97B7B]', defaultSubfolder: '_Trash'  },
]

export function TabFoldersForm({ sourceFolder, onToast, autoLoad = true }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  useEffect(() => {
    if (!autoLoad || !sourceFolder) return
    let cancelled = false
    setLoading(true); setError(null)
    fetch(`${API}/folder-settings?source_folder=${encodeURIComponent(sourceFolder)}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`Server ${r.status}`)))
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [autoLoad, sourceFolder])

  async function resetAll() {
    if (!sourceFolder || !data) return
    const fields = ROWS
      .map(r => r.key)
      .filter(k => !data[`${k.replace('_folder', '')}_is_default`])
    if (fields.length === 0) return
    setData(d => d ? {
      ...d,
      ...Object.fromEntries(fields.flatMap(k => [
        [k, `${sourceFolder}/${ROWS.find(r => r.key === k).defaultSubfolder}`],
        [`${k.replace('_folder', '')}_is_default`, true],
      ])),
    } : d)
    try {
      await Promise.all(fields.map(k =>
        fetch(`${API}/folder-settings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_folder: sourceFolder, [k]: null }),
        }).then(r => r.ok ? null : Promise.reject(new Error(`Server ${r.status}`)))
      ))
      const res = await fetch(`${API}/folder-settings?source_folder=${encodeURIComponent(sourceFolder)}`)
      if (res.ok) setData(await res.json())
    } catch (e) {
      onToast?.({ type: 'error', message: `Could not reset folders — ${e.message}` })
      try {
        const res = await fetch(`${API}/folder-settings?source_folder=${encodeURIComponent(sourceFolder)}`)
        if (res.ok) setData(await res.json())
      } catch { /* ignore */ }
    }
  }

  async function persist(field, decision, value) {
    if (!sourceFolder) return
    const next = (value || '').trim()
    setData(d => d ? {
      ...d,
      [field]: next || `${sourceFolder}/${ROWS.find(r => r.key === field).defaultSubfolder}`,
      [`${field.replace('_folder', '')}_is_default`]: !next,
    } : d)
    try {
      const res = await fetch(`${API}/folder-settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_folder: sourceFolder, [field]: next || null }),
      })
      if (!res.ok) throw new Error(`Server ${res.status}`)
      const d = await res.json()
      setData(d)
    } catch (e) {
      onToast?.({ type: 'error', message: `Could not save folder — ${e.message}` })
      try {
        const res = await fetch(`${API}/folder-settings?source_folder=${encodeURIComponent(sourceFolder)}`)
        if (res.ok) setData(await res.json())
      } catch { /* ignore */ }
    }
  }

  if (error) {
    return <p className="text-xs text-[#C97B7B]">Couldn't load folders — {error}</p>
  }
  if (loading && !data) {
    return <p className="text-xs text-[#6a6b6c]">Loading…</p>
  }
  if (!data) return null

  const customCount = [data.keeps_is_default, data.maybes_is_default, data.trash_is_default].filter(d => !d).length

  return (
    <div>
      <div className="space-y-2">
        {ROWS.map(r => {
          const isDefault = data[`${r.key.replace('_folder', '')}_is_default`]
          const value = isDefault ? '' : data[r.key]
          return (
            <div key={r.key} className="flex items-center gap-2">
              <span className={`w-0.5 h-5 rounded-full shrink-0 ${r.borderColor}`} />
              <span className="text-xs text-[#cecece] w-[52px] shrink-0">{r.label}</span>
              <FolderInput
                value={value}
                onChange={v => persist(r.key, r.decision, v)}
                defaultHint={`${r.defaultSubfolder}/ inside this folder`}
                className="flex-1 min-w-0"
              />
              {!isDefault && (
                <button
                  onClick={() => persist(r.key, r.decision, '')}
                  className="text-[#9c9c9d] hover:text-[#cecece] w-6 h-6 rounded inline-flex items-center justify-center shrink-0 transition-colors"
                  title="Reset to default subfolder"
                  aria-label="Reset to default subfolder"
                >
                  <RotateCcw size={14} />
                </button>
              )}
            </div>
          )
        })}
      </div>

      {customCount > 0 && (
        <div className="mt-2 text-right">
          <button
            onClick={resetAll}
            className="inline-flex items-center gap-1 text-[11px] text-[#9c9c9d] hover:text-[#cecece] transition-colors"
            title="Reset every custom folder back to the default subfolder"
          >
            <RotateCcw size={13} /> Reset all to default
          </button>
        </div>
      )}
    </div>
  )
}
