// InstallOllamaCTA — shown when status === "not_installed".
//
// Before today this state was prose-only ("Install Ollama from ollama.com,
// then ollama pull qwen2.5vl:7b") with no actionable button. The pull
// button doesn't help here — there's nothing to pull from until Ollama
// itself is installed, and clicking it on a fresh machine sends the user
// into a 30-minute silent poll that times out.
//
// This component gives the user two clear paths and a "what next" line:
//   1. Open ollama.com — opens the official installer page in the browser.
//      The site auto-detects the user's OS and serves the right download.
//   2. brew install ollama — click-to-copy snippet for users on Homebrew.
//   3. "Once Ollama is installed, click Refresh." — points at whichever
//      Refresh / Try again control the host surface provides.
//
// `compact` switches to a single-line variant used by the GroupLoupe chip,
// which lives in a horizontal top bar where the full card doesn't fit.

import { useCallback, useState } from 'react'
import { Copy, Check, ExternalLink } from 'lucide-react'

const INSTALL_URL    = 'https://ollama.com'
const BREW_COMMAND   = 'brew install ollama'

export function InstallOllamaCTA({ compact = false }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(BREW_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard API unavailable — silent no-op, same as FilenameWithCopy */ }
  }, [])

  if (compact) {
    // Single-line variant for the GroupLoupe top bar.
    return (
      <span
        className="inline-flex items-center gap-2 px-2 py-1 rounded-md bg-[rgba(232,184,74,0.10)] border border-[rgba(232,184,74,0.30)] select-none"
        title="Ollama isn't installed yet. Visit ollama.com to download, or run `brew install ollama` in Terminal."
      >
        <span className="w-1.5 h-1.5 rounded-full bg-[#E8B84A] shrink-0" />
        <span className="text-xs text-[#E8B84A] whitespace-nowrap">
          AI ranking needs Ollama
        </span>
        <a
          href={INSTALL_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)] hover:opacity-70 transition-opacity"
        >
          Install <ExternalLink size={11} />
        </a>
      </span>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <a
          href={INSTALL_URL}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs bg-[rgba(91,184,212,0.12)] text-[#5BB8D4] border border-[rgba(91,184,212,0.30)] hover:opacity-70 transition-opacity"
        >
          Install Ollama <ExternalLink size={12} />
        </a>
        <span className="text-xs text-[#6a6b6c]">or</span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono bg-[rgba(255,255,255,0.04)] text-[#cecece] border border-[rgba(255,255,255,0.08)] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
          title={copied ? 'Copied' : 'Copy to clipboard'}
        >
          <span>{BREW_COMMAND}</span>
          {copied ? <Check size={12} className="text-[#7DB89A]" /> : <Copy size={12} className="text-[#9c9c9d]" />}
        </button>
      </div>
      <p className="text-xs text-[#9c9c9d] leading-relaxed">
        Once Ollama is installed, click Refresh — the app will then offer to
        download the vision model (qwen2.5vl:7b, ≈6 GB).
      </p>
    </div>
  )
}
