// Similarity-group state and actions for App.jsx.
//
// The redesigned Groups feature treats each cluster as a first-class workspace:
// - In the grid each group is rendered as a single GroupTile (the AI's hero
//   preview + a count badge) — no more wide inline strips.
// - Clicking a tile opens GroupLoupe (full-screen overlay) where the user
//   compares photos in Survey or Loupe sub-mode and decides per-photo K/M/X.
//
// Owns: groups, threshold, groupsLoading, loupeGroupId.
// Exposes: loadGroups, sendGroupDecision, enterLoupe, exitLoupe.

import { useState, useEffect, useCallback, useMemo } from 'react'
import { API } from '../api'

// localStorage keys for persisted clustering knobs. Stored globally
// (not per-tab) because users converge on the values that match their
// shooting style and want them sticky across sessions.
const LS_THRESHOLD     = 'pca.groupThreshold'
const LS_FACE_THRESH   = 'pca.faceThreshold'
const LS_TIME_GAP      = 'pca.groupTimeGapSeconds'

const loadFloat = (key, fallback) => {
  const raw = localStorage.getItem(key)
  if (raw == null) return fallback
  const parsed = parseFloat(raw)
  return Number.isFinite(parsed) ? parsed : fallback
}

// People-mode threshold scale changed: SigLIP full-photo embeddings used
// 0.70–0.90, FaceNet face-identity embeddings use 0.30–0.70 (different
// embedding space, different "same person" cutoff). If the user has a
// stored value from the SigLIP era (≥ 0.70), reset to the new default
// instead of clamping to a meaningless point on the new scale.
const loadFaceThreshold = (key, fallback) => {
  const raw = localStorage.getItem(key)
  if (raw == null) return fallback
  const parsed = parseFloat(raw)
  if (!Number.isFinite(parsed)) return fallback
  if (parsed >= 0.70) return fallback   // legacy SigLIP-era value
  return parsed
}


