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
    // 'batch' = a folder on disk, 'photos' = an Apple Photos album. A tab's
    // SOURCE, not its mode — watch-live is a separate per-tab flag.
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
    // Same idea as selectedGroupId, but for a burst's ALREADY-DECIDED members
    // collapsed into one DecidedGroupTile (Keeps/Maybes/Rejects views). Holds
    // the tile's groupKey. Mutually exclusive with both selectedIdx and
    // selectedGroupId — Enter/Space on it opens DetailView carrying the burst,
    // not GroupLoupe (nothing to rank/cull, only to review/re-cull).
    selectedDecidedGroupKey: null,
    loaded: true,  // empty tab has nothing to load
    watchLive: false,
  }
}

export function makeReadyTab(folderPath, { kind = 'batch', label = null, libraryPath = null } = {}) {
  return {
    ...makeNewTab(),
    folderPath,
    kind,
    label,
    libraryPath,
    status: 'ready',
    loaded: false,
  }
}

// A Photos-album tab. Its folderPath is the opaque `photos://<lib>/<album>`
// key the backend uses as source_folder — never a real path, so anything that
// does path arithmetic on folderPath has to check this first.
export function isPhotosTab(tab) {
  return tab?.kind === 'photos'
}

export function tabLabel(tab) {
  // A Photos album carries its own display name: splitting its photos:// key
  // on '/' would render a raw album UUID.
  if (tab?.label) return tab.label
  if (!tab.folderPath) return 'New analysis'
  const segments = tab.folderPath.split('/').filter(Boolean)
  return segments[segments.length - 1] || tab.folderPath
}
