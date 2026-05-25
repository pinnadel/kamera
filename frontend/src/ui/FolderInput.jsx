import { useState } from 'react'
import { Folder } from 'lucide-react'
import { API } from '../api'

// FolderInput — text input that opens a native OS folder picker on click.
// The backend uses tkinter, which shows the native folder dialog on every platform.
export function FolderInput({ value, onChange, placeholder, className, defaultHint, placeholderClassName = 'placeholder-[#6a6b6c]', prompt }) {
  const [picking, setPicking] = useState(false)

  async function openPicker() {
    if (picking) return
    setPicking(true)
    try {
      const res = await fetch(`${API}/pick-folder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_path: value || '', prompt: prompt || '' }),
      })
      const data = await res.json()
      // Strip trailing slashes for consistency with backend Path() normalization.
      if (data.path) onChange(data.path.replace(/\/+$/, ''))
    } catch (e) {
      console.error('Folder picker failed:', e)
    } finally {
      setPicking(false)
    }
  }

  // When no custom value is set and a default-hint is provided, render a
  // descriptive button instead of an empty path field. This avoids the
  // confusing pattern of a placeholder path that isn't the actual default.
  if (!value && defaultHint) {
    return (
      <button
        type="button"
        onClick={openPicker}
        className={`inline-flex items-center gap-2 text-left cursor-pointer bg-[#07080a] border border-dashed border-[rgba(255,255,255,0.10)] rounded-lg h-9 px-3 text-[13px] text-[#6a6b6c] hover:text-[#9c9c9d] hover:border-[rgba(255,255,255,0.18)] focus:outline-none focus:shadow-[0_0_0_3px_rgba(91,184,212,0.18)] transition-colors ${className}`}
        title="Click to choose a custom folder"
      >
        <Folder size={16} className="flex-shrink-0" />
        {picking ? 'Opening folder picker…' : defaultHint}
      </button>
    )
  }

  return (
    <input
      type="text"
      value={value}
      readOnly
      onClick={openPicker}
      onChange={() => {}}
      placeholder={picking ? 'Opening folder picker…' : placeholder}
      className={`cursor-pointer bg-[#07080a] border border-[rgba(255,255,255,0.08)] rounded-lg h-9 px-3 font-mono text-[13px] text-[#f9f9f9] ${placeholderClassName} focus:outline-none focus:border-[rgba(255,255,255,0.18)] focus:shadow-[0_0_0_3px_rgba(91,184,212,0.18)] transition-shadow ${className}`}
      title="Click to choose a folder"
    />
  )
}
