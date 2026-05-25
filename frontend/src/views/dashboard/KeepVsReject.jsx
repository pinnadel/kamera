// KeepVsReject — for each scored feature, the median value among kept
// photos vs the median among rejected photos.
//
// Rows are grouped into 5 collapsible sections (Technical · Face · Camera ·
// Scene · Aesthetic) mirroring the DetailView pattern. Within each section
// "weak signal" rows (delta ≈ 0 or both-zero one-hots) hide by default
// behind a per-section "Show all" toggle — high-noise scene flags don't
// drown the few real signals.
//
// Each row carries a unit + "good direction" hint so numbers are legible
// without a tooltip: "0–100 ↑" tells you the score is 0–100 and higher is
// the kept side's preference. A ✓/✗ next to the delta calls out whether
// the user's actual behavior matches the feature's "better" direction;
// when it doesn't (e.g. they reject sharper photos), that's the surprising
// signal worth surfacing.

import { useState } from 'react'
import { CollapsibleSection } from '../../ui/CollapsibleSection'

const LOW_SAMPLE_THRESHOLD = 5

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
    label: 'Face',
    defaultOpen: true,
    features: [
      'face_present',
      'face_detected',
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

// Per-feature metadata. `good`: 'higher' | 'lower' | 'context' — context
// means there's no universal "better" direction (a 35mm photo isn't
// objectively better than a 50mm photo). When 'context', no arrow glyph
// renders and no ✓/✗ verdict is computed.
const FEATURE_META = {
  sharpness_score:              { unit: '0–100',  good: 'higher' },
  exposure_score:               { unit: '0–100',  good: 'higher' },
  iqa_score:                    { unit: '0–100',  good: 'higher' },
  highlight_clip_pct:           { unit: '%',      good: 'lower'  },
  shadow_clip_pct:              { unit: '%',      good: 'lower'  },
  shake_detected:               { unit: '0/1',    good: 'lower'  },
  face_present:                 { unit: '0/1',    good: 'context' },
  face_detected:                { unit: '0/1',    good: 'context' },
  face_count:                   { unit: 'n',      good: 'context' },
  face_sharpness_score:         { unit: '0–100',  good: 'higher' },
  face_size_ratio:              { unit: 'frame',  good: 'context' },
  eye_openness_ratio:           { unit: '0–1',    good: 'higher' },
  eyes_open:                    { unit: '0/1',    good: 'higher' },
  smile_score:                  { unit: '0–1',    good: 'context' },
  mouth_open_score:             { unit: '0–1',    good: 'context' },
  focal_length_mm:              { unit: 'mm',     good: 'context' },
  aperture:                     { unit: 'f-stop', good: 'context' },
  iso:                          { unit: 'ISO',    good: 'lower'  },
  scene_is_portrait:            { unit: '0/1',    good: 'context' },
  scene_is_landscape:           { unit: '0/1',    good: 'context' },
  scene_is_street:              { unit: '0/1',    good: 'context' },
  scene_is_night:               { unit: '0/1',    good: 'context' },
  scene_is_macro:               { unit: '0/1',    good: 'context' },
  scene_is_indoor:              { unit: '0/1',    good: 'context' },
  scene_is_action:              { unit: '0/1',    good: 'context' },
  scene_is_water:               { unit: '0/1',    good: 'context' },
  aesthetic_score:              { unit: '0–100',  good: 'higher' },
  subject_prominence_score:     { unit: '0–1',    good: 'higher' },
  background_distraction_score: { unit: '0–1',    good: 'lower'  },
  eye_contact_score:            { unit: '0–1',    good: 'higher' },
  decisive_moment_score:        { unit: '0–1',    good: 'higher' },
}

function isWeak(f) {
  if (f.kept_median == null || f.rejected_median == null) return true
  const delta = f.kept_median - f.rejected_median
  if (Math.abs(delta) < 0.01) return true
  if (Math.abs(f.kept_median) + Math.abs(f.rejected_median) < 0.001) return true
  return false
}

export function KeepVsReject({ data }) {
  const features = data?.features || []
  const hasAny   = features.some(f => f.n_kept > 0 || f.n_rejected > 0)

  // Pull n from the first feature (all rows share counts since features_json
  // freezes the whole vector at decision time).
  const nKept    = features[0]?.n_kept ?? 0
  const nReject  = features[0]?.n_rejected ?? 0
  const lowSample = nKept < LOW_SAMPLE_THRESHOLD || nReject < LOW_SAMPLE_THRESHOLD

  // Bucket features by group; anything not listed lands in 'other' so a
  // future _COLUMNS addition surfaces rather than disappearing silently.
  const byName = Object.fromEntries(features.map(f => [f.feature, f]))
  const grouped = FEATURE_GROUPS.map(g => ({
    ...g,
    rows: g.features.map(name => byName[name]).filter(Boolean),
  }))
  const knownNames = new Set(FEATURE_GROUPS.flatMap(g => g.features))
  const other = features.filter(f => !knownNames.has(f.feature))
  if (other.length) {
    grouped.push({ id: 'other', label: 'Other', defaultOpen: false, rows: other })
  }

  return (
    <section className="rounded-xl border border-[#2a2b2d] bg-[#101111] p-6">
      <div className="flex items-baseline justify-between mb-2 gap-4 flex-wrap">
        <h2 className="text-base font-semibold text-[#f9f9f9]">What you keep vs reject</h2>
        {hasAny && (
          <span className="text-xs text-[#9c9c9d]">
            <span className="font-mono text-[#7DB89A]">{nKept}</span> kept ·{' '}
            <span className="font-mono text-[#C97B7B]">{nReject}</span> rejected
          </span>
        )}
      </div>

      {hasAny && (
        <p className="text-xs text-[#6a6b6c] leading-relaxed mb-4">
          Kept median <span className="text-[#7DB89A]">·</span> Rejected median <span className="text-[#C97B7B]">·</span> Delta
          {' '}<span className="text-[#4a4a4a]">·</span>{' '}
          <span className="text-[#9c9c9d]">↑/↓</span> direction that the feature considers &quot;better&quot;
          {' '}<span className="text-[#4a4a4a]">·</span>{' '}
          <span className="text-[#9c9c9d]">✓/✗</span> whether your behavior matches that direction
        </p>
      )}

      {!hasAny ? (
        <p className="text-xs text-[#9c9c9d] leading-relaxed">
          Keep vs reject patterns will surface here once you&apos;ve made decisions across both. Tells you things like &quot;you keep wide-aperture shots and reject high-ISO ones.&quot;
        </p>
      ) : (
        <>
          {lowSample && (
            <p className="text-xs text-[#E8B84A] mb-3 leading-relaxed">
              Low sample size on one or both sides. Patterns will firm up with more decisions.
            </p>
          )}
          <div className="flex flex-col gap-2">
            {grouped.map(g => (
              <CollapsibleSection
                key={g.id}
                storageKey={`pca.dashboard.section.${g.id}`}
                label={g.label}
                defaultOpen={g.defaultOpen}
              >
                <FeatureGroup rows={g.rows} />
              </CollapsibleSection>
            ))}
          </div>
        </>
      )}
    </section>
  )
}

function FeatureGroup({ rows }) {
  const [showAll, setShowAll] = useState(false)
  const weakCount = rows.filter(isWeak).length
  const visible = showAll ? rows : rows.filter(r => !isWeak(r))

  if (rows.length === 0) {
    return <p className="text-xs text-[#6a6b6c]">No features in this group.</p>
  }
  if (visible.length === 0) {
    return (
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-[#6a6b6c]">No strong signal yet.</p>
        <button
          onClick={() => setShowAll(true)}
          className="text-xs text-[#9c9c9d] hover:text-[#cecece] underline transition-colors"
        >
          Show all {rows.length}
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1">
        {visible.map(f => <FeatureRow key={f.feature} feature={f} />)}
      </div>
      {weakCount > 0 && (
        <div className="pt-1">
          <button
            onClick={() => setShowAll(s => !s)}
            className="text-xs text-[#9c9c9d] hover:text-[#cecece] underline transition-colors"
          >
            {showAll ? `Hide ${weakCount} weak-signal` : `Show all (${weakCount} weak-signal hidden)`}
          </button>
        </div>
      )}
    </>
  )
}

function FeatureRow({ feature }) {
  const { feature: name, kept_median, rejected_median } = feature
  const meta = FEATURE_META[name] || { unit: '', good: 'context' }
  const both = kept_median != null && rejected_median != null
  const fmt  = (v) => v == null ? '—' : (Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(0))

  const directionGlyph = meta.good === 'higher' ? '↑' : meta.good === 'lower' ? '↓' : ''

  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-xs text-[#cecece] flex-1 truncate">
        {name.replace(/_/g, ' ')}
      </span>
      <span className="text-[10px] text-[#6a6b6c] font-mono w-14 text-right whitespace-nowrap">
        {meta.unit}{directionGlyph && <span className="text-[#9c9c9d] ml-1">{directionGlyph}</span>}
      </span>
      <span className="font-mono text-xs text-[#7DB89A] w-12 text-right" title="Kept median">
        {fmt(kept_median)}
      </span>
      <span className="text-[#6a6b6c] text-xs">·</span>
      <span className="font-mono text-xs text-[#C97B7B] w-12 text-right" title="Rejected median">
        {fmt(rejected_median)}
      </span>
      <span className="font-mono text-xs w-12 text-right" title="Difference (kept − rejected)">
        {both ? <DeltaPill kept={kept_median} rejected={rejected_median} /> : <span className="text-[#6a6b6c]">—</span>}
      </span>
      <span className="text-xs w-3 text-right" title="Matches the feature's better direction?">
        {both && <Verdict delta={kept_median - rejected_median} good={meta.good} />}
      </span>
    </div>
  )
}

function DeltaPill({ kept, rejected }) {
  const delta = kept - rejected
  if (Math.abs(delta) < 0.01) return <span className="text-[#9c9c9d]">≈</span>
  const sign = delta > 0 ? '+' : ''
  const tone = delta > 0 ? 'text-[#7DB89A]' : 'text-[#C97B7B]'
  return <span className={tone}>{sign}{Math.abs(delta) < 10 ? delta.toFixed(2) : delta.toFixed(0)}</span>
}

// Verdict — ✓ when kept-side direction matches the feature's "better"
// direction; ✗ when it doesn't (surprising — user is keeping the "worse"
// side, which is itself a learnable signal); blank for 'context' features
// and tiny deltas.
function Verdict({ delta, good }) {
  if (good === 'context') return null
  if (Math.abs(delta) < 0.01) return null
  const matches = (good === 'higher' && delta > 0) || (good === 'lower' && delta < 0)
  if (matches) return <span className="text-[#7DB89A]">✓</span>
  return <span className="text-[#C97B7B]">✗</span>
}
