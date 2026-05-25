// Filmstrip — horizontal thumbnail strip for the GroupView and CullView's
// "compare against neighbours" affordance. Each thumb is wrapped in a 44pt
// tap target even though the visible chip is 56×56pt (SC 2.5.8).

import { useEffect, useRef } from 'react'
import { API } from '../../api'
import { Star } from 'lucide-react'

export function Filmstrip({ images, currentId, aiPickId, onSelect }) {
  const stripRef = useRef(null)

  // Scroll the current item into view when it changes.
  useEffect(() => {
    const el = stripRef.current?.querySelector(`[data-id="${currentId}"]`)
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
    }
  }, [currentId])

  if (!images?.length) return null

  return (
    <div
      ref={stripRef}
      className="m-no-scrollbar flex items-center overflow-x-auto px-3 py-2 gap-1"
      role="listbox"
      aria-label="Photos in this group"
    >
      {images.map((img, i) => {
        const isCurrent = img.id === currentId
        const isPick = aiPickId != null && img.id === aiPickId
        return (
          <button
            key={img.id}
            type="button"
            data-id={img.id}
            role="option"
            aria-selected={isCurrent}
            aria-label={`Photo ${i + 1}${isPick ? ' (AI pick)' : ''}${img.decision ? ` — ${img.decision}` : ''}`}
            onClick={() => onSelect(img.id)}
            className="m-filmstrip-thumb-tap"
          >
            <span
              className="m-filmstrip-thumb"
              data-current={isCurrent}
              data-pick={isPick}
            >
              <img src={`${API}/previews/${img.id}`} alt="" loading="lazy" />
              {img.decision && (
                <span
                  className={`absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-sm flex items-center justify-center text-[8px] font-bold ${
                    img.decision === 'keep'   ? 'bg-[rgba(125,184,154,0.85)] text-[#07080a]' :
                    img.decision === 'maybe'  ? 'bg-[rgba(232,184,74,0.85)]  text-[#07080a]' :
                                                'bg-[rgba(201,123,123,0.85)] text-[#07080a]'
                  }`}
                  aria-hidden="true"
                >
                  {img.decision === 'keep' ? 'K' : img.decision === 'maybe' ? 'M' : 'R'}
                </span>
              )}
              {isPick && (
                <span
                  className="absolute bottom-0.5 left-0.5 w-3.5 h-3.5 rounded-sm bg-[rgba(232,184,74,0.92)] flex items-center justify-center"
                  aria-hidden="true"
                >
                  <Star size={9} fill="#07080a" stroke="#07080a" />
                </span>
              )}
            </span>
          </button>
        )
      })}
    </div>
  )
}
