import { useState } from 'react'
import { BookOpen, Keyboard, MessageSquare, ExternalLink, X } from 'lucide-react'
import { BTN_ICON } from '../ui/buttons'
import { DecisionWord } from '../ui/primitives'
import { KAMERA_VERSION, FEEDBACK_URL } from '../version'

// Inline keycap matching the DetailView header style (18×18 square,
// inset/shadow gradient). Used in copy and in the Shortcuts table.
function KeyCap({ children, wide = false }) {
  return (
    <span
      className={`inline-flex items-center justify-center ${wide ? 'px-2 h-[18px] min-w-[18px]' : 'w-[18px] h-[18px]'} bg-gradient-to-b from-[#121212] to-[#0d0d0d] rounded-[4px] text-[11px] font-mono font-semibold text-[#f9f9f9] relative -top-[2px] mx-[1px]`}
      style={{boxShadow:'0 1.5px 0.5px 2.5px rgba(0,0,0,0.4),inset 0 1px 0 rgba(255,255,255,0.08),inset 0 -1px 0 rgba(0,0,0,0.4),0 1px 0 rgba(0,0,0,0.5),0 0 0 1px rgba(255,255,255,0.04)'}}
    >
      {children}
    </span>
  )
}

// ShortcutsModal — help overlay with tabbed sections (How to cull · Shortcuts)
export function ShortcutsModal({ onClose }) {
  const [activeTab, setActiveTab] = useState('how-to')

  const STEPS = [
    {
      n: 1,
      title: 'Choose your folder',
      body: 'Click the folder field above and select a folder containing RAW files (RAF, NEF, ARW, DNG).',
    },
    {
      n: 2,
      title: 'Analyze',
      body: 'The app scores each photo for sharpness, exposure, and aesthetic quality. Typically 2–5 seconds per photo.',
    },
    {
      n: 3,
      title: 'Cull',
      body: (
        <>
          Press <KeyCap>K</KeyCap> to <DecisionWord kind="keep">Keep</DecisionWord>,
          {' '}<KeyCap>R</KeyCap> to <DecisionWord kind="reject">Reject</DecisionWord>,
          {' '}<KeyCap>M</KeyCap> for <DecisionWord kind="maybe">Maybe</DecisionWord>.
          {' '}Files move to your destination folders automatically — undo with <KeyCap>U</KeyCap>.
          {' '}Changed your mind right after deciding? Double-press the new key (e.g. <KeyCap>R</KeyCap> <KeyCap>R</KeyCap>) within ~200ms to amend the previous photo without going back
        </>
      ),
    },
  ]

  const SHORTCUTS = [
    { group: 'Culling', keys: [
      { key: 'K', desc: <><DecisionWord kind="keep">Keep</DecisionWord> — moves file to Keep folder. In compare view, also <DecisionWord kind="reject">Rejects</DecisionWord> the other compared photos</> },
      { key: 'M', desc: <><DecisionWord kind="maybe">Maybe</DecisionWord> — moves file to Maybe folder</> },
      { key: 'R', desc: <><DecisionWord kind="reject">Reject</DecisionWord> — moves file to Reject folder</> },
      { key: 'K K', desc: <>Changed your mind on the previous photo? Double-press <DecisionWord kind="keep">K</DecisionWord> / <DecisionWord kind="maybe">M</DecisionWord> / <DecisionWord kind="reject">R</DecisionWord> within ~200ms to amend the previous decision without navigating back. Current photo stays put</> },
      { key: 'U',         desc: 'Undo the selected photo’s decision' },
      { key: 'C',         desc: 'Add / remove selected photo from compare (max 4)' },
      { key: 'B',         desc: 'In a group loupe, mark the focused photo as the group’s Best' },
    ]},
    { group: 'Navigation', keys: [
      { key: '← →',       desc: 'Previous / next cell (photos and groups)' },
      { key: '↑ ↓',       desc: 'Move up / down by row' },
      { key: 'Space',     desc: 'Open compare (when 2+ staged) · otherwise open focused photo or group' },
      { key: 'Enter',     desc: 'Open focused group in the loupe' },
      { key: 'O',         desc: 'In detail view on a group, open it in the loupe (double-click the photo also works)' },
      { key: 'Esc',       desc: 'Close layers (compare → loupe → detail)' },
    ]},
  ]

  const TAB_ACTIVE   = 'text-[#f9f9f9] border-b border-[#5BB8D4]'
  const TAB_INACTIVE = 'text-[#6a6b6c] hover:opacity-70 border-b border-transparent transition-opacity'

  return (
    <div className="fixed inset-0 z-[90] flex items-start justify-center bg-[rgba(7,8,10,0.85)] pt-[5vh]" onClick={onClose}>
      <div
        className="bg-[#101111] border border-[rgba(255,255,255,0.10)] rounded-xl w-[440px] max-w-full max-h-[90vh] flex flex-col shadow-[0_0_0_2px_rgba(0,0,0,0.5),0_0_14px_rgba(255,255,255,0.10)]"
        onClick={e => e.stopPropagation()}
      >
        {/* Upper section — darker band, matches SettingsModal */}
        <div className="bg-[#161718] border-b border-[#2f3031] px-6 pt-5 pb-0 rounded-t-xl">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-[#f9f9f9]">Help</h2>
            <button onClick={onClose} className={BTN_ICON} aria-label="Close">
              <X size={18} />
            </button>
          </div>

          <div className="flex gap-5">
            <button
              onClick={() => setActiveTab('how-to')}
              className={`inline-flex items-center gap-1.5 pb-2 text-xs font-medium transition-colors ${activeTab === 'how-to' ? TAB_ACTIVE : TAB_INACTIVE}`}
            >
              <BookOpen size={15} /> How to cull your photos
            </button>
            <button
              onClick={() => setActiveTab('shortcuts')}
              className={`inline-flex items-center gap-1.5 pb-2 text-xs font-medium transition-colors ${activeTab === 'shortcuts' ? TAB_ACTIVE : TAB_INACTIVE}`}
            >
              <Keyboard size={15} /> Shortcuts
            </button>
            <button
              onClick={() => setActiveTab('feedback')}
              className={`inline-flex items-center gap-1.5 pb-2 text-xs font-medium transition-colors ${activeTab === 'feedback' ? TAB_ACTIVE : TAB_INACTIVE}`}
            >
              <MessageSquare size={15} /> Feedback
            </button>
          </div>
        </div>

        {/* Body — scrollable middle region */}
        <div className="px-6 pt-5 pb-6 overflow-y-auto flex-1 min-h-0">
          {activeTab === 'how-to' && (
            <div className="space-y-6">
              {STEPS.map(({ n, title, body }) => (
                <div key={n} className="flex gap-4 items-start">
                  <div className="w-7 h-7 rounded-full bg-[#1b1c1e] border border-[rgba(255,255,255,0.10)] flex items-center justify-center flex-shrink-0 mt-0.5">
                    <span className="text-xs font-semibold text-[#9c9c9d]">{n}</span>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-[#f9f9f9] mb-1">{title}</p>
                    <p className="text-sm text-[#cecece]">{body}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'shortcuts' && (
            <div className="space-y-5">
              {SHORTCUTS.map(({ group, keys }) => (
                <div key={group}>
                  <p className="label mb-2">{group}</p>
                  <div className="space-y-2">
                    {keys.map(({ key, desc }) => {
                      // Split on whitespace so multi-key combos render as
                      // individual caps (e.g. "← →", "Shift+Tab", "⌘Z" stays one).
                      const parts = key.split(/\s+/)
                      return (
                        <div key={key} className="flex items-center justify-between gap-4">
                          <span className="text-xs text-[#9c9c9d]">{desc}</span>
                          <span className="flex gap-1 flex-shrink-0">
                            {parts.map((p, i) => (
                              <KeyCap key={i} wide={p.length > 1}>{p}</KeyCap>
                            ))}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === 'feedback' && (
            <div className="space-y-5">
              <p className="text-sm text-[#cecece] leading-relaxed">
                KaMeRa is an early personal project shared for feedback. Bug reports, feature ideas, and camera-compatibility notes are all welcome — anything that helps the app get better.
              </p>

              <a
                href={FEEDBACK_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-3 py-2 rounded-md bg-[#1b1c1e] border border-[rgba(255,255,255,0.10)] text-sm text-[#f9f9f9] hover:bg-[#222324] transition-colors"
              >
                <MessageSquare size={15} />
                Open feedback form on GitHub
                <ExternalLink size={13} className="opacity-60" />
              </a>

              <div className="space-y-2 pt-2 border-t border-[rgba(255,255,255,0.08)]">
                <p className="text-xs text-[#9c9c9d]">When reporting a bug, please include:</p>
                <ul className="text-xs text-[#cecece] space-y-1 list-disc pl-4">
                  <li>KaMeRa version: <span className="font-mono text-[#f9f9f9]">v{KAMERA_VERSION}</span></li>
                  <li>Your OS and Python version</li>
                  <li>Camera / file format involved, if relevant</li>
                  <li>The last few lines of <span className="font-mono">data/app.log</span> if the app crashed</li>
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
