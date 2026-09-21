// KeepVsReject — what separates the photos you kept from the ones you rejected.
//
// This used to be a numeric table: FEATURE · UNIT · KEPT · REJ. · Δ · ✓/✗.
// Every column needed prior knowledge to read. "80" didn't say it was a
// median, "Δ +47" restated the two numbers beside it, and the ✓/✗ legend sat
// in a footnote at the bottom of the card, far from any row.
//
// Worse, it was actively misleading for the 0/1 features. A median of a
// binary column is a majority vote, so "face present · kept 0.00 · rejected
// 1.00" read as "my keeps never have faces, my rejects always do" — when the
// real shares are 47% and 57%, i.e. almost no difference at all. Binary
// features now report shares (the backend's kept_mean / rejected_mean) and
// say so in words.
//
// Each row is a claim, then its evidence:
//
//   Your keeps are far sharper
//   Sharpness · 0–100    ──○──────────●──    33 → 80
//
// The dumbbell is the point: the two dots sit on the feature's own scale, so
// a real gap looks like a gap and a trivial one shows as two overlapping
// dots. That is why "blown highlights 0.07 vs 0.04" — which the old table
// flagged with a red ✗ as though it were a finding — now visibly reads as the
// noise it is.
//
// Ordering is by what's worth knowing, not declaration order: rows where the
// choice runs AGAINST the feature's own notion of better come first (that's
// the part a quality score can't express), then by effect size.

import { useState } from 'react'
import { CollapsibleSection } from '../../ui/CollapsibleSection'
import { Card, Empty, useGrow } from './primitives'

const LOW_SAMPLE_THRESHOLD = 5

// Features that exist in the vector but must never get their own row: a
// duplicate would read as independent corroboration of a pattern that was
// only measured once. They are skipped rather than falling into "Other".
const HIDDEN_FEATURES = new Set(['face_detected'])

// A row must move at least this much of its own scale to earn a sentence.
// Below it the row is "barely differs" and hides behind the section toggle.
const WEAK_EFFECT = 0.05

const KEEP_COLOR = '#7DB89A'
const REJECT_COLOR = '#C97B7B'

// Section membership. Single source of truth — adding a feature to the
// backend without updating this map drops it into the catch-all "Other"
// section so it can never silently disappear.
const FEATURE_GROUPS = [
  {
    id: 'technical',
    label: 'Technical',
    defaultOpen: true,
    features: [
      'sharpness_score',
      'exposure_score',
      'iqa_score',
      'highlight_clip_pct',
      'shadow_clip_pct',
      'shake_detected',
    ],
  },
  {
    id: 'face',
    label: 'People',
    defaultOpen: true,
    features: [
      'face_present',
      // 'face_detected' omitted on purpose — face_present is computed from it
      // (1.0 if face_detected else 0.0), so both rows printed the same share
      // under two names. See HIDDEN_FEATURES.
      'face_count',
      'face_sharpness_score',
      'face_size_ratio',
      'eye_openness_ratio',
      'eyes_open',
      'smile_score',
      'mouth_open_score',
    ],
  },
  {
    id: 'camera',
    label: 'Camera',
    defaultOpen: false,
    features: ['focal_length_mm', 'aperture', 'iso'],
  },
  {
    id: 'scene',
    label: 'Scene',
    defaultOpen: false,
    features: [
      'scene_is_portrait',
      'scene_is_landscape',
      'scene_is_street',
      'scene_is_night',
      'scene_is_macro',
      'scene_is_indoor',
      'scene_is_action',
      'scene_is_water',
    ],
  },
  {
    id: 'aesthetic',
    label: 'Aesthetic',
    defaultOpen: false,
    features: [
      'aesthetic_score',
      'subject_prominence_score',
      'background_distraction_score',
      'eye_contact_score',
      'decisive_moment_score',
    ],
  },
]

