// DetailSheet — body content for the BottomSheet on the cull screen.
// Aggregates everything that lives behind hover popovers / collapsible
// sections on desktop into a single touch-scrollable sheet body.
//
// Sections (top to bottom):
//   1. Score breakdown — Overall, Sharpness, Exposure, IQA, Aesthetic, Face quality
//   2. Personal score with top-3 influencer attribution
//   3. Histogram (luminance) + clipping toggles
//   4. EXIF
//   5. AI explanation (lazy-load on first view)
//   6. Tools — open in fullscreen, zoom, copy filename

import { useEffect, useState } from 'react'
import {
  ChevronRight, Camera, Aperture, Timer, Sun, Hash, MapPin,
  Sparkles, AlertTriangle, Maximize2,
} from 'lucide-react'
import { API } from '../../api'
import { ScoreBar } from '../../ui/primitives'
import {
  formatShutter, faceQualityScore, aestheticLabel, iqaLabel,
} from '../../ui/format'
import { HistogramMini } from '../components/HistogramMini'
import { MobileInfo } from '../components/MobileInfo'

function ScoreRow({ label, value, color = 'bg-white/85', tooltip }) {
  return (
    <div className="flex items-center gap-3">
      {tooltip ? (
        <MobileInfo content={tooltip}>
          <span className="text-[14px] text-[#cecece] font-medium w-32">{label}</span>
        </MobileInfo>
      ) : (
        <span className="text-[14px] text-[#cecece] font-medium w-32">{label}</span>
      )}
      <div className="flex-1">
        <ScoreBar value={value ?? null} color={color} />
      </div>
    </div>
  )
}

function ExifRow({ Icon, label, value }) {
  if (value == null || value === '') return null
  return (
    <div className="flex items-center gap-3 py-2 border-b border-white/5 last:border-0">
      <Icon size={16} className="text-[#9c9c9d]" aria-hidden="true" />
      <span className="text-[13px] text-[#9c9c9d] w-28">{label}</span>
      <span className="text-[14px] text-[#f9f9f9] font-mono m-tabular">{value}</span>
    </div>
  )
}

