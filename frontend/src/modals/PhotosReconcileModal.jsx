import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, RefreshCw, X } from 'lucide-react'
import { API } from '../api'
import { BTN_ICON, BTN_PRIMARY, BTN_PRIMARY_DANGER, BTN_SECONDARY } from '../ui/buttons'
import { DecisionWord } from '../ui/primitives'

// PhotosReconcileModal — show what disagrees between KaMeRa and Photos, and
// let the user pick a direction per kind of disagreement.
//
// Why per class and not one "sync" button: Photos and KaMeRa disagree for good
// reasons in BOTH directions. A keyword edited by hand in Photos is a real
// decision; so is one made here. A single direction would be wrong half the
// time, silently. So the diff is shown, and only the one class where the two
// sides don't actually disagree — a decision whose keyword never landed —
// defaults to being fixed.

// Copy for each mismatch class: what it means, and what each action does.
// Written from the user's side ("you decided", "Photos says"), because the
// class names are implementation words and the choice is not.
const CLASS_COPY = {
  keyword_missing: {
    title: 'Decided here, unmarked in Photos',
    body: 'You made the call in KaMeRa, but the photo carries no KaMeRa keyword — the write never landed, or the keyword was deleted in Photos.',
    actions: {
      apply: { label: 'Write the keyword', hint: 'Re-apply your decision to Photos' },
      clear: { label: 'Drop my decision', hint: 'Treat Photos as the record and undecide it here' },
      skip:  { label: 'Leave alone',      hint: 'Change nothing' },
    },
  },
  keyword_foreign: {
    title: 'The two disagree',
    body: 'KaMeRa and Photos each carry a decision for this photo, and they are not the same one. Either side may be right — this is the one case worth looking at photo by photo.',
    actions: {
      skip:   { label: 'Leave alone',   hint: 'Decide later' },
      kamera: { label: 'KaMeRa wins',   hint: 'Overwrite the keyword in Photos' },
      photos: { label: 'Photos wins',   hint: 'Adopt the keyword as your decision here' },
    },
  },
  decision_missing: {
    title: 'Decided in Photos only',
    body: 'The photo carries a KaMeRa keyword but has no decision here — you set it directly in Photos, or an undo here did not reach the library.',
    actions: {
      skip:   { label: 'Leave alone',      hint: 'Change nothing' },
      import: { label: 'Import as mine',   hint: 'Record the keyword as a decision in KaMeRa' },
      clear:  { label: 'Remove the keyword', hint: 'Strip the marker in Photos' },
    },
  },
  not_in_album: {
    title: 'No longer in this album',
    body: 'The photo still exists in your library but has left the album this tab tracks. Its decision here no longer has anywhere to point.',
    actions: {
      skip: { label: 'Keep the row', hint: 'It may come back' },
      drop: { label: 'Remove from KaMeRa', hint: 'Delete the row — the photo itself is untouched' },
    },
  },
  asset_gone: {
    title: 'Gone from the library',
    body: 'The photo no longer exists in this Photos library at all. Nothing can be written to it again.',
    actions: {
      skip: { label: 'Keep the row', hint: 'Leave the stale row in place' },
      drop: { label: 'Remove from KaMeRa', hint: 'Delete the row' },
    },
  },
}

// Actions that delete an images row. Used to tint the confirm button and to
// say the word "remove" out loud in the summary — a reconcile that only
// rewrites keywords and one that deletes rows should not look identical.
const DESTRUCTIVE = new Set(['drop'])

