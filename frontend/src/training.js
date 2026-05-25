// Training Mode helpers (pure, no side-effects)

const _shuffle = arr => [...arr].sort(() => Math.random() - 0.5)
const _randInt  = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min

// Active learning: surface photos the model is most uncertain about first so
// the user's feedback has the highest signal value. Uncertainty is the
// minimum distance from personal_score to either decision boundary.
// Only kicks in when personal_score is populated (model is ready).
const _uncertainty = (img, keepThreshold, maybeThreshold) => {
  if (img.personal_score == null) return Infinity
  return Math.min(
    Math.abs(img.personal_score - keepThreshold),
    Math.abs(img.personal_score - maybeThreshold)
  )
}

export function buildTrainingQueue(images, { keepThreshold = 70, maybeThreshold = 45 } = {}) {
  const allUndecided = images.filter(img => img.analysis_status === 'done' && !img.decision)
  const hasScores    = allUndecided.some(img => img.personal_score != null)
  const undecided    = hasScores
    ? [...allUndecided].sort((a, b) =>
        _uncertainty(a, keepThreshold, maybeThreshold) - _uncertainty(b, keepThreshold, maybeThreshold)
      )
    : _shuffle(allUndecided)
  // Re-show pool: decided photos with a score, capped at 30% of queue length
  const decided    = _shuffle(images.filter(img => img.decision && img.overall_score != null))
  const reshowPool = decided.slice(0, Math.floor(undecided.length * 0.3))

  const queue = []
  let ri = 0, nextAt = _randInt(5, 25)

  undecided.forEach((img, i) => {
    queue.push({ image: img, isReshow: false })
    if (i + 1 === nextAt && ri < reshowPool.length) {
      queue.push({ image: reshowPool[ri++], isReshow: true })
      nextAt += _randInt(5, 25)
    }
  })
  return queue
}
