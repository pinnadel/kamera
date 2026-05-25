// Shared math for filmstrip layouts. Two surfaces need the strip's total
// pixel height to position siblings around it:
//   - DetailView (`gridFilmstrip` strip with score/decision badges per thumb)
//   - GroupLoupe (`LoupePane` strip, no score row)
// and the floating bottom pill needs to lift above the strip in both.
//
// `THUMB_ASPECT` matches the inline style applied to every strip cell
// (height = width × 0.70). Cell chrome (border + py-3 padding + optional
// score row) is captured separately so each surface can pick the right value.

export const THUMB_ASPECT = 0.70

// DetailView's GridFilmstrip renders a score+decision row below each thumb.
// Total strip-cell height ≈ thumbH + ~28 px of inner caption + py-3 padding
// + 1 px border. Treat 64 as the rounded chrome budget that keeps the
// preview pane uncropped at any thumb size.
export const FILMSTRIP_CHROME_WITH_BADGES = 64

// GroupLoupe's LoupePane strip has no per-thumb chrome row — just the
// thumb image inside a border + py-3. Smaller chrome budget.
export const FILMSTRIP_CHROME_BARE = 32

// Vertical gap between the bottom of the strip and the bottom of the pill.
export const PILL_STRIP_GAP = 8

// Strip-cell height in pixels for a given thumb-width and chrome variant.
export function stripHeight(thumbWidth, chrome) {
  return Math.round(thumbWidth * THUMB_ASPECT) + chrome
}

// Where the floating bottom pill should sit when a filmstrip is below it.
// Returns the `bottom` value in pixels for `position: fixed`.
export function pillBottomAboveStrip(thumbWidth, chrome) {
  return stripHeight(thumbWidth, chrome) + PILL_STRIP_GAP
}