function ClassRow({ name, entries, choices, chosen, onChoose }) {
  const copy = CLASS_COPY[name]
  const [showAll, setShowAll] = useState(false)
  if (!copy || entries.length === 0) return null

  const preview = showAll ? entries : entries.slice(0, 4)

  return (
    <div className="border border-[rgba(255,255,255,0.07)] rounded-lg p-3 mb-2.5">
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span className="text-sm text-[#f0f0f0] font-medium">{copy.title}</span>
        <span className="text-xs font-mono text-[#8a8a8a] tabular-nums shrink-0">{entries.length}</span>
      </div>
      <p className="text-[11px] text-[#9c9c9d] leading-relaxed mb-2.5">{copy.body}</p>

      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        {choices.map(action => {
          const a = copy.actions[action]
          if (!a) return null
          const active = chosen === action
          return (
            <button
              key={action}
              onClick={() => onChoose(name, action)}
              title={a.hint}
              className={`px-2.5 py-1 rounded-md border text-[11px] transition-colors ${
                active
                  ? (DESTRUCTIVE.has(action)
                      ? 'border-[rgba(201,123,123,0.55)] bg-[rgba(201,123,123,0.12)] text-[#C97B7B] font-medium'
                      : 'border-[rgba(91,184,212,0.55)] bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] font-medium')
                  : 'border-[rgba(255,255,255,0.10)] text-[#9c9c9d] hover:opacity-70'
              }`}
            >
              {a.label}
            </button>
          )
        })}
      </div>
      <p className="text-[11px] text-[#6a6b6c] mb-2">{copy.actions[chosen]?.hint}</p>

      <ul className="space-y-0.5">
        {preview.map(e => (
          <li key={e.image_id} className="flex items-center gap-2 text-[11px] text-[#8a8a8a]">
            <span className="font-mono truncate">{e.filename}</span>
            {e.decision && <DecisionWord kind={e.decision}>{e.decision}</DecisionWord>}
            {e.keyword && <span className="text-[#6a6b6c]">· {e.keyword}</span>}
            {e.ambiguous && (
              <span className="text-[#E8B84A]" title="Carries more than one KaMeRa keyword">
                · ambiguous
              </span>
            )}
          </li>
        ))}
        {entries.length > preview.length && (
          <li>
            <button onClick={() => setShowAll(true)} className="text-[11px] text-[#5BB8D4] hover:opacity-70">
              Show {entries.length - preview.length} more
            </button>
          </li>
        )}
      </ul>
    </div>
  )
}