export function useGroups({ images, activeTab, addToast, loadModelInfo }) {
  const [groups, setGroups]               = useState([])
  const [threshold, setThresholdState]    = useState(() => loadFloat(LS_THRESHOLD, 0.90))
  const [groupsLoading, setGroupsLoading] = useState(false)
  // 'bursts' = similarity groups via SigLIP full-photo embeddings (threshold 0.80–0.99)
  // 'people' = face groups via FaceNet face-identity embeddings (threshold 0.30–0.70)
  const [groupMode, setGroupMode]         = useState('bursts')
  // Photos with a detected face but no FaceNet embedding (analysed before
  // schema v38). Frontend banner uses this to nudge re-analysis.
  const [peoplePendingReanalysis, setPeoplePendingReanalysis] = useState(0)
  const [faceThreshold, setFaceThresholdState] = useState(() => loadFaceThreshold(LS_FACE_THRESH, 0.50))
  // Time-gap primary split (seconds). The dominant signal — photographers
  // pause between distinct moments. Default 60s — tuned for portrait/burst
  // pacing where 90s+ pauses typically mark a new scene. (Was 120s; that
  // value left multi-scene sessions fusing into single 70+ photo clusters
  // when SigLIP cosine happened to bridge them — see scene-fusion
  // diagnosis 2026-05-13.) Stored as seconds so the slider in the UI maps
  // cleanly onto it.
  const [timeGapSeconds, setTimeGapSecondsState] = useState(() => loadFloat(LS_TIME_GAP, 60))
  const [loupeGroupId, setLoupeGroupId]   = useState(null)

  // Persisting setters — write through to localStorage so the user's
  // tuning survives reloads.
  const setThreshold = useCallback((v) => {
    setThresholdState(v); localStorage.setItem(LS_THRESHOLD, String(v))
  }, [])
  const setFaceThreshold = useCallback((v) => {
    setFaceThresholdState(v); localStorage.setItem(LS_FACE_THRESH, String(v))
  }, [])
  const setTimeGapSeconds = useCallback((v) => {
    setTimeGapSecondsState(v); localStorage.setItem(LS_TIME_GAP, String(v))
  }, [])

  // ── loadGroups ───────────────────────────────────────────────────────────
  const loadGroups = useCallback(async () => {
    setGroupsLoading(true)
    try {
      // time_gap_seconds: a very large value disables the split entirely
      // (the backend short-circuits when None, but the URL param can't be
      // absent and "1e9" effectively never triggers a split).
      const gap = timeGapSeconds > 0 ? timeGapSeconds : 1e9
      const url = groupMode === 'people'
        ? `${API}/face-groups?threshold=${faceThreshold}`
        : `${API}/similarity-groups?threshold=${threshold}&time_gap_seconds=${gap}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      setGroups(data.groups || [])
      setPeoplePendingReanalysis(
        groupMode === 'people' ? (data.pending_reanalysis || 0) : 0
      )
    } catch (err) {
      console.error('Groups load failed:', err)
      addToast({ type: 'error', message: `Couldn't load groups: ${err.message}`, duration: 6000 })
    } finally {
      setGroupsLoading(false)
    }
  }, [threshold, faceThreshold, timeGapSeconds, groupMode, addToast])

  useEffect(() => { loadGroups() }, [loadGroups])

  // ── loadGroupsAndPrerank ─────────────────────────────────────────────────
  // Used after an analyze batch finishes. Loads the groups (same as the
  // bare `loadGroups`), then POSTs the member id lists to /prerank-groups
  // so the backend can warm the burst_rankings cache before the user
  // opens a loupe. Threshold sliders still call the bare `loadGroups`,
  // so dragging a slider doesn't spam the prerank queue.
  //
  // Cancel on folder change is handled by the caller (App.jsx) — when the
  // active tab changes, it posts /prerank-cancel so the worker doesn't
  // waste cycles on a folder the user just left.
  const loadGroupsAndPrerank = useCallback(async () => {
    setGroupsLoading(true)
    try {
      const gap = timeGapSeconds > 0 ? timeGapSeconds : 1e9
      const url = groupMode === 'people'
        ? `${API}/face-groups?threshold=${faceThreshold}`
        : `${API}/similarity-groups?threshold=${threshold}&time_gap_seconds=${gap}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      const fresh = data.groups || []
      setGroups(fresh)
      setPeoplePendingReanalysis(
        groupMode === 'people' ? (data.pending_reanalysis || 0) : 0
      )
      // Fire-and-forget prerank kick. The backend filters too-small groups
      // (<3) itself, and skips any with an existing cache row, so we can
      // send the full membership map without pre-filtering on the client.
      const memberLists = fresh
        .filter(g => Array.isArray(g.images) && g.images.length >= 3)
        .map(g => g.images.map(im => im.id))
      if (memberLists.length > 0) {
        fetch(`${API}/prerank-groups`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ groups: memberLists }),
        }).catch(() => { /* fire-and-forget; backend failure is non-fatal */ })
      }
    } catch (err) {
      console.error('Groups load + prerank failed:', err)
      addToast({ type: 'error', message: `Couldn't load groups: ${err.message}`, duration: 6000 })
    } finally {
      setGroupsLoading(false)
    }
  }, [threshold, faceThreshold, timeGapSeconds, groupMode, addToast])

  // ── enrichedGroups ───────────────────────────────────────────────────────
  // Merge live `images` state into group members so decisions made elsewhere
  // (grid keyboard, GroupLoupe, batch actions) reflect immediately.
  const enrichedGroups = useMemo(() => {
    const imageMap = new Map(images.map(img => [img.id, img]))
    return groups
      .filter(g => g.size > 1)
      .map(group => {
        const enriched = group.images.map(img => ({ ...img, ...(imageMap.get(img.id) || {}) }))
        const hero = enriched.find(img => img.id === group.best_image_id)
        const rest = enriched
          .filter(img => img.id !== group.best_image_id)
          .sort((a, b) => (b.overall_score || 0) - (a.overall_score || 0))
        return { ...group, images: hero ? [hero, ...rest] : rest }
      })
  }, [groups, images])

  // ── sendGroupDecision ────────────────────────────────────────────────────
  // Single-photo decision used by GroupLoupe. App.jsx wraps this to also
  // patch the local images array.
  const sendGroupDecision = useCallback(async (imageId, decision) => {
    try {
      const res = await fetch(`${API}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId, decision }),
      })
      const data = await res.json()
      if (!res.ok) {
        addToast({ type: 'error', message: data.detail || `Move failed (${res.status})`, duration: 8000 })
        return
      }
      loadModelInfo()
      return { decision, new_path: data.new_path }
    } catch (err) {
      addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 8000 })
    }
  }, [addToast, loadModelInfo])

  // ── sendGroupUndo ────────────────────────────────────────────────────────
  // Reverse a single decision back to "no decision" — used by GroupLoupe
  // and CompareView when undoing a fresh K/M/R (no prior decision to replay).
  const sendGroupUndo = useCallback(async (imageId, previousFilePath) => {
    try {
      const res = await fetch(`${API}/undo-decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId, previous_path: previousFilePath }),
      })
      const data = await res.json()
      if (!res.ok) {
        addToast({ type: 'error', message: data.detail || `Undo failed (${res.status})`, duration: 6000 })
        return
      }
      loadModelInfo()
      return { decision: null, new_path: data.new_path }
    } catch (err) {
      addToast({ type: 'error', message: `Undo failed: ${err.message}`, duration: 6000 })
    }
  }, [addToast, loadModelInfo])

  // ── Loupe open/close ─────────────────────────────────────────────────────
  const enterLoupe = useCallback((groupBestId) => {
    setLoupeGroupId(groupBestId)
    setLoupeAnchorId(null)
  }, [])
  const exitLoupe  = useCallback(() => {
    setLoupeGroupId(null)
    setLoupeAnchorId(null)
  }, [])

  // Anchor: when the user re-clusters from inside the loupe, the current
  // group dissolves into smaller ones. Setting an anchor (typically the
  // photo the user was focused on) tells us which new group to land on
  // once the next loadGroups response arrives.
  const [loupeAnchorId, setLoupeAnchorId] = useState(null)

  // The currently-open group, or null. Recomputed when enrichedGroups
  // changes so live decisions update what the loupe renders.
  const loupeGroup = useMemo(
    () => enrichedGroups.find(g => g.best_image_id === loupeGroupId) || null,
    [enrichedGroups, loupeGroupId],
  )

  // Re-anchor: if an anchor is pending and the current loupeGroup either
  // doesn't exist or doesn't contain the anchor photo, find a group that
  // does and switch to it. Anchor is consumed (cleared) once resolved.
  useEffect(() => {
    if (loupeAnchorId == null) return
    const containsAnchor = loupeGroup?.images.some(img => img.id === loupeAnchorId)
    if (containsAnchor) { setLoupeAnchorId(null); return }
    const next = enrichedGroups.find(g => g.images.some(img => img.id === loupeAnchorId))
    if (next) {
      setLoupeGroupId(next.best_image_id)
      setLoupeAnchorId(null)
    }
    // If no group contains the anchor (it became a singleton), the close
    // effect below kicks in and the loupe exits — that's the right
    // behaviour: the photo no longer has neighbours to compare against.
  }, [loupeAnchorId, loupeGroup, enrichedGroups])

  // If the currently-open group disappears (threshold change, photos all
  // rejected and removed, etc.), close the loupe so the user isn't stuck.
  // Don't close while an anchor is pending — the re-anchor effect above
  // is about to switch us to the right group.
  useEffect(() => {
    if (loupeGroupId != null && !loupeGroup && loupeAnchorId == null) {
      setLoupeGroupId(null)
    }
  }, [loupeGroupId, loupeGroup, loupeAnchorId])

  return {
    groups,
    setGroups,
    threshold,
    setThreshold,
    groupMode,
    setGroupMode,
    faceThreshold,
    setFaceThreshold,
    timeGapSeconds,
    setTimeGapSeconds,
    peoplePendingReanalysis,
    groupsLoading,
    enrichedGroups,
    loadGroups,
    loadGroupsAndPrerank,
    sendGroupDecision,
    sendGroupUndo,
    loupeGroupId,
    loupeGroup,
    enterLoupe,
    exitLoupe,
    setLoupeAnchorId,
  }
}
