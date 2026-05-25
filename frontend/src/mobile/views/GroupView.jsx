// GroupView — fullscreen group review. Horizontal pager + filmstrip +
// batch action sheet. Reuses PhotoPager for in-photo zoom/pan.

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  RotateCcw, Info, ChevronLeft, ChevronRight, Sparkles, Star, Trash2, HelpCircle,
} from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { PhotoPager } from '../components/PhotoPager'
import { Filmstrip } from '../components/Filmstrip'
import { DecisionBar } from '../components/DecisionBar'
import { BottomSheet } from '../components/BottomSheet'
import { DetailSheet } from './DetailSheet'
import { MobileInfo } from '../components/MobileInfo'
import { useHaptic } from '../hooks/useHaptic'

export function GroupView(props) {
  const { groupsState, setDecision, bulkDecision, undoLast, addToast, back, extra } = props
  const haptic = useHaptic()

  const groups = groupsState.groups
  const targetBestId = extra?.groupBestId

  const groupIdx = useMemo(
    () => groups.findIndex(g => g.best_image_id === targetBestId),
    [groups, targetBestId],
  )
  const group = groupIdx >= 0 ? groups[groupIdx] : null

  const [focusedId, setFocusedId] = useState(group?.best_image_id ?? null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)

  // Keep focusedId valid when group changes underneath us.
  useEffect(() => {
    if (!group) return
    if (!group.images.find(img => img.id === focusedId)) {
      setFocusedId(group.best_image_id)
    }
  }, [group, focusedId])

  if (!group) {
    return (
      <>
        <TopBar title="Group not found" onBack={back} />
        <main className="flex-1 flex items-center justify-center text-[#9c9c9d] px-6 text-center">
          <p>This group is no longer available — it may have dissolved when thresholds changed.</p>
        </main>
      </>
    )
  }

  const focusedIdx = group.images.findIndex(img => img.id === focusedId)
  const focused = focusedIdx >= 0 ? group.images[focusedIdx] : group.images[0]
  const aiPick = group.images.find(img => img.id === group.best_image_id)

  const goPrev = () => {
    if (focusedIdx > 0) setFocusedId(group.images[focusedIdx - 1].id)
  }
  const goNext = () => {
    if (focusedIdx < group.images.length - 1) setFocusedId(group.images[focusedIdx + 1].id)
  }

  const decideFocused = async (decision) => {
    haptic(decision === 'reject' ? 'medium' : 'light')
    await setDecision(focused.id, decision)
  }

  const batchKeepBestRest = async (restDecision) => {
    setBatchOpen(false)
    haptic('success')
    const restIds = group.images.filter(img => img.id !== group.best_image_id).map(img => img.id)
    if (restIds.length === 0) {
      await setDecision(group.best_image_id, 'keep')
      return
    }
    // First the "best" decision, then the rest.
    await setDecision(group.best_image_id, 'keep')
    await bulkDecision(restIds, restDecision)
    addToast({
      type: 'success',
      message: `Kept the AI pick · ${restDecision === 'reject' ? 'Rejected' : 'Marked maybe on'} ${restIds.length} others`,
      duration: 4500,
    })
  }

  return (
    <>
      <TopBar
        title={`Group ${groupIdx + 1} of ${groups.length}`}
        subtitle={`${group.size} photos`}
        onBack={back}
        trailing={
          <button
            type="button"
            onClick={() => undoLast()}
            aria-label="Undo last decision"
            className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#cecece] hover:text-[#f9f9f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]"
          >
            <RotateCcw size={20} aria-hidden="true" />
          </button>
        }
        menu={[
          { label: 'Batch decide…',     icon: Sparkles, onClick: () => setBatchOpen(true) },
          { label: 'Photo details',     icon: Info,     onClick: () => setSheetOpen(true) },
          { label: 'Previous photo',    icon: ChevronLeft,  onClick: goPrev },
          { label: 'Next photo',        icon: ChevronRight, onClick: goNext },
        ]}
      />

      <main
        className="flex-1 flex flex-col"
        style={{ paddingBottom: 'calc(112px + var(--safe-bottom))' }}
      >
        <div className="flex-1 relative">
          <PhotoPager
            image={focused}
            alt={focused?.filename}
            onPrev={goPrev}
            onNext={goNext}
            onSwipeUp={() => setSheetOpen(true)}
            decisionTint={focused?.decision || null}
          />
          {/* AI-pick badge floating top-left */}
          {aiPick && focused?.id === aiPick.id && (
            <span
              className="absolute top-4 left-4 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full bg-black/60 backdrop-blur-md"
              role="img"
              aria-label="AI pick"
            >
              <Star size={14} fill="#E8B84A" stroke="#E8B84A" aria-hidden="true" />
              <span className="text-[12px] font-semibold text-[#F0CD7A]">AI pick</span>
            </span>
          )}
        </div>

        <Filmstrip
          images={group.images}
          currentId={focused?.id}
          aiPickId={aiPick?.id}
          onSelect={setFocusedId}
        />

        {/* Batch action quick row */}
        <div className="px-4 pb-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setBatchOpen(true)}
            className="m-btn m-btn-ghost flex-1"
            aria-label="Open batch decision options"
          >
            <Sparkles size={16} aria-hidden="true" />
            Batch
          </button>
          <MobileInfo
            label="About batch decisions"
            content={
              <p className="text-[13px] leading-snug">
                <strong>Keep best · Reject rest</strong> applies the AI pick as a Keep and sends every other photo in this group to Reject. Use this for redundant burst frames.
              </p>
            }
          >
            <span className="inline-flex items-center justify-center h-11 w-11 rounded-full text-[#9c9c9d]" aria-hidden="true">
              <Info size={18} />
            </span>
          </MobileInfo>
        </div>
      </main>

      <DecisionBar current={focused?.decision} onDecide={decideFocused} disabled={!focused} />

      <BottomSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        ariaLabel={`Details for ${focused?.filename || 'photo'}`}
        initialSnap={1}
      >
        <DetailSheet image={focused} />
      </BottomSheet>

      <BottomSheet
        open={batchOpen}
        onClose={() => setBatchOpen(false)}
        ariaLabel="Batch decisions for this group"
        initialSnap={1}
      >
        <div className="px-5 pb-6">
          <h3 className="m-h2 mb-3">Batch decide</h3>
          <p className="text-[14px] text-[#9c9c9d] mb-4">
            Apply a decision to every photo in this {group.size}-photo group at once. The AI's pick (the warm-amber starred photo) is treated as the keeper.
          </p>
          <div className="space-y-2">
            <BatchAction
              Icon={Trash2}
              tone="reject"
              title="Keep best · Reject rest"
              body={`Keep the AI pick. Send the other ${group.size - 1} to _Trash.`}
              onClick={() => batchKeepBestRest('reject')}
            />
            <BatchAction
              Icon={HelpCircle}
              tone="maybe"
              title="Keep best · Maybe rest"
              body={`Keep the AI pick. Mark the other ${group.size - 1} as Maybe — review later.`}
              onClick={() => batchKeepBestRest('maybe')}
            />
          </div>
        </div>
      </BottomSheet>
    </>
  )
}

function BatchAction({ Icon, tone, title, body, onClick }) {
  const tones = {
    reject: 'border-[rgba(201,123,123,0.40)] hover:bg-[rgba(201,123,123,0.08)]',
    maybe:  'border-[rgba(232,184,74,0.40)] hover:bg-[rgba(232,184,74,0.08)]',
    keep:   'border-[rgba(125,184,154,0.40)] hover:bg-[rgba(125,184,154,0.08)]',
  }
  const iconTones = {
    reject: 'text-[#E8A0A0]',
    maybe:  'text-[#F0CD7A]',
    keep:   'text-[#9DD0B5]',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-start gap-3 p-3 rounded-2xl bg-[#101111] border ${tones[tone]} text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5BB8D4]`}
    >
      <span className={`inline-flex items-center justify-center w-9 h-9 rounded-full bg-white/5 ${iconTones[tone]} flex-shrink-0`}>
        <Icon size={18} aria-hidden="true" />
      </span>
      <div className="flex-1">
        <p className="text-[15px] font-semibold text-[#f9f9f9]">{title}</p>
        <p className="text-[13px] text-[#9c9c9d] mt-0.5 leading-snug">{body}</p>
      </div>
    </button>
  )
}