// Per-feature metadata.
//
//   label  — what a photographer calls it, not what the column is named
//   unit   — printed under the sentence so the numbers have a scale
//   range  — the feature's natural domain, used both to place the dots AND
//            to measure effect size. null = no natural bounds (ISO, mm,
//            counts), so the row scales to its own pair instead.
//   good   — 'higher' | 'lower' | 'context'. 'context' means there is no
//            universal better (a 35mm frame isn't better than a 50mm one),
//            so such a row can never be flagged unexpected.
//   kind   — 'binary' reports shares; everything else reports medians.
//   verb / up / down — the comparative clause, rendered as
//            "Your keeps {verb} {magnitude} {up|down}".
//   noun   — binary only: "{noun} appear in X% of keeps and Y% of rejects".
const FEATURE_META = {
  sharpness_score: {
    label: 'Sharpness', unit: '0–100', range: [0, 100], good: 'higher',
    verb: 'are', up: 'sharper', down: 'softer',
  },
  exposure_score: {
    label: 'Exposure', unit: '0–100', range: [0, 100], good: 'higher',
    verb: 'are', up: 'better exposed', down: 'worse exposed',
  },
  iqa_score: {
    label: 'Technical quality (TOPIQ)', unit: '0–100', range: [0, 100], good: 'higher',
    verb: 'are', up: 'technically cleaner', down: 'technically noisier',
  },
  highlight_clip_pct: {
    label: 'Blown highlights', unit: '% of frame', range: [0, 100], good: 'lower',
    verb: 'have', up: 'more blown highlights', down: 'fewer blown highlights',
  },
  shadow_clip_pct: {
    label: 'Crushed shadows', unit: '% of frame', range: [0, 100], good: 'lower',
    verb: 'have', up: 'more crushed shadows', down: 'fewer crushed shadows',
  },
  shake_detected: {
    label: 'Camera shake', kind: 'binary', noun: 'Camera shake', singular: true,
    range: [0, 1], good: 'lower',
  },
  face_present: {
    label: 'Any face', kind: 'binary', noun: 'Faces', range: [0, 1], good: 'context',
  },
  face_detected: {
    label: 'Face detected', kind: 'binary', noun: 'Detected faces', range: [0, 1], good: 'context',
  },
  face_count: {
    // Bounded deliberately: with range:null the span is max(kept, rejected),
    // so a median shift of 1 face → 0 faces scored a perfect 1.0 effect and
    // claimed "far fewer people" — the loudest row on the card, off a single
    // face. Five faces is a busy frame; measuring against that is honest.
    label: 'People in frame', unit: 'faces', range: [0, 5], good: 'context',
    verb: 'have', up: 'more people in frame', down: 'fewer people in frame',
  },
  face_sharpness_score: {
    label: 'Face sharpness', unit: '0–100', range: [0, 100], good: 'higher',
    verb: 'have', up: 'sharper faces', down: 'softer faces',
  },
  face_size_ratio: {
    label: 'Face size', unit: 'share of frame', range: [0, 1], good: 'context',
    verb: 'have', up: 'larger faces in frame', down: 'smaller faces in frame',
  },
  eye_openness_ratio: {
    label: 'Eye openness', unit: '0–1', range: [0, 1], good: 'higher',
    verb: 'have', up: 'wider-open eyes', down: 'more half-closed eyes',
  },
  eyes_open: {
    label: 'Eyes open', kind: 'binary', noun: 'Open eyes', range: [0, 1], good: 'higher',
  },
  smile_score: {
    label: 'Smiling', unit: '0–1', range: [0, 1], good: 'context',
    verb: 'have', up: 'more smiling', down: 'less smiling',
  },
  mouth_open_score: {
    label: 'Mouth open', unit: '0–1', range: [0, 1], good: 'context',
    verb: 'have', up: 'more open mouths', down: 'fewer open mouths',
  },
  focal_length_mm: {
    label: 'Focal length', unit: 'mm', range: null, good: 'context',
    verb: 'use', up: 'longer lenses', down: 'wider lenses',
  },
  aperture: {
    label: 'Aperture', unit: 'f-stop', range: null, good: 'context',
    verb: 'use', up: 'narrower apertures', down: 'wider apertures',
  },
  iso: {
    label: 'ISO', unit: 'ISO', range: null, good: 'lower',
    verb: 'use', up: 'higher ISO', down: 'lower ISO',
  },
  scene_is_portrait:  { label: 'Portrait',  kind: 'binary', noun: 'Portraits',     range: [0, 1], good: 'context' },
  scene_is_landscape: { label: 'Landscape', kind: 'binary', noun: 'Landscapes',    range: [0, 1], good: 'context' },
  scene_is_street:    { label: 'Street',    kind: 'binary', noun: 'Street scenes', range: [0, 1], good: 'context' },
  scene_is_night:     { label: 'Night',     kind: 'binary', noun: 'Night shots',   range: [0, 1], good: 'context' },
  scene_is_macro:     { label: 'Macro',     kind: 'binary', noun: 'Macro shots',   range: [0, 1], good: 'context' },
  scene_is_indoor:    { label: 'Indoor',    kind: 'binary', noun: 'Indoor shots',  range: [0, 1], good: 'context' },
  scene_is_action:    { label: 'Action',    kind: 'binary', noun: 'Action shots',  range: [0, 1], good: 'context' },
  scene_is_water:     { label: 'Water',     kind: 'binary', noun: 'Water scenes',  range: [0, 1], good: 'context' },
  aesthetic_score: {
    label: 'Aesthetic (TOPIQ-IAA)', unit: '0–100', range: [0, 100], good: 'higher',
    verb: 'are', up: 'more appealing', down: 'less appealing',
  },
  subject_prominence_score: {
    label: 'Subject prominence', unit: '0–1', range: [0, 1], good: 'higher',
    verb: 'have', up: 'more prominent subjects', down: 'less prominent subjects',
  },
  background_distraction_score: {
    label: 'Background distraction', unit: '0–1', range: [0, 1], good: 'lower',
    verb: 'have', up: 'busier backgrounds', down: 'cleaner backgrounds',
  },
  eye_contact_score: {
    label: 'Eye contact', unit: '0–1', range: [0, 1], good: 'higher',
    verb: 'have', up: 'more eye contact', down: 'less eye contact',
  },
  decisive_moment_score: {
    label: 'Decisive moment', unit: '0–1', range: [0, 1], good: 'higher',
    verb: 'have', up: 'stronger moments', down: 'weaker moments',
  },
}

