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
    autoGenerate,
    setAutoGenerate,
  }
}
