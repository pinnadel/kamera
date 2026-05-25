// BrowseView — primary "Browse" / "Groups" tab. Renders a responsive grid
// of photos (mode='photos') or group tiles (mode='groups'). Persistent shell:
// TopBar (folder name + kebab), BottomNav, and an Auto-cull FAB.

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Folder, FolderOpen, Sparkles, Settings as SettingsIcon,
  Search, X,
} from 'lucide-react'
import { API } from '../../api'
import { compareImages, getMetricLabel } from '../../sortMetrics'
import { TopBar }     from '../components/TopBar'
import { BottomNav }  from '../components/BottomNav'
import { ModelBanner } from '../components/ModelBanner'
import { EmptyState } from '../components/EmptyState'
import { SegmentedControl } from '../components/SegmentedControl'
import { MobileInfo } from '../components/MobileInfo'

function pathTail(path) {
  if (!path) return null
  const segs = path.split('/').filter(Boolean)
  return segs[segs.length - 1] || path
}

const LS_SORT = 'pca.sort'
function readSort() {
  try {
    const raw = localStorage.getItem(LS_SORT)
    if (!raw) return { field: 'shot_at', dir: 'desc' }
    const parsed = JSON.parse(raw)
    return { field: parsed.field || 'shot_at', dir: parsed.dir || 'desc' }
  } catch { return { field: 'shot_at', dir: 'desc' } }
}

export function BrowseView(props) {
  const {
    mode = 'photos',
    activeFolder, setActiveFolder, folders, pickFolder,
    images, imagesLoading,
    groupsState, modelState,
    addToast, goTo, back,
  } = props

  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const sort = readSort()

  // Filter + sort photos
  const visibleImages = useMemo(() => {
    if (!images) return []
    const q = search.trim().toLowerCase()
    let list = q
      ? images.filter(img => (img.filename || '').toLowerCase().includes(q))
      : images.slice()
    list.sort((a, b) => compareImages(a, b, sort.field, sort.dir))
    return list
  }, [images, search, sort.field, sort.dir])

  const decisionCounts = useMemo(() => {
    if (!images) return { keep: 0, maybe: 0, reject: 0, undecided: 0 }
    const c = { keep: 0, maybe: 0, reject: 0, undecided: 0 }
    for (const img of images) {
      if (img.decision === 'keep')   c.keep++
      else if (img.decision === 'maybe')  c.maybe++
      else if (img.decision === 'reject') c.reject++
      else c.undecided++
    }
    return c
  }, [images])

  const onPickFolder = async () => {
    const path = await pickFolder()
    if (path) {
      setActiveFolder(path)
      // Trigger an analysis with default settings.
      try {
        const res = await fetch(`${API}/analyze-folder`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_folder: path, watch_live: false }),
        })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          addToast({ type: 'error', message: body.detail || `Couldn't start analysis (${res.status})`, duration: 6000 })
          return
        }
        addToast({ type: 'info', message: `Analyzing ${pathTail(path)}…`, duration: 4000 })
      } catch (err) {
        addToast({ type: 'error', message: `Network error: ${err.message}`, duration: 6000 })
      }
    }
  }

  const folderTail = pathTail(activeFolder) || 'No folder'

  const menu = [
    { label: 'Auto-cull',     icon: Sparkles,     onClick: () => goTo('autoCull') },
    { label: 'Settings',      icon: SettingsIcon, onClick: () => goTo('settings') },
    { label: 'Switch folder', icon: FolderOpen,   onClick: () => goTo('pickFolder') },
  ]

  return (
    <>
      <TopBar
        title={folderTail}
        subtitle={mode === 'groups' ? 'Groups' : 'Browse'}
        trailing={
          <button
            type="button"
            onClick={() => setSearchOpen(o => !o)}
            aria-label={searchOpen ? 'Close search' : 'Search photos by filename'}
            aria-expanded={searchOpen}
            className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
          >
            {searchOpen ? <X size={20} /> : <Search size={20} />}
          </button>
        }
        menu={menu}
      />

      {searchOpen && (
        <div className="px-4 py-2 m-blur-surface border-b border-white/5">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9c9c9d]" aria-hidden="true" />
            <input
              type="search"
              autoFocus
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search by filename"
              aria-label="Search photos by filename"
              className="m-input pl-10"
            />
          </div>
        </div>
      )}

      {/* Main content area */}
      <main
        className="flex-1 overflow-y-auto"
        style={{
          paddingTop: 4,
          paddingBottom: 'calc(var(--m-bottomnav-h) + var(--safe-bottom) + 12px)',
        }}
      >
        {!activeFolder ? (
          <EmptyState
            icon={Folder}
            title="Pick a folder to begin"
            body="KaMeRa reads RAW + JPEG files in place. Decisions move them to _Keeps / _Maybes / _Trash subfolders."
            action={
              <button type="button" className="m-btn m-btn-primary" onClick={onPickFolder}>
                <FolderOpen size={18} aria-hidden="true" />
                Choose folder
              </button>
            }
          />
        ) : (
          <>
            {modelState?.info && (
              <div className="px-4 pt-3">
                <ModelBanner info={modelState.info} onOpen={() => goTo('modelStatus')} />
              </div>
            )}

            {/* Decision summary bar — quick-glance counts */}
            <div className="px-4 pt-3">
              <div className="grid grid-cols-4 gap-2 text-center">
                <SummaryChip label="Keep"      count={decisionCounts.keep}      tint="keep"   />
                <SummaryChip label="Maybe"     count={decisionCounts.maybe}     tint="maybe"  />
                <SummaryChip label="Reject"    count={decisionCounts.reject}    tint="reject" />
                <SummaryChip label="Undecided" count={decisionCounts.undecided} tint="neutral" />
              </div>
            </div>

            {mode === 'groups' ? (
              <GroupsGrid
                groups={groupsState.groups}
                loading={groupsState.loading}
                groupMode={groupsState.mode}
                setGroupMode={groupsState.setMode}
                onOpen={(group) => goTo('group', { groupBestId: group.best_image_id })}
              />
            ) : (
              <PhotosGrid
                images={visibleImages}
                loading={imagesLoading}
                searchQuery={search}
                onTap={(image) => goTo('cull', { selectedId: image.id })}
              />
            )}
          </>
        )}
      </main>

      {/* Auto-cull FAB — only on photos mode + folder loaded */}
      {activeFolder && mode === 'photos' && (
        <button
          type="button"
          onClick={() => goTo('autoCull')}
          aria-label="Auto-cull"
          className="fixed right-4 z-30 h-14 w-14 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] focus-visible:ring-offset-2 focus-visible:ring-offset-[#07080a]"
          style={{
            bottom: 'calc(var(--m-bottomnav-h) + var(--safe-bottom) + 16px)',
            background: 'linear-gradient(180deg, #6FC9DF 0%, #4FA8C4 100%)',
            color: '#07080a',
            boxShadow: '0 10px 28px rgba(91,184,212,0.45), 0 0 0 1px rgba(91,184,212,0.55)',
          }}
        >
          <Sparkles size={22} strokeWidth={2.4} className="m-auto" aria-hidden="true" />
        </button>
      )}

      <BottomNav active={mode === 'groups' ? 'groups' : 'browse'} onSelect={goTo} />
    </>
  )
}