function metaFor(name) {
  // An unregistered feature still renders, just without a hand-written
  // sentence — the catch-all "Other" group exists for exactly this case.
  return FEATURE_META[name] || {
    label: name.replace(/_/g, ' '),
    unit: '', range: null, good: 'context',
    verb: 'have', up: 'a higher value', down: 'a lower value',
  }
}

// analyse — turn one backend row into everything a rendered row needs.
// Returns null when either side has no usable value.
function analyse(row) {
  const meta = metaFor(row.feature)
  const binary = meta.kind === 'binary'

  const kept = binary ? row.kept_mean : row.kept_median
  const rej  = binary ? row.rejected_mean : row.rejected_median
  if (kept == null || rej == null) return null

  const delta = kept - rej

  // Effect size: how far the pair moved across the feature's own scale.
  // Without natural bounds, measure against the larger of the two values.
  const span = meta.range
    ? meta.range[1] - meta.range[0]
    : Math.max(Math.abs(kept), Math.abs(rej), 1e-9)
  const effect = Math.min(1, Math.abs(delta) / span)

  // Dot placement. Bounded features sit on their real scale, so a tiny
  // difference looks tiny. Unbounded ones scale to their own pair with
  // headroom, since "23mm vs 75mm" has no universal axis to sit on.
  const domain = meta.range ?? [0, Math.max(kept, rej) * 1.15 || 1]
  const place = (v) => {
    const t = (v - domain[0]) / (domain[1] - domain[0] || 1)
    return Math.max(0, Math.min(1, t))
  }

  const unexpected =
    meta.good !== 'context' &&
    effect >= WEAK_EFFECT &&
    !((meta.good === 'higher' && delta > 0) || (meta.good === 'lower' && delta < 0))

  return {
    name: row.feature,
    meta, binary, kept, rej, delta, effect, unexpected,
    keptPos: place(kept),
    rejPos: place(rej),
    weak: effect < WEAK_EFFECT,
  }
}

const pct = (v) => `${Math.round(v * 100)}%`

function magnitude(effect) {
  if (effect >= 0.35) return 'far'
  if (effect >= 0.15) return 'clearly'
  return 'slightly'
}

function sentence(a) {
  if (a.binary) {
    // Shares state a fact rather than a direction, so they stay truthful even
    // when the two sides are nearly equal.
    const verb = a.meta.singular ? 'appears' : 'appear'
    return `${a.meta.noun} ${verb} in ${pct(a.kept)} of keeps and ${pct(a.rej)} of rejects`
  }
  // Below the threshold there is no direction worth claiming. Saying "slightly
  // worse exposed" next to "95.00 → 95.00" reads as a broken readout.
  if (a.weak) return `${a.meta.label} made no real difference`
  const word = a.delta > 0 ? a.meta.up : a.meta.down
  return `Your keeps ${a.meta.verb} ${magnitude(a.effect)} ${word}`
}

