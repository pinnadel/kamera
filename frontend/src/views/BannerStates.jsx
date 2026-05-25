// Temporary dev preview — renders every PersonalModelBanner state side by
// side so you can inspect design and copy without needing real training data.
// Toggle with Shift+B in the app (wired in App.jsx; remove when done).

import { X } from 'lucide-react'
import { PersonalModelBanner } from './PersonalModelBanner'

const STATES = [
  {
    label: '1 · Untrained (0 / 30)',
    modelInfo: {
      model_status:      'untrained',
      decided_count:     8,
      min_decisions:     30,
      training_size:     0,
      auto_running:      false,
      last_auto_train_at: null,
      pending_samples:   0,
    },
  },
  {
    label: '2 · Untrained (near threshold)',
    modelInfo: {
      model_status:      'untrained',
      decided_count:     27,
      min_decisions:     30,
      training_size:     0,
      auto_running:      false,
      last_auto_train_at: null,
      pending_samples:   0,
    },
  },
  {
    label: '3 · Learning your taste (30 / 50)',
    modelInfo: {
      model_status:      'learning',
      decided_count:     30,
      min_decisions:     30,
      training_size:     30,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      pending_samples:   0,
    },
  },
  {
    label: '4 · Learning — re-training in flight',
    modelInfo: {
      model_status:      'learning',
      decided_count:     42,
      min_decisions:     30,
      training_size:     32,
      auto_running:      true,
      last_auto_train_at: new Date(Date.now() - 30 * 1000).toISOString(),
      pending_samples:   10,
    },
  },
  {
    label: '5 · Under review (underperforming)',
    modelInfo: {
      model_status:      'underperforming',
      decided_count:     45,
      min_decisions:     30,
      training_size:     45,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      pending_samples:   0,
    },
  },
  {
    label: '6 · Calibrating (50 – 100)',
    modelInfo: {
      model_status:      'ready',
      decided_count:     75,
      min_decisions:     30,
      training_size:     75,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
      pending_samples:   5,
    },
  },
  {
    label: '7 · Calibrating — re-training in flight',
    modelInfo: {
      model_status:      'ready',
      decided_count:     88,
      min_decisions:     30,
      training_size:     78,
      auto_running:      true,
      last_auto_train_at: new Date(Date.now() - 10 * 1000).toISOString(),
      pending_samples:   10,
    },
  },
  {
    label: '8 · Knows your eye (100 – 200)',
    modelInfo: {
      model_status:      'ready',
      decided_count:     155,
      min_decisions:     30,
      training_size:     155,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
      pending_samples:   0,
    },
  },
  {
    label: '9 · Your curator (200 – 500)',
    modelInfo: {
      model_status:      'ready',
      decided_count:     340,
      min_decisions:     30,
      training_size:     340,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      pending_samples:   8,
    },
  },
  {
    label: '10 · Deeply attuned (500+)',
    modelInfo: {
      model_status:      'ready',
      decided_count:     612,
      min_decisions:     30,
      training_size:     612,
      auto_running:      false,
      last_auto_train_at: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      pending_samples:   12,
    },
  },
]

export function BannerStates({ onClose }) {
  return (
    <div className="fixed inset-0 z-[200] bg-[#07080a] overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10">

        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-sm font-semibold text-[#f9f9f9]">PersonalModelBanner — all states</h1>
            <p className="text-xs text-[#6a6b6c] mt-1">Press Shift+B to close · dismiss buttons are disabled here</p>
          </div>
          <button
            onClick={onClose}
            className="text-[#6a6b6c] hover:opacity-70 transition-opacity px-2"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-8">
          {STATES.map(({ label, modelInfo }) => (
            <div key={label}>
              <p className="text-[11px] font-mono text-[#6a6b6c] mb-2">{label}</p>
              <PersonalModelBanner modelInfo={modelInfo} />
            </div>
          ))}
        </div>

      </div>
    </div>
  )
}
