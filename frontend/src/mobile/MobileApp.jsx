// MobileApp — root state machine for the mobile bundle.
//
// Navigation is route-less but persistent: bottom nav owns 4 destinations
// (Browse, Groups, Train, Dashboard); other screens (Cull, Group, AutoCull,
// Settings, ModelStatus, PickFolder) are pushed on top via `goTo(view, extra)`
// and popped via `back()`.
//
// Active folder + decisions persist via the same SQLite backing as desktop —
// closing the tab and reopening lands the user back on the last folder.

import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import { useImages }       from './data/useImages'
import { useFolders }      from './data/useFolders'
import { useGroups }       from './data/useGroups'
import { useToasts }       from './data/useToasts'
import { useModelInfo }    from './data/useModelInfo'
import { ToastStack }      from './components/ToastStack'
import { BrowseView }      from './views/BrowseView'
import { CullView }        from './views/CullView'
import { GroupView }       from './views/GroupView'
import { TrainView }       from './views/TrainView'
import { AutoCullView }    from './views/AutoCullView'
import { SettingsView }    from './views/SettingsView'
import { DashboardView }   from './views/DashboardView'
import { ModelStatusView } from './views/ModelStatusView'
import { PickFolderView }  from './views/PickFolderView'

const LS_FOLDER = 'pca.activeFolderPath'

export function MobileApp() {
  // Active folder
  const [activeFolder, setActiveFolderState] = useState(
    () => (typeof localStorage !== 'undefined' && localStorage.getItem(LS_FOLDER)) || null
  )
  const setActiveFolder = useCallback((p) => {
    setActiveFolderState(p)
    try { p ? localStorage.setItem(LS_FOLDER, p) : localStorage.removeItem(LS_FOLDER) } catch {}
  }, [])

  // Navigation state. `extra` is per-view (e.g. selectedId for cull).
  const [stack, setStack] = useState([{ view: 'browse', extra: {} }])
  const top = stack[stack.length - 1]

  const goTo = useCallback((view, extra = {}) => {
    setStack(s => {
      // If targeting a primary tab, replace the stack entirely (so the
      // bottom nav behaves like a tab bar, not a back stack).
      if (['browse', 'groups', 'train', 'dashboard'].includes(view)) {
        return [{ view, extra }]
      }
      return [...s, { view, extra }]
    })
  }, [])

  const back = useCallback(() => {
    setStack(s => s.length > 1 ? s.slice(0, -1) : s)
  }, [])

  // Mirror current view onto <html> so portal-rendered chrome (Toasts,
  // BottomSheet) can read --m-bottom-chrome via the CSS cascade.
  useLayoutEffect(() => {
    document.documentElement.dataset.mobileView = top.view
    return () => { delete document.documentElement.dataset.mobileView }
  }, [top.view])

  // Data hooks
  const { images, loading, reload, setDecision, bulkDecision, undoLast, lastDecision, clearLastDecision } = useImages(activeFolder)
  const { folders, reload: reloadFolders, pickFolder } = useFolders()
  const groupsState = useGroups({ images })
  const modelState  = useModelInfo()
  const { toasts, addToast, dismiss } = useToasts()

  // Pop the post-decision toast as an undo affordance whenever a decision lands.
  useEffect(() => {
    if (!lastDecision) return
    const id = addToast({
      type: 'undo',
      message: lastDecision.bulk
        ? `Decided ${lastDecision.ids.length} photos`
        : `Decision saved`,
      duration: 5000,
      action: {
        label: 'Undo',
        onClick: () => undoLast(),
      },
    })
    return () => { dismiss(id); clearLastDecision() }
    // We deliberately omit dismiss/clear from deps — they're stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastDecision])

  // Helper: sequenced cull list for prev/next nav inside CullView. The
  // ordering follows the user's current sort (read live from localStorage
  // 'pca.sort'); fall back to the natural list order.
  const cullOrder = images

  const sharedProps = {
    activeFolder, setActiveFolder,
    folders, reloadFolders, pickFolder,
    images, imagesLoading: loading, reloadImages: reload,
    setDecision, bulkDecision, undoLast,
    groupsState, modelState,
    addToast,
    cullOrder,
    goTo, back,
  }

  return (
    <div
      className="min-h-screen flex flex-col bg-[var(--color-canvas)]"
      data-mobile-view={top.view}
    >
      {top.view === 'browse' && (
        <BrowseView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'groups' && (
        <BrowseView mode="groups" {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'train' && (
        <TrainView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'dashboard' && (
        <DashboardView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'cull' && (
        <CullView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'group' && (
        <GroupView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'autoCull' && (
        <AutoCullView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'settings' && (
        <SettingsView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'modelStatus' && (
        <ModelStatusView {...sharedProps} extra={top.extra} />
      )}
      {top.view === 'pickFolder' && (
        <PickFolderView {...sharedProps} extra={top.extra} />
      )}

      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </div>
  )
}