export function DetailSheet({ image, onOpenFullscreen }) {
  const [explanation, setExplanation] = useState(null)
  const [explainLoading, setExplainLoading] = useState(false)
  const [explainError, setExplainError] = useState(null)

  // Lazy-fetch existing explanation. Generation is opt-in (button below).
  useEffect(() => {
    if (!image?.id) return
    let cancelled = false
    setExplanation(null)
    setExplainError(null)
    fetch(`${API}/explanation/${image.id}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled && d?.text) setExplanation(d.text) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [image?.id])

  if (!image) return null

  const personal = image.personal_score
  const overall  = image.overall_score
  const personalDelta = (personal != null && overall != null) ? personal - overall : null
  const fq = faceQualityScore(image)

  const generateExplanation = async () => {
    setExplainLoading(true)
    setExplainError(null)
    try {
      const res = await fetch(`${API}/generate-explanation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: image.id }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `Status ${res.status}`)
      setExplanation(body.text || body.explanation || '')
    } catch (err) {
      setExplainError(err.message)
    } finally {
      setExplainLoading(false)
    }
  }

  return (
    <div className="px-5 pb-6">
      {/* Title row */}
      <div className="flex items-start gap-3 mb-4">
        <div className="flex-1 min-w-0">
          <h3 className="m-h2 truncate">{image.filename}</h3>
          {image.shot_at && (
            <p className="text-[13px] text-[#9c9c9d] mt-0.5">
              {new Date(image.shot_at).toLocaleString()}
            </p>
          )}
        </div>
        {onOpenFullscreen && (
          <button
            type="button"
            onClick={onOpenFullscreen}
            className="m-btn m-btn-ghost shrink-0"
            aria-label="Open photo fullscreen"
          >
            <Maximize2 size={16} aria-hidden="true" />
            View
          </button>
        )}
      </div>

      {/* 1. Score breakdown */}
      <Section title="Quality scores">
        <div className="space-y-3">
          <ScoreRow
            label="Overall"
            value={overall}
            tooltip={
              <p>Weighted blend of Sharpness ({Math.round((image.sharpness_score ?? 0))}) and Exposure ({Math.round((image.exposure_score ?? 0))}). Sharpness is non-recoverable so it carries more weight.</p>
            }
          />
          <ScoreRow label="Sharpness" value={image.sharpness_score}
            tooltip={<p>Per-tile p90 fusion of Laplacian, Tenengrad and Sobel measures. Detects camera shake and missed focus.</p>} />
          <ScoreRow label="Exposure" value={image.exposure_score}
            tooltip={<p>0 = blown / black, 100 = balanced midtones. Highlight clip {image.highlight_clip_pct?.toFixed?.(1) ?? '—'}% · Shadow clip {image.shadow_clip_pct?.toFixed?.(1) ?? '—'}%.</p>} />
          {image.iqa_score != null && (
            <ScoreRow label="Perceptual" value={image.iqa_score}
              tooltip={<p>TOPIQ no-reference IQA — predicts how a viewer rates technical quality. Band: <strong>{iqaLabel(image.iqa_score)}</strong>.</p>} />
          )}
          {image.aesthetic_score != null && (
            <ScoreRow label="Aesthetic" value={image.aesthetic_score}
              tooltip={<p>TOPIQ-IAA — AVA-trained aesthetic assessment of composition, lighting, subject matter. Band: <strong>{aestheticLabel(image.aesthetic_score)}</strong>.</p>} />
          )}
          {image.face_detected && fq != null && (
            <ScoreRow label="Face quality" value={fq}
              tooltip={<p>Composite of face sharpness, eye openness ({(image.eye_openness_ratio ?? 0).toFixed(2)}), and face size ratio ({(image.face_size_ratio ?? 0).toFixed(2)}). Closed eyes take a 35-point penalty.</p>} />
          )}
        </div>
      </Section>

      {/* 2. Personal score */}
      {personal != null && (
        <Section title="Your taste model">
          <div className="rounded-xl bg-[#101111] border border-[rgba(123,130,201,0.20)] p-3">
            <div className="flex items-center gap-3 mb-2">
              <Sparkles size={16} className="text-[#7B82C9]" aria-hidden="true" />
              <span className="text-[13px] text-[#cecece] flex-1">Personal score</span>
              <span className="text-[20px] font-mono font-semibold text-[#f9f9f9] m-tabular">
                {Math.round(personal)}
              </span>
              {personalDelta != null && Math.abs(personalDelta) > 1 && (
                <span
                  className="text-[13px] font-mono font-semibold m-tabular"
                  style={{ color: personalDelta > 0 ? '#9DD0B5' : '#E8A0A0' }}
                >
                  {personalDelta > 0 ? '+' : ''}{Math.round(personalDelta)}
                </span>
              )}
            </div>
            <ScoreBar value={personal} color="bg-[#7B82C9]" />
            <p className="text-[12px] text-[#9c9c9d] mt-2 leading-snug">
              {personalDelta == null
                ? 'Predicted score from your past decisions.'
                : Math.abs(personalDelta) <= 1
                  ? 'Your model agrees with the technical score.'
                  : personalDelta > 0
                    ? 'Your model rates this above the technical score — this fits your eye.'
                    : 'Your model rates this below the technical score — this is below your bar.'}
            </p>
          </div>
        </Section>
      )}

      {/* 3. Histogram */}
      <Section title="Histogram">
        <HistogramMini imageId={image.id} />
        {(image.highlight_clip_pct != null || image.shadow_clip_pct != null) && (
          <div className="flex items-center gap-3 mt-2 text-[12px] font-mono text-[#9c9c9d] m-tabular">
            <span>Highlights {image.highlight_clip_pct?.toFixed?.(1) ?? '—'}%</span>
            <span>Shadows {image.shadow_clip_pct?.toFixed?.(1) ?? '—'}%</span>
          </div>
        )}
      </Section>

      {/* 4. EXIF */}
      <Section title="Camera">
        <div>
          <ExifRow Icon={Camera}   label="Camera"        value={image.camera_make ? `${image.camera_make} ${image.camera_model || ''}`.trim() : null} />
          <ExifRow Icon={Aperture} label="Aperture"      value={image.aperture != null ? `f/${image.aperture}` : null} />
          <ExifRow Icon={Timer}    label="Shutter"       value={formatShutter(image.shutter_speed)} />
          <ExifRow Icon={Sun}      label="ISO"           value={image.iso} />
          <ExifRow Icon={Hash}     label="Focal length"  value={image.focal_length_mm != null ? `${image.focal_length_mm}mm` : null} />
          <ExifRow Icon={MapPin}   label="Lens"          value={image.lens_model} />
        </div>
      </Section>

      {/* 5. AI explanation */}
      <Section title="AI explanation">
        {explanation ? (
          <p className="text-[14px] text-[#cecece] leading-relaxed whitespace-pre-wrap">{explanation}</p>
        ) : explainLoading ? (
          <p className="text-[13px] text-[#9c9c9d]">Generating…</p>
        ) : explainError ? (
          <div className="flex items-start gap-2 text-[13px] text-[#E8A0A0]">
            <AlertTriangle size={14} className="mt-0.5" aria-hidden="true" />
            <span>{explainError}</span>
          </div>
        ) : (
          <button
            type="button"
            onClick={generateExplanation}
            className="m-btn m-btn-ghost"
          >
            <Sparkles size={16} aria-hidden="true" />
            Generate explanation
          </button>
        )}
      </Section>

      {/* 6. Tools */}
      <Section title="Tools">
        <ToolRow
          label="Copy filename"
          onClick={() => navigator.clipboard?.writeText(image.filename || '')}
        />
        <ToolRow
          label="Copy file path"
          onClick={() => navigator.clipboard?.writeText(image.file_path || '')}
        />
      </Section>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <section className="mb-5">
      <h4 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] mb-2">{title}</h4>
      {children}
    </section>
  )
}

function ToolRow({ label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center w-full text-left h-12 px-2 rounded-xl hover:bg-white/5 focus-visible:outline-none focus-visible:bg-white/5"
    >
      <span className="flex-1 text-[14px] text-[#cecece]">{label}</span>
      <ChevronRight size={16} className="text-[#9c9c9d]" aria-hidden="true" />
    </button>
  )
}