function formatValue(a, v) {
  if (a.binary) return pct(v)
  if (Math.abs(v) >= 100) return v.toFixed(0)
  if (Math.abs(v) >= 10) return v.toFixed(1).replace(/\.0$/, '')
  return v.toFixed(2)
}

export function KeepVsReject({ data, error, loading, stale }) {
  const features = data?.features || []
  const hasAny = features.some(f => f.n_kept > 0 || f.n_rejected > 0)

  // Photo counts, not column counts — features[0].n_kept counts the non-NaN
  // values of ONE feature and under-reports as soon as a feature is missing
  // for part of the corpus.
  const nKept   = data?.n_kept_photos ?? 0
  const nReject = data?.n_rejected_photos ?? 0
  const lowSample = nKept < LOW_SAMPLE_THRESHOLD || nReject < LOW_SAMPLE_THRESHOLD

  const byName = Object.fromEntries(features.map(f => [f.feature, f]))
  const grouped = FEATURE_GROUPS.map(g => ({
    ...g,
    rows: g.features.map(n => byName[n]).filter(Boolean).map(analyse).filter(Boolean),
  }))
  const known = new Set(FEATURE_GROUPS.flatMap(g => g.features))
  const other = features
    .filter(f => !known.has(f.feature) && !HIDDEN_FEATURES.has(f.feature))
    .map(analyse).filter(Boolean)
  if (other.length) {
    grouped.push({ id: 'other', label: 'Other', defaultOpen: false, rows: other })
  }

  const surprises = grouped.reduce((n, g) => n + g.rows.filter(r => r.unexpected).length, 0)

  return (
    <Card
      title="What you keep vs reject"
      headerRight={
        hasAny && (
          <span>
            <span className="font-mono text-[#7DB89A] tabular-nums">{nKept.toLocaleString()}</span> kept ·{' '}
            <span className="font-mono text-[#C97B7B] tabular-nums">{nReject.toLocaleString()}</span> rejected
          </span>
        )
      }
      error={error}
      loading={loading}
      stale={stale}
    >
      {!hasAny ? (
        <Empty>
          Patterns surface here once you&apos;ve decided on both sides. It answers things
          like &quot;you keep wide-aperture shots and reject high-ISO ones.&quot;
        </Empty>
      ) : (
        <>
          <p className="text-xs text-[#8a8b8c] leading-relaxed mb-4 max-w-3xl">
            The typical value in the photos you kept <Dot color={KEEP_COLOR} /> versus the
            ones you rejected <Dot color={REJECT_COLOR} />, each on its own scale — a wide
            gap is a real difference, two dots on top of each other means the measure
            didn&apos;t sway you.
            {surprises > 0 && (
              <>
                {' '}
                <span className="text-[#E8B84A]">
                  {surprises} {surprises === 1 ? 'row runs' : 'rows run'} against the usual
                  direction
                </span>
                {' '}— those come first, because that&apos;s the part a quality score
                can&apos;t express.
              </>
            )}
          </p>

          {lowSample && (
            <p className="text-xs text-[#E8B84A] mb-3 leading-relaxed">
              Low sample size on one or both sides — patterns will firm up with more decisions.
            </p>
          )}

          <div className="flex flex-col gap-2">
            {grouped.map(g => (
              <CollapsibleSection
                key={g.id}
                storageKey={`pca.dashboard.section.${g.id}`}
                label={g.label}
                defaultOpen={g.defaultOpen}
                headerRight={
                  g.rows.some(r => r.unexpected)
                    ? <span className="text-[11px] text-[#E8B84A]">unexpected</span>
                    : null
                }
              >
                <FeatureGroup rows={g.rows} />
              </CollapsibleSection>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

function FeatureGroup({ rows }) {
  const [showAll, setShowAll] = useState(false)
  const weakCount = rows.filter(r => r.weak).length
  const pool = showAll ? rows : rows.filter(r => !r.weak)

  // Unexpected first, then strongest effect. The old order was whatever
  // FEATURE_GROUPS declared, which buried the one row worth reading.
  const visible = [...pool].sort((a, b) => {
    if (a.unexpected !== b.unexpected) return a.unexpected ? -1 : 1
    return b.effect - a.effect
  })

  if (rows.length === 0) return <Empty>Nothing measured in this group yet.</Empty>

  if (visible.length === 0) {
    return (
      <div className="flex items-center justify-between gap-2">
        <Empty>Nothing here separated your keeps from your rejects.</Empty>
        <LinkButton onClick={() => setShowAll(true)} label={`Show all ${rows.length}`} />
      </div>
    )
  }

  return (
    <>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-x-10">
        {visible.map(a => <FeatureRow key={a.name} a={a} />)}
      </div>
      {weakCount > 0 && (
        <div className="pt-2">
          <LinkButton
            onClick={() => setShowAll(s => !s)}
            label={showAll ? `Hide ${weakCount} that barely differ` : `Show ${weakCount} that barely differ`}
          />
        </div>
      )}
    </>
  )
}

function FeatureRow({ a }) {
  return (
    <div className="py-2 px-2 -mx-2 rounded transition-colors hover:bg-[rgba(255,255,255,0.03)]">
      <div className="flex items-baseline gap-2 mb-1.5">
        <span className="text-xs text-[#f0f0f0] leading-snug">{sentence(a)}</span>
        {a.unexpected && (
          <span
            className="text-[10px] text-[#E8B84A] border border-[rgba(232,184,74,0.35)] rounded px-1 py-px flex-shrink-0"
            title={`This measure treats ${a.meta.good} as better. Your decisions go the other way — that's taste the quality score can't see.`}
          >
            unexpected
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-[11px] text-[#8a8b8c] w-36 truncate flex-shrink-0" title={a.meta.label}>
          {a.meta.label}
          {a.meta.unit && <span className="text-[#6a6b6c]"> · {a.meta.unit}</span>}
        </span>
        <Dumbbell keptPos={a.keptPos} rejPos={a.rejPos} />
        <span className="font-mono text-[11px] tabular-nums whitespace-nowrap flex-shrink-0">
          <span style={{ color: REJECT_COLOR }}>{formatValue(a, a.rej)}</span>
          <span className="text-[#6a6b6c] mx-1">→</span>
          <span style={{ color: KEEP_COLOR }}>{formatValue(a, a.kept)}</span>
        </span>
      </div>
    </div>
  )
}

// Dumbbell — two dots on a shared track, joined by the span between them.
// The gap IS the delta, which is why there is no Δ column any more: it was
// restating the two numbers already sitting on the row.
function Dumbbell({ keptPos, rejPos }) {
  const kept = useGrow(keptPos * 100)
  const rej  = useGrow(rejPos * 100)
  const lo = Math.min(kept, rej)
  const hi = Math.max(kept, rej)
  const ease = 'cubic-bezier(0.22, 1, 0.36, 1)'

  return (
    <div className="relative flex-1 min-w-[72px] h-3 flex items-center">
      <div className="absolute inset-x-0 h-[3px] rounded-full bg-[#1b1c1e]" />
      <div
        className="absolute h-[3px] rounded-full bg-[#3a3c3e]"
        style={{
          left: `${lo}%`,
          width: `${hi - lo}%`,
          transition: `left 240ms ${ease}, width 240ms ${ease}`,
        }}
      />
      <Knob pos={rej} color={REJECT_COLOR} />
      <Knob pos={kept} color={KEEP_COLOR} />
    </div>
  )
}

function Knob({ pos, color }) {
  return (
    <span
      className="absolute w-[7px] h-[7px] rounded-full"
      style={{
        left: `calc(${pos}% - 3.5px)`,
        backgroundColor: color,
        // A ring in the card colour keeps the two dots readable when they
        // land on top of each other, which is exactly the "no signal" case.
        boxShadow: '0 0 0 2px #101111',
        transition: 'left 240ms cubic-bezier(0.22, 1, 0.36, 1)',
      }}
    />
  )
}

function Dot({ color }) {
  return (
    <span
      className="inline-block w-[7px] h-[7px] rounded-full align-middle"
      style={{ backgroundColor: color }}
    />
  )
}

function LinkButton({ onClick, label }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] text-[#9c9c9d] hover:text-[#cecece] transition-colors px-2 py-1 -mx-2 rounded focus:outline-none focus-visible:text-[#5BB8D4]"
    >
      {label}
    </button>
  )
}