function SummaryChip({ label, count, tint }) {
  const tints = {
    keep:    { bg: 'rgba(125,184,154,0.12)', border: 'rgba(125,184,154,0.30)', text: '#9DD0B5' },
    maybe:   { bg: 'rgba(232,184,74,0.12)',  border: 'rgba(232,184,74,0.30)',  text: '#F0CD7A' },
    reject:  { bg: 'rgba(201,123,123,0.12)', border: 'rgba(201,123,123,0.30)', text: '#E8A0A0' },
    neutral: { bg: 'rgba(255,255,255,0.04)', border: 'rgba(255,255,255,0.06)', text: '#cecece' },
  }
  const t = tints[tint] || tints.neutral
  return (
    <div
      className="rounded-xl py-2 px-2 border"
      style={{ backgroundColor: t.bg, borderColor: t.border }}
    >
      <p className="text-[18px] font-mono font-semibold m-tabular leading-none" style={{ color: t.text }}>
        {count}
      </p>
      <p className="text-[10px] uppercase tracking-wide font-medium mt-0.5" style={{ color: t.text, opacity: 0.78 }}>
        {label}
      </p>
    </div>
  )
}

function PhotosGrid({ images, loading, searchQuery, onTap }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 px-4 mt-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="m-skeleton shimmer" />
        ))}
      </div>
    )
  }
  if (!images.length) {
    return (
      <div className="px-4 mt-8 text-center">
        <p className="text-[#9c9c9d]">{searchQuery ? 'No matches.' : 'No photos analyzed yet.'}</p>
      </div>
    )
  }
  return (
    <div
      className="grid grid-cols-2 sm:grid-cols-3 gap-2 px-4 mt-3"
      role="grid"
      aria-label="Photos grid"
    >
      {images.map(img => (
        <PhotoTile key={img.id} image={img} onTap={() => onTap(img)} />
      ))}
    </div>
  )
}

