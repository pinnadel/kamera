// Settings and personal-model state for App.jsx.
//
// Owns: settings, modelInfo, trainingModel, autoGenerate.
// Exposes: loadSettings, loadModelInfo, trainModel.

import { useState, useEffect, useCallback } from 'react'
import { API } from '../api'

export function useSettings({ addToast, loadImages }) {
  const [settings, setSettings]           = useState(null)
  const [modelInfo, setModelInfo]         = useState(null)
  const [trainingModel, setTrainingModel] = useState(false)
  const [importingModel, setImportingModel] = useState(false)
  const [autoGenerate, setAutoGenerate]   = useState(
    () => localStorage.getItem('pca.autoGenerateExplanation') !== 'false'
  )

  // ── loadSettings ─────────────────────────────────────────────────────────
  const loadSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/settings`)
      if (res.ok) setSettings(await res.json())
    } catch {}
  }, [])

  useEffect(() => { loadSettings() }, [loadSettings])

  // ── loadModelInfo ─────────────────────────────────────────────────────────
  const loadModelInfo = useCallback(() => {
    fetch(`${API}/model-info`)
      .then(r => r.json())
      .then(setModelInfo)
      .catch(() => {})
  }, [])

  useEffect(() => { loadModelInfo() }, [loadModelInfo])

  // ── trainModel ────────────────────────────────────────────────────────────
  const trainModel = useCallback(async () => {
    setTrainingModel(true)
    try {
      const res = await fetch(`${API}/train-model`, { method: 'POST' })
      const data = await res.json()
      await loadModelInfo()
      await loadImages()
      if (res.ok) {
        addToast({ type: 'success', message: `Model trained on ${data.training_size} decisions` })
      }
    } finally {
      setTrainingModel(false)
    }
  }, [loadModelInfo, loadImages, addToast])

  // ── exportTasteModel ──────────────────────────────────────────────────────
  // Download the durable taste-model state (corpus + settings) as a portable
  // JSON backup.
  //
  // We navigate an anchor STRAIGHT at the backend URL rather than fetch→blob.
  // Inside the installed PWA (app_mode_loader) a programmatic fetch to
  // localhost:8000 was being intercepted and resolved to the app shell's
  // index.html — the download saved 1.2 KB of HTML instead of the JSON. A
  // direct `<a href>` to the backend is a plain document request the server
  // answers with its Content-Disposition attachment header; there's nothing
  // in the app layer to intercept. Cache-buster keeps any stale layer from
  // matching a previous navigation. Filename comes from the server header.
  const exportTasteModel = useCallback(() => {
    try {
      const a = document.createElement('a')
      a.href = `${API}/export-taste-model?t=${Date.now()}`
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      addToast({ type: 'success', message: 'Taste model backup downloaded' })
    } catch (err) {
      console.error('Export failed:', err)
      addToast({ type: 'error', message: 'Could not export taste model — server error' })
    }
  }, [addToast])

  // ── importTasteModel ──────────────────────────────────────────────────────
  // Restore a backup file. The JSON is POSTed as the raw request body (no
  // multipart — zero server deps); `mode` is a query param. On success we
  // refresh model info and images since the corpus (and scores) can change.
  const importTasteModel = useCallback(async (file, mode) => {
    setImportingModel(true)
    try {
      const text = await file.text()
      const res = await fetch(`${API}/import-taste-model?mode=${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: text,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.detail || `Server error ${res.status}`)
      }
      await loadModelInfo()
      await loadImages()
      addToast({
        type: 'success',
        message: `Imported ${data.samples_imported} decision${data.samples_imported === 1 ? '' : 's'}`
          + (data.retrained ? ' — model retrained' : ''),
      })
      if (data.schema_warning) {
        addToast({ type: 'warning', message: data.schema_warning })
      }
    } catch (err) {
      console.error('Import failed:', err)
      addToast({ type: 'error', message: `Could not import: ${err.message}` })
    } finally {
      setImportingModel(false)
    }
  }, [loadModelInfo, loadImages, addToast])

  return {
    settings,
    setSettings,
    loadSettings,
    modelInfo,
    setModelInfo,
    loadModelInfo,
    trainModel,
    trainingModel,
    setTrainingModel,
    exportTasteModel,
    importTasteModel,
    importingModel,
    autoGenerate,
    setAutoGenerate,
  }
}
