// Shared helpers for the multi-tab folder analysis UI.
//
// Each tab represents one folder analysis "session". The rightmost tab is
// always a fresh empty tab (folderPath === null) — non-closable, non-draggable.
//
// Watch live is now a per-tab attribute (`watchLive: true`) instead of a
// dedicated `kind: 'live'` tab. Only one tab can have it on at a time —
// turning it on for tab A turns it off everywhere else (enforced in App.jsx).

export function makeNewTab() {
  return {
    id: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `t-${Date.now()}-${Math.random()}`,
    kind: 'batch',
    folderPath: null,
    status: 'empty',
    images: [],
    progress: null,
    analyzeResult: null,
    resultDismissed: false,
    selectedIdx: 0,
    // When the keyboard cursor lands on a group tile (rather than a photo),
    // selectedGroupId holds that group's best_image_id. Exactly one of
    // selectedIdx / selectedGroupId is "active" at a time — selectedGroupId
    // null means selectedIdx wins. The cursor advances onto groups after a
    // K/M/R that left the next grid item as a group, and arrow nav can land
    // on groups too — Enter / Space then opens the GroupLoupe.
    selectedGroupId: null,
    loaded: true,  // empty tab has nothing to load
    watchLive: false,
  }
}

export function makeReadyTab(folderPath) {
  return {
    ...makeNewTab(),
    folderPath,
    status: 'ready',
    loaded: false,
  }
}

export function tabLabel(tab) {
  if (!tab.folderPath) return 'New analysis'
  const segments = tab.folderPath.split('/').filter(Boolean)
  return segments[segments.length - 1] || tab.folderPath
}
