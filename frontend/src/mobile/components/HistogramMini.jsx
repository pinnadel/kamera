// HistogramMini — luminance-only histogram for the mobile detail sheet.
// Loads `/histogram/{id}` lazily on mount and renders a single curve.
// Per-channel RGB overlay omitted on mobile to keep the read fast and
// readable at small sizes.

import { useEffect, useState } from 'react'
import { API } from '../../api'

export function HistogramMini({ imageId }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    let cancelled = false
    setData(null)
    if (!imageId) return
    fetch(`${API}/histogram/${imageId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => { if (!cancelled) setData(null) })
    return () => { cancelled = true }
  }, [imageId])

  if (!data?.luminance) {
    return <div className="m-histogram opacity-50" aria-label="Histogram loading" />
  }

  const lum = data.luminance
  const max = Math.max(1, ...lum)
  // Normalize to 0–60 (canvas height). Build an SVG path of 256 points.
  const points = lum
    .map((v, i) => `${(i / 255) * 100},${64 - (v / max) * 60}`)
    .join(' ')

  const clip = data.any_clip_pct ?? data.any_channel_clip_pct
  return (
    <div className="m-histogram relative" role="img" aria-label={`Luminance histogram${clip != null ? `, ${clip.toFixed(1)}% clipped` : ''}`}>
      <svg viewBox="0 0 100 64" preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        <polygon points={`0,64 ${points} 100,64`} fill="rgba(255,255,255,0.18)" />
        <polyline points={points} fill="none" stroke="rgba(255,255,255,0.85)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      </svg>
      {clip != null && (
        <span className="absolute top-1.5 right-2 text-[10px] font-mono text-[#9c9c9d] m-tabular">
          {clip.toFixed(1)}% clip
        </span>
      )}
    </div>
  )
}
