// Which folders are armed to auto-cull as soon as their analysis finishes.
//
// Stored here rather than in the backend's `folder_settings` table because this
// is frontend orchestration, not backend behaviour: nothing server-side reads
// it, the browser is what watches the batch finish and fires POST /auto-cull.
// Keyed by folder path like `pca.activeFolderPath`, so it survives a relaunch —
// tabs are rebuilt from GET /folders on every launch and carry no state of
// their own.
//
// Every accessor is wrapped: localStorage throws in a private window and can
// come back empty after a site-data clear. An unreadable store means "nothing
// is armed", which is the safe direction — auto-cull moves files.

const KEY = 'pca.autoCullAfterAnalysis'

function readAll() {
  try {
    const raw = localStorage.getItem(KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter(p => typeof p === 'string') : []
  } catch {
    return []
  }
}

export function isArmed(folderPath) {
  if (!folderPath) return false
  return readAll().includes(folderPath)
}

export function setArmed(folderPath, on) {
  if (!folderPath) return
  try {
    const next = readAll().filter(p => p !== folderPath)
    if (on) next.push(folderPath)
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    // Non-fatal: the in-memory tab flag still drives this session's run.
  }
}
