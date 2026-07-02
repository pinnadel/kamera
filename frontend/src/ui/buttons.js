// Button variant classes — use these instead of ad-hoc inline Tailwind.
//
// Convention:
//   <button className={BTN_PRIMARY} onClick={…}>
//     <WandSparkles size={16} />   ← Tier 1: full-size action (BTN_*)
//     Cull {n} photos
//   </button>
//
// Icon size tiers (keeps icons proportional to their text context):
//   Tier 1 — full action buttons (BTN_PRIMARY/SECONDARY/DANGER/CTA_SECONDARY):  size={16}
//   Tier 2 — compact labeled buttons (text-xs toolbar/pill/chip/tab):           size={15}
//   Tier 3 — inline/micro (text links, badge icons, input prefix, chevrons):    size={12-13}
//   Icon-only (BTN_ICON): size={18} — the larger container gives better visual weight
//
// The `inline-flex items-center gap-2` baseline is shared across every variant,
// so dropping a Lucide icon as the first child aligns automatically with the
// label. Decision buttons (Keep/Maybe/Reject) intentionally don't use these
// — they have their own colored-border + kbd-badge system.

const BASE = 'inline-flex items-center gap-2 rounded-lg text-sm tracking-wide ' +
             'transition-opacity disabled:opacity-40 disabled:cursor-not-allowed'

// Primary CTA. Visual chrome (gradient + multi-layer shadow + cyan glow on
// hover) lives in `.btn-primary` in index.css — too many layers to express
// cleanly as Tailwind utilities.
export const BTN_PRIMARY       = `${BASE} btn-primary px-4 py-2 font-semibold`

// Secondary CTA — invited action, dark surface + light text. Use when an
// action should be noticed but not compete with the primary. Cancel/dismiss
// uses BTN_SECONDARY (muted text) instead.
export const BTN_CTA_SECONDARY = `${BASE} px-4 py-2 font-semibold bg-[#1b1c1e] border border-[rgba(255,255,255,0.15)] text-[#f0f0f0] hover:opacity-80`

// Cancel / dismiss — quiet.
export const BTN_SECONDARY     = `${BASE} px-4 py-2 font-medium bg-[#101111] border border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70`

// Destructive — coral text, no fill. Hover is a coral tint (the only
// hover-color rule in the system; opacity alone reads dead).
export const BTN_DANGER        = `${BASE} px-3 py-1.5 font-medium text-[#C97B7B] hover:bg-[rgba(201,123,123,0.12)] !transition-colors`

// Icon-only — square pads, no gap. For close, rotate, settings, help, etc.
// w-9 h-9 = 36px hit target (WCAG 2.5.8 compliant with surrounding spacing).
// Use size={18} for the icon inside.
export const BTN_ICON          = 'inline-flex items-center justify-center w-9 h-9 rounded-lg text-[#9c9c9d] hover:opacity-70 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed'

// ── Active-control emphasis (single source of truth) ──────────────────────
// Cyan is the "this control is ON / SET / narrowing what you see" signal.
// One vocabulary, two intensities:
//
//   ACTIVE_PILL — the LOUD tier: cyan-tinted container + border + bold cyan
//     text + heavier icon. Reserved for the FILTER pill — the single control
//     whose active state means "you are viewing a narrowed subset." Pair with
//     `strokeWidth={2.5}` on its leading icon.
//
//   ACTIVE_TOGGLE — the LIGHTER tier: bold cyan text + cyan icon, NO container
//     fill. For binary mode toggles that are currently ON (Select mode, active
//     Search, group-mode, Watch-live). Quieter than the filter so the filter
//     stays the loudest "narrowing" signal.
//
// Do NOT apply either to always-has-a-value controls (Sort, View/size) — they
// would read as active 100% of the time. Decision colors (K/M/R) and selection
// rings are a separate vocabulary; leave them alone.
export const ACTIVE_PILL   = 'bg-[rgba(91,184,212,0.12)] border-[rgba(91,184,212,0.30)] text-[#5BB8D4] font-bold'
export const ACTIVE_TOGGLE = 'text-[#5BB8D4] font-bold'
