// SignalStrip — always-visible single-row score summary between the photo
// and the decision bar. Shows the user the minimum viable signals needed
// to decide; everything else lives in the detail sheet.
//
// Layout (from left):
//   [overall bar with number] [contextual chip] [personal-score divergence dot]
//
// Contextual chip rule: if face detected, show face-quality chip; otherwise
// aesthetic chip. Mirrors the desktop ImageCard hierarchy.

import { ScoreBar } from '../../ui/primitives'
import { faceQualityScore, aestheticLabel, iqaLabel } from '../../ui/format'
import { MobileInfo } from './MobileInfo'

function bandTint(score) {
  if (score == null) return 'bg-white/10 text-[#9c9c9d] border-white/10'
  if (score >= 75) return 'bg-[rgba(125,184,154,0.18)] text-[#9DD0B5] border-[rgba(125,184,154,0.45)]'
  if (score >= 55) return 'bg-[rgba(91,184,212,0.18)] text-[#7FCEE3] border-[rgba(91,184,212,0.45)]'
  if (score >= 35) return 'bg-[rgba(232,184,74,0.18)] text-[#F0CD7A] border-[rgba(232,184,74,0.45)]'
  return 'bg-[rgba(201,123,123,0.18)] text-[#E8A0A0] border-[rgba(201,123,123,0.45)]'
}

export function SignalStrip({ image, onOpenDetail }) {
  if (!image) return null
  const overall = image.overall_score
  const personal = image.personal_score
  const aesthetic = image.aesthetic_score
  const fq = faceQualityScore(image)
  const showPersonalDivergence =
    personal != null && overall != null && Math.abs(personal - overall) > 15

  // Contextual chip: face if present, otherwise aesthetic.
  const ctx = image.face_detected
    ? { kind: 'face',     value: fq,         label: 'Face' }
    : { kind: 'aesthetic', value: aesthetic,  label: 'Aesthetic' }

  return (
    <div
      className="px-4 py-2 flex items-center gap-3"
      role="group"
      aria-label="Photo quality signals"
    >
      <button
        type="button"
        onClick={onOpenDetail}
        className="flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] rounded-md py-1"
        aria-label={`Overall score ${overall != null ? Math.round(overall) : 'not analyzed'}. Tap for details.`}
      >
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[12px] font-medium text-[#cecece] uppercase tracking-wide">Overall</span>
          {overall != null && (
            <span className="ml-auto text-[15px] font-mono font-semibold text-[#f9f9f9] m-tabular">
              {Math.round(overall)}
            </span>
          )}
        </div>
        <div className="flex-1">
          <ScoreBar value={overall ?? null} color="bg-[#cecece]" />
        </div>
      </button>

      <div className="flex items-center gap-2">
        {ctx.value != null ? (
          <MobileInfo
            content={
              <div>
                <p className="font-semibold text-[14px] mb-1">{ctx.kind === 'face' ? 'Face quality' : 'Aesthetic'}</p>
                <p className="text-[13px] text-[#cecece] leading-snug">
                  {ctx.kind === 'face'
                    ? 'Composite of face sharpness, eye openness, and face size. The lower the eyes are open, the more the score is penalised.'
                    : `TOPIQ-IAA aesthetic (AVA-trained) — ${aestheticLabel(aesthetic) || '—'}.`}
                </p>
              </div>
            }
          >
            <span className={`inline-flex items-center gap-1 px-2 h-8 rounded-full border text-[12px] font-medium ${bandTint(ctx.value)}`}>
              <span className="opacity-80">{ctx.label}</span>
              <span className="font-mono font-semibold m-tabular">{Math.round(ctx.value)}</span>
            </span>
          </MobileInfo>
        ) : null}

        {image.iqa_score != null && (
          <MobileInfo
            content={
              <div>
                <p className="font-semibold text-[14px] mb-1">Perceptual quality</p>
                <p className="text-[13px] text-[#cecece] leading-snug">
                  TOPIQ no-reference IQA — {iqaLabel(image.iqa_score) || 'unknown'}. Predicts how a viewer rates technical quality.
                </p>
              </div>
            }
          >
            <span className={`inline-flex items-center gap-1 px-2 h-8 rounded-full border text-[12px] font-medium ${bandTint(image.iqa_score)}`}>
              <span className="opacity-80">IQA</span>
              <span className="font-mono font-semibold m-tabular">{Math.round(image.iqa_score)}</span>
            </span>
          </MobileInfo>
        )}

        {showPersonalDivergence && (
          <MobileInfo
            label="Personal score divergence"
            content={
              <div>
                <p className="font-semibold text-[14px] mb-1">Your taste model</p>
                <p className="text-[13px] text-[#cecece] leading-snug">
                  Personal score {Math.round(personal)} ({personal > overall ? '+' : ''}{Math.round(personal - overall)} vs. overall).
                  This photo {personal > overall ? 'fits your eye' : 'is below your eye'} based on past decisions.
                </p>
              </div>
            }
          >
            <span
              className="inline-flex items-center justify-center w-8 h-8 rounded-full"
              style={{ background: personal > overall ? 'rgba(123,130,201,0.55)' : 'rgba(123,130,201,0.20)' }}
            >
              <span className="text-[10px] font-bold text-white m-tabular">
                {personal > overall ? '+' : ''}{Math.round(personal - overall)}
              </span>
            </span>
          </MobileInfo>
        )}
      </div>
    </div>
  )
}
