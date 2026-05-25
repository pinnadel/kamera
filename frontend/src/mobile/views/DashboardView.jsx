// DashboardView — at-a-glance stats for the active folder.
//
// Mobile-native layout: hero banner at top (model status), three "metric"
// cards below (decisions, agreement, top features), then optional rule
// breakdown. Auto-pulls /model-card if available.

import { useEffect, useMemo, useState } from 'react'
import {
  Sparkles, Camera, ListChecks, TrendingUp, Award, ChevronRight,
} from 'lucide-react'
import { API } from '../../api'
import { TopBar } from '../components/TopBar'
import { BottomNav } from '../components/BottomNav'
import { ModelBanner } from '../components/ModelBanner'
import { EmptyState } from '../components/EmptyState'
import { MobileInfo } from '../components/MobileInfo'

export function DashboardView({ activeFolder, images, modelState, goTo }) {
  const [modelCard, setModelCard] = useState(null)

  useEffect(() => {
    fetch(`${API}/model-card`).then(r => r.ok ? r.json() : null).then(setModelCard).catch(() => {})
  }, [modelState?.info])

  const stats = useMemo(() => computeStats(images), [images])

  if (!activeFolder) {
    return (
      <>
        <TopBar title="Dashboard" />
        <main className="flex-1 flex flex-col">
          <EmptyState
            icon={TrendingUp}
            title="Pick a folder to see stats"
            body="Dashboard summarises decisions, agreement with the AI, and what your taste model is learning."
            action={
              <button type="button" className="m-btn m-btn-primary" onClick={() => goTo('pickFolder')}>
                Choose folder
              </button>
            }
          />
        </main>
        <BottomNav active="dashboard" onSelect={goTo} />
      </>
    )
  }

  const decided = stats.keep + stats.maybe + stats.reject
  const total = stats.keep + stats.maybe + stats.reject + stats.undecided
  const decidedPct = total ? Math.round((decided / total) * 100) : 0

  return (
    <>
      <TopBar title="Dashboard" subtitle="This folder" />

      <main
        className="flex-1 overflow-y-auto"
        style={{ paddingBottom: 'calc(var(--m-bottomnav-h) + var(--safe-bottom) + 12px)' }}
      >
        {/* Personal model banner — opens model status sheet */}
        <div className="px-4 pt-4">
          <ModelBanner info={modelState?.info} onOpen={() => goTo('modelStatus')} />
        </div>

        {/* Progress hero */}
        <section className="mt-4 px-4">
          <div className="rounded-3xl p-4 bg-gradient-to-br from-[#0e1518] to-[#101111] border border-white/5">
            <p className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d] mb-1">Progress</p>
            <div className="flex items-end gap-3">
              <span className="text-[42px] font-mono font-semibold leading-none m-tabular text-[#f9f9f9]">
                {decidedPct}%
              </span>
              <span className="text-[13px] text-[#9c9c9d] mb-1">{decided} of {total} decided</span>
            </div>
            <div className="mt-3 h-2 rounded-full bg-white/5 overflow-hidden flex">
              <span style={{ width: `${pct(stats.keep, total)}%`, background: 'rgba(125,184,154,0.85)' }} aria-hidden="true" />
              <span style={{ width: `${pct(stats.maybe, total)}%`, background: 'rgba(232,184,74,0.85)'  }} aria-hidden="true" />
              <span style={{ width: `${pct(stats.reject, total)}%`, background: 'rgba(201,123,123,0.85)' }} aria-hidden="true" />
            </div>
            <div className="mt-3 grid grid-cols-3 text-center text-[12px] font-medium">
              <div><span className="text-[#9DD0B5]">{stats.keep}</span><br/><span className="text-[#9c9c9d]">Keep</span></div>
              <div><span className="text-[#F0CD7A]">{stats.maybe}</span><br/><span className="text-[#9c9c9d]">Maybe</span></div>
              <div><span className="text-[#E8A0A0]">{stats.reject}</span><br/><span className="text-[#9c9c9d]">Reject</span></div>
            </div>
          </div>
        </section>

        {/* Metric cards */}
        <section className="mt-3 px-4 grid grid-cols-2 gap-3">
          <MetricCard
            Icon={Award}
            title="Top score"
            value={stats.maxOverall != null ? Math.round(stats.maxOverall) : '—'}
            sub={stats.topImage ? stats.topImage.filename : ''}
          />
          <MetricCard
            Icon={ListChecks}
            title="Median overall"
            value={stats.medianOverall != null ? Math.round(stats.medianOverall) : '—'}
            sub="Across all photos"
          />
          <MetricCard
            Icon={Camera}
            title="Cameras"
            value={stats.cameras}
            sub={stats.cameraNames.slice(0, 2).join(' · ') || ''}
          />
          <MetricCard
            Icon={Sparkles}
            title="With faces"
            value={stats.withFaces}
            sub={`${pct(stats.withFaces, total)}%`}
          />
        </section>

        {/* Top features (if model card available) */}
        {modelCard?.top_features?.length > 0 && (
          <section className="mt-4 px-4">
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d]">Your model values</h2>
              <MobileInfo
                icon
                label="About top features"
                content={
                  <p className="text-[13px] leading-snug">
                    The features that move your personal score the most. The model learned these from your past Keep / Maybe / Reject decisions.
                  </p>
                }
              />
            </div>
            <div className="rounded-2xl bg-[#101111] border border-white/5 overflow-hidden">
              {modelCard.top_features.map((f, i) => (
                <div key={i} className="flex items-center gap-3 px-3 py-3 border-b border-white/5 last:border-0">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-[rgba(123,130,201,0.12)] text-[#7B82C9] text-[13px] font-mono font-bold m-tabular">
                    {i + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[14px] font-medium text-[#f9f9f9] truncate">{prettyFeature(f.name || f.feature || '')}</p>
                    <div className="mt-1 h-1 rounded-full bg-white/5 overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min(100, Math.abs(f.importance || f.weight || 0) * 100)}%`,
                          background: 'linear-gradient(90deg, #5BB8D4, #7B82C9)',
                        }}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Quick actions */}
        <section className="mt-4 px-4 mb-2">
          <div className="rounded-2xl bg-[#101111] border border-white/5 overflow-hidden">
            <Action onClick={() => goTo('autoCull')}    Icon={Sparkles} label="Run auto-cull" />
            <Action onClick={() => goTo('train')}       Icon={TrendingUp} label="Open training queue" />
            <Action onClick={() => goTo('modelStatus')} Icon={Award} label="Personal model details" />
          </div>
        </section>
      </main>

      <BottomNav active="dashboard" onSelect={goTo} />
    </>
  )
}

function pct(n, total) {
  if (!total) return 0
  return Math.round((n / total) * 100)
}

function computeStats(images) {
  const stats = {
    keep: 0, maybe: 0, reject: 0, undecided: 0,
    cameraNames: [],
    withFaces: 0,
    cameras: 0,
    maxOverall: null,
    medianOverall: null,
    topImage: null,
  }
  if (!images?.length) return stats
  const cameras = new Set()
  const overalls = []
  for (const img of images) {
    if (img.decision === 'keep')   stats.keep++
    else if (img.decision === 'maybe')  stats.maybe++
    else if (img.decision === 'reject') stats.reject++
    else stats.undecided++
    if (img.face_detected) stats.withFaces++
    if (img.camera_model) cameras.add(img.camera_model)
    if (img.overall_score != null) overalls.push(img.overall_score)
    if (img.overall_score != null && (stats.maxOverall == null || img.overall_score > stats.maxOverall)) {
      stats.maxOverall = img.overall_score
      stats.topImage = img
    }
  }
  stats.cameras = cameras.size
  stats.cameraNames = Array.from(cameras)
  if (overalls.length) {
    overalls.sort((a, b) => a - b)
    stats.medianOverall = overalls[Math.floor(overalls.length / 2)]
  }
  return stats
}

function prettyFeature(name) {
  return name
    .replace(/_/g, ' ')
    .replace(/\bscore\b/i, '')
    .replace(/\bratio\b/i, '')
    .trim()
    .replace(/^\w/, c => c.toUpperCase())
}

function MetricCard({ Icon, title, value, sub }) {
  return (
    <div className="rounded-2xl p-3 bg-[#101111] border border-white/5">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} className="text-[#5BB8D4]" aria-hidden="true" />
        <span className="text-[11px] uppercase tracking-wide font-semibold text-[#9c9c9d]">{title}</span>
      </div>
      <p className="text-[24px] font-mono font-semibold leading-none m-tabular text-[#f9f9f9]">
        {value}
      </p>
      {sub && (
        <p className="mt-1 text-[12px] text-[#9c9c9d] truncate" title={sub}>{sub}</p>
      )}
    </div>
  )
}

function Action({ Icon, label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-3 w-full text-left h-14 px-3 border-b border-white/5 last:border-0 hover:bg-white/5 focus-visible:outline-none focus-visible:bg-white/5"
    >
      <span className="inline-flex items-center justify-center w-9 h-9 rounded-full bg-[rgba(91,184,212,0.10)]" aria-hidden="true">
        <Icon size={16} className="text-[#5BB8D4]" />
      </span>
      <span className="flex-1 text-[15px] font-medium text-[#f9f9f9]">{label}</span>
      <ChevronRight size={16} className="text-[#9c9c9d]" aria-hidden="true" />
    </button>
  )
}
