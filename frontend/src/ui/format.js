export function formatEta(seconds) {
  if (seconds == null || seconds < 1) return null
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

// Like formatEta but for completed durations (the analysis took N seconds).
// Accepts a float (backend rounds to one decimal) and returns "Xh Ym Zs",
// "Ym Zs", or "Zs". Always returns a string when given a non-null number,
// even for zero — completion banners always want SOMETHING to render.
export function formatDuration(seconds) {
  if (seconds == null) return null
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  if (total < 3600) {
    const m = Math.floor(total / 60)
    const s = total % 60
    return s === 0 ? `${m}m` : `${m}m ${s}s`
  }
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (s === 0 && m === 0) return `${h}h`
  if (s === 0) return `${h}h ${m}m`
  return `${h}h ${m}m ${s}s`
}

export function formatShutter(seconds) {
  if (seconds == null) return null
  if (seconds >= 1) return `${Math.round(seconds)}s`
  return `1/${Math.round(1 / seconds)}s`
}

// Mirror of phase2_quality/iqa_scorer.py thresholds (75/55/35).
// Backend stays the source of truth — frontend re-derives for display only.
export function iqaLabel(score) {
  if (score == null) return null
  if (score >= 75) return 'Excellent'
  if (score >= 55) return 'Good'
  if (score >= 35) return 'Fair'
  return 'Poor'
}

// Mirror of phase2_quality/aesthetic_scorer.py thresholds (46/42/36).
// TOPIQ-IAA's distribution is narrower than the previous CLIP+SAC head —
// cutoffs partition a survey distribution (n=40 cached previews) into
// top 15% / top 50% / top 85% / bottom 15%. Keep in sync with the matching
// aestheticTint() in DetailView.jsx.
export function aestheticLabel(score) {
  if (score == null) return null
  if (score >= 46) return 'Excellent'
  if (score >= 42) return 'Good'
  if (score >= 36) return 'Fair'
  return 'Poor'
}

// pickHeadlineScore — pick the score that should be shown as the headline
// (grid badges, filmstrip thumbnails, comparison rows). The personal
// model's score is computed on every image as soon as the model has
// trained once (training_size >= MIN_DECISIONS), but the score isn't
// considered TRUSTWORTHY until model_status === 'ready' (training_size >=
// 50 AND beats the baseline). Before then, surfacing personal_score
// would mislead the user — show technical overall instead. The
// in-DetailView Personal Scoring section uses the same gate.
export function pickHeadlineScore(image, modelInfo) {
  const modelReady = modelInfo?.model_status === 'ready'
  if (modelReady && image?.personal_score != null) return image.personal_score
  return image?.overall_score
}

// Composite face quality score (0–100). Display-only — never feeds the
// personal model or any backend scorer. Sharpness dominates; closed eyes
// take a steep flat penalty (near-rejection in portraits); face size adds
// a mild bonus so well-framed subjects edge out tiny faces.
//
// Note: SQLite stores booleans as 0/1 integers and the backend returns
// raw rows without coercion, so we use truthy checks (not `=== true`) for
// face_detected and `== 0` for eyes_open (which is null when no face).
export function faceQualityScore(image) {
  if (!image.face_detected) return null
  const sharp       = image.face_sharpness_score ?? 0
  const eyesPenalty = image.eyes_open != null && !image.eyes_open ? 35 : 0
  const sizeBonus   = image.face_size_ratio != null
    ? Math.min(10, image.face_size_ratio * 50)
    : 0
  return Math.max(0, Math.min(100, sharp - eyesPenalty + sizeBonus))
}