export function PhotosReconcileModal({ sourceFolder, onClose, onComplete, onToast }) {
  const [diff, setDiff] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [actions, setActions] = useState({})

  // `attempt` is the retry handle: the effect owns the fetch and only ever
  // sets state from its async callbacks, so nothing updates synchronously
  // during render. The Try-again button bumps it (and resets the spinner from
  // an event handler, where a synchronous setState is fine).
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/photos-album/diff?source_folder=${encodeURIComponent(sourceFolder)}`)
      .then(async r => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`)
        return r.json()
      })
      .then(data => {
        if (cancelled) return
        setDiff(data)
        setActions(data.default_actions || {})
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sourceFolder, attempt])

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    setAttempt(n => n + 1)
  }, [])

  async function run() {
    setRunning(true)
    try {
      const res = await fetch(`${API}/photos-album/reconcile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_folder: sourceFolder, actions }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)

      const changed = Object.values(data.applied || {}).reduce((a, b) => a + b, 0)
      const failed = (data.errors || []).length
      if (failed > 0) {
        onToast?.({
          type: 'warning',
          message: `${changed} reconciled · ${failed} could not be applied`,
        })
      } else {
        onToast?.({ type: 'success', message: `${changed} photo${changed === 1 ? '' : 's'} reconciled` })
      }
      onComplete?.(data)
      onClose()
    } catch (e) {
      onToast?.({ type: 'error', message: `Reconcile failed: ${e.message}` })
    } finally {
      setRunning(false)
    }
  }

  const classes = diff?.classes || {}
  const pending = Object.entries(actions).filter(
    ([name, action]) => action !== 'skip' && (classes[name]?.length ?? 0) > 0
  )
  const pendingCount = pending.reduce((n, [name]) => n + classes[name].length, 0)
  const removing = pending
    .filter(([, action]) => DESTRUCTIVE.has(action))
    .reduce((n, [name]) => n + classes[name].length, 0)

  return (
    <div className="fixed inset-0 z-50 bg-[rgba(7,8,10,0.80)] flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl w-[34rem] max-h-[85vh] flex flex-col shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)]"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-6 pb-4">
          <div>
            <h2 className="text-[#f9f9f9] font-semibold text-base">Compare with Photos</h2>
            {diff && (
              <p className="text-[11px] text-[#6a6b6c] mt-0.5">
                {diff.album_name} · {diff.library_name}
              </p>
            )}
          </div>
          <button onClick={onClose} className={BTN_ICON} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="px-6 overflow-y-auto flex-1">
          {loading && (
            <div className="text-center py-10">
              <p className="text-[#9c9c9d] text-sm">Reading the library catalogue…</p>
              {/* Honesty about the wait: this re-parses the whole catalogue on
                  purpose, because a cached read would report yesterday's drift. */}
              <p className="text-[11px] text-[#6a6b6c] mt-1">
                Re-read in full so hand-made edits in Photos show up. Takes a moment on a large library
              </p>
            </div>
          )}

          {!loading && error && (
            <div className="text-center py-8">
              <p className="text-sm text-[#C97B7B]">{error}</p>
              <button onClick={load} className={`${BTN_SECONDARY} mt-3`}>
                <RefreshCw size={16} /> Try again
              </button>
            </div>
          )}

          {!loading && diff && diff.drift === 0 && (
            <div className="text-center py-10">
              <Check size={22} className="text-[#7DB89A] mx-auto mb-2" />
              <p className="text-sm text-[#f0f0f0]">Everything agrees</p>
              <p className="text-[11px] text-[#6a6b6c] mt-1">
                {diff.checked} photo{diff.checked === 1 ? '' : 's'} checked, {diff.in_sync} in sync
              </p>
            </div>
          )}

          {!loading && diff && diff.drift > 0 && (
            <>
              <p className="text-xs text-[#9c9c9d] mb-3 leading-relaxed">
                {diff.drift} of {diff.checked} photos disagree. Pick a direction per kind — only the
                first is preselected, because it is the only one where the two sides do not actually
                contradict each other.
              </p>
              {Object.keys(CLASS_COPY).map(name => (
                <ClassRow
                  key={name}
                  name={name}
                  entries={classes[name] || []}
                  choices={diff.actions?.[name] || []}
                  chosen={actions[name]}
                  onChoose={(n, a) => setActions(prev => ({ ...prev, [n]: a }))}
                />
              ))}
            </>
          )}

          {!loading && diff && diff.unmanaged_keyword > 0 && (
            <p className="text-[11px] text-[#6a6b6c] mb-3 leading-relaxed">
              {diff.unmanaged_keyword} photo{diff.unmanaged_keyword === 1 ? '' : 's'} in this album
              carry a KaMeRa keyword but were never analysed here. Nothing to reconcile — analyse the
              album again to bring them in
            </p>
          )}
        </div>

        <div className="px-6 py-4 mt-auto border-t border-[rgba(255,255,255,0.07)]">
          {removing > 0 && (
            <p className="flex items-center gap-1.5 text-[11px] text-[#C97B7B] mb-2.5">
              <AlertTriangle size={12} />
              {removing} row{removing === 1 ? '' : 's'} will be removed from KaMeRa. Your photos are not touched
            </p>
          )}
          <div className="flex justify-end gap-3">
            <button onClick={onClose} className={BTN_SECONDARY}>
              <X size={16} /> {diff?.drift === 0 ? 'Close' : 'Cancel'}
            </button>
            {diff && diff.drift > 0 && (
              <button
                onClick={run}
                disabled={running || pendingCount === 0}
                className={removing > 0 ? BTN_PRIMARY_DANGER : BTN_PRIMARY}
              >
                <Check size={16} />
                {running ? 'Reconciling…' : `Reconcile ${pendingCount}`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