function PhotoTile({ image, onTap }) {
  const ring =
    image.decision === 'keep'   ? 'ring-1 ring-[rgba(125,184,154,0.55)]' :
    image.decision === 'maybe'  ? 'ring-1 ring-[rgba(232,184,74,0.55)]'  :
    image.decision === 'reject' ? 'ring-1 ring-[rgba(201,123,123,0.55)]' :
                                  'ring-1 ring-white/5'
  return (
    <button
      type="button"
      onClick={onTap}
      className={`relative aspect-square overflow-hidden rounded-xl bg-[#161718] ${ring} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4] ${image.decision === 'reject' ? 'opacity-75' : ''}`}
      aria-label={`Open ${image.filename || 'photo'}${image.decision ? ` (${image.decision})` : ''}`}
    >
      <img
        src={`${API}/previews/${image.id}`}
        alt=""
        loading="lazy"
        className="absolute inset-0 w-full h-full object-cover"
        draggable={false}
      />
      {image.decision && (
        <span
          aria-hidden="true"
          className={`absolute top-1.5 right-1.5 w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold ${
            image.decision === 'keep'   ? 'bg-[rgba(125,184,154,0.92)] text-[#07080a]' :
            image.decision === 'maybe'  ? 'bg-[rgba(232,184,74,0.92)]  text-[#07080a]' :
                                          'bg-[rgba(201,123,123,0.92)] text-[#07080a]'
          }`}
        >
          {image.decision === 'keep' ? 'K' : image.decision === 'maybe' ? 'M' : 'R'}
        </span>
      )}
      {image.overall_score != null && (
        <span
          aria-hidden="true"
          className="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold m-tabular text-[#f9f9f9] bg-black/55 backdrop-blur-sm"
        >
          {Math.round(image.overall_score)}
        </span>
      )}
    </button>
  )
}

function GroupsGrid({ groups, loading, groupMode, setGroupMode, onOpen }) {
  return (
    <>
      <div className="px-4 pt-3 flex items-center justify-between">
        <SegmentedControl
          label="Group by"
          value={groupMode}
          onChange={setGroupMode}
          options={[
            { value: 'bursts', label: 'Bursts' },
            { value: 'people', label: 'People' },
          ]}
        />
        <MobileInfo
          icon
          label="About grouping"
          content={
            <div className="space-y-2">
              <p><strong>Bursts</strong> — photos taken close together that look alike (SigLIP visual similarity within a 2-minute window).</p>
              <p><strong>People</strong> — photos containing the same recognizable face (FaceNet identity embedding).</p>
            </div>
          }
        />
      </div>

      {loading ? (
        <div className="grid grid-cols-2 gap-2 px-4 mt-3">
          {Array.from({ length: 4 }).map((_, i) => <div key={i} className="m-skeleton shimmer" />)}
        </div>
      ) : !groups.length ? (
        <div className="px-4 mt-8 text-center">
          <p className="text-[#9c9c9d]">No groups detected.</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 px-4 mt-3">
          {groups.map(g => (
            <GroupTile key={g.best_image_id} group={g} onOpen={() => onOpen(g)} />
          ))}
        </div>
      )}
    </>
  )
}

function GroupTile({ group, onOpen }) {
  const counts = group.images.reduce((acc, img) => {
    if (img.decision === 'keep') acc.k++
    else if (img.decision === 'maybe') acc.m++
    else if (img.decision === 'reject') acc.r++
    return acc
  }, { k: 0, m: 0, r: 0 })

  return (
    <button
      type="button"
      onClick={onOpen}
      className="relative aspect-square overflow-hidden rounded-xl bg-[#161718] ring-1 ring-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
      aria-label={`Group of ${group.size} photos`}
    >
      <img
        src={`${API}/previews/${group.best_image_id}`}
        alt=""
        loading="lazy"
        className="absolute inset-0 w-full h-full object-cover"
        draggable={false}
      />
      {/* Stacked-paper edge */}
      <span aria-hidden="true" className="absolute inset-x-2 bottom-0 h-[3px] rounded-b-md bg-white/10" />
      <span aria-hidden="true" className="absolute inset-x-3 -bottom-[1px] h-[2px] rounded-b-md bg-white/5" />

      <span className="absolute top-1.5 right-1.5 px-2 h-6 inline-flex items-center justify-center rounded-full bg-black/60 backdrop-blur-sm text-[12px] font-mono font-semibold m-tabular text-[#f9f9f9]">
        {group.size}
      </span>

      <div className="absolute bottom-1.5 left-1.5 right-1.5 flex items-center gap-1 text-[10px] font-mono">
        {counts.k > 0 && <span className="px-1.5 py-0.5 rounded bg-[rgba(125,184,154,0.85)] text-[#07080a]">K {counts.k}</span>}
        {counts.m > 0 && <span className="px-1.5 py-0.5 rounded bg-[rgba(232,184,74,0.85)]  text-[#07080a]">M {counts.m}</span>}
        {counts.r > 0 && <span className="px-1.5 py-0.5 rounded bg-[rgba(201,123,123,0.85)] text-[#07080a]">R {counts.r}</span>}
      </div>
    </button>
  )
}
