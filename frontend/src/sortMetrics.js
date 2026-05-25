// Single source of truth for grid + GroupLoupe sort options. Date and Name
// are always available (the L1 menu). Score metrics live under the Score
// submenu and can be toggled in Settings → Display → Sort options.

// Each metric carries `desc`/`asc` semantic phrasing so the SortPill can show
// what a direction *means* for this specific score (e.g. "Biggest smiles" vs
// just "↓"). Used for the arrow tooltip and the L2 row subtitles.
export const ALL_METRICS = [
  // Technical
  { id: 'sharpness_score',      label: 'Sharpness',          group: 'Technical',         default: true,  desc: 'Sharpest',         asc: 'Softest'           },
  { id: 'exposure_score',       label: 'Exposure',           group: 'Technical',         default: true,  desc: 'Best exposure',    asc: 'Worst exposure'    },
  { id: 'overall_score',        label: 'Overall',            group: 'Technical',         default: true,  desc: 'Highest',          asc: 'Lowest'            },
  { id: 'highlight_clip_pct',   label: 'Highlight clip',     group: 'Technical',         default: false, desc: 'Most clipped',     asc: 'Least clipped'     },
  { id: 'shadow_clip_pct',      label: 'Shadow clip',        group: 'Technical',         default: false, desc: 'Most clipped',     asc: 'Least clipped'     },
  // AI Quality
  { id: 'iqa_score',            label: 'Perceptual quality', group: 'AI Quality',        default: true,  desc: 'Highest quality',  asc: 'Lowest quality'    },
  { id: 'aesthetic_score',      label: 'Aesthetic',          group: 'AI Quality',        default: true,  desc: 'Most appealing',   asc: 'Least appealing'   },
  { id: 'face_sharpness_score', label: 'Face sharpness',     group: 'AI Quality',        default: true,  desc: 'Sharpest faces',   asc: 'Softest faces'     },
  { id: 'eye_openness_ratio',   label: 'Eye openness',       group: 'AI Quality',        default: true,  desc: 'Most open eyes',   asc: 'Most closed eyes'  },
  { id: 'smile_score',          label: 'Smile',              group: 'AI Quality',        default: true,  desc: 'Biggest smiles',   asc: 'Smallest smiles'   },
  { id: 'mouth_open_score',     label: 'Mouth open',         group: 'AI Quality',        default: false, desc: 'Most open',        asc: 'Most closed'       },
  { id: 'face_size_ratio',      label: 'Face size',          group: 'AI Quality',        default: false, desc: 'Largest faces',    asc: 'Smallest faces'    },
  // Personal
  { id: 'personal_score',       label: 'Personal score',     group: 'Personal scoring',  default: true,  desc: 'Top picks',        asc: 'Bottom picks'      },
  // EXIF (advanced-only)
  { id: 'iso',                  label: 'ISO',                group: 'EXIF',              default: false, desc: 'Highest ISO',      asc: 'Lowest ISO'        },
  { id: 'aperture',             label: 'Aperture',           group: 'EXIF',              default: false, desc: 'Largest f-number', asc: 'Smallest f-number' },
  { id: 'focal_length_mm',      label: 'Focal length',       group: 'EXIF',              default: false, desc: 'Longest',          asc: 'Shortest'          },
]

export const SCORE_GROUPS = ['Technical', 'AI Quality', 'Personal scoring', 'EXIF']

export const LEAN_DEFAULTS = ALL_METRICS.filter(m => m.default).map(m => m.id)

export const TOP_LEVEL_OPTIONS = [
  { field: 'shot_at',  label: 'Date' },
  { field: 'filename', label: 'Name' },
]

export function isScoreField(field) {
  return ALL_METRICS.some(m => m.id === field)
}

export function getMetricLabel(field) {
  if (field === 'shot_at')  return 'Date'
  if (field === 'filename') return 'Name'
  return ALL_METRICS.find(m => m.id === field)?.label ?? field
}

// Short semantic descriptor for a (field, dir) pair — e.g. "Biggest smiles".
// Used in L2 row subtitles next to each metric so the user can see what each
// pick will produce given the currently-selected direction.
export function getDirectionShort(field, dir) {
  if (field === 'shot_at')  return dir === 'desc' ? 'Newest'  : 'Oldest'
  if (field === 'filename') return dir === 'desc' ? 'Z → A'   : 'A → Z'
  const m = ALL_METRICS.find(x => x.id === field)
  if (!m) return dir === 'desc' ? 'High → Low' : 'Low → High'
  return dir === 'desc' ? m.desc : m.asc
}

// Full sentence for the leading-arrow tooltip: explains both the current
// state and what clicking will do — "Biggest smiles first — click for
// smallest smiles".
export function getDirectionTooltip(field, dir) {
  const current = getDirectionShort(field, dir).toLowerCase()
  const flipped = getDirectionShort(field, dir === 'desc' ? 'asc' : 'desc').toLowerCase()
  return `${current.charAt(0).toUpperCase()}${current.slice(1)} first — click for ${flipped}`
}

// Comparator extracted from App.jsx. Strings via localeCompare, numbers via
// subtraction, nulls always last regardless of direction (so an ascending
// sort doesn't surface unanalysed photos at the top).
export function compareImages(a, b, field, dir) {
  const aVal = a[field]
  const bVal = b[field]
  if (aVal == null && bVal == null) return 0
  if (aVal == null) return 1
  if (bVal == null) return -1
  if (typeof aVal === 'string') {
    return dir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
  }
  return dir === 'asc' ? aVal - bVal : bVal - aVal
}
