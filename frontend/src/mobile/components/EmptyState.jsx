// EmptyState — full-bleed empty placeholder. Used when no folder is selected
// or the current folder has no analyzed photos.

import { ImageIcon } from 'lucide-react'

export function EmptyState({ icon: Icon = ImageIcon, title, body, action }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
      <span
        className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[rgba(91,184,212,0.10)] mb-4"
        aria-hidden="true"
      >
        <Icon size={28} className="text-[#5BB8D4]" />
      </span>
      <h2 className="m-h2 mb-2">{title}</h2>
      {body && <p className="m-body text-[#9c9c9d] max-w-xs">{body}</p>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  )
}
