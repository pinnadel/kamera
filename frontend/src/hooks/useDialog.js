import { useEffect, useRef } from 'react'

// Dialogs currently mounted, oldest first. Only the last one answers Escape
// and Tab, so a confirm opened on top of Settings closes alone instead of
// taking Settings down with it.
const stack = []

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

// useDialog — the keyboard half of a modal: Escape closes it, Tab stays inside
// it, focus moves in on open and returns to whatever opened it on close.
// Attach the returned ref to the dialog panel (not the backdrop) and give the
// panel role="dialog" + aria-modal + a name.
//
// Pass onClose = null for a forced choice (no Escape), e.g. ConflictModal.
//
// The listener runs in the capture phase on window and swallows Escape, so the
// grid's own Escape handlers (exit multi-select, close detail) don't also fire
// for a keypress that was meant for the dialog.
export function useDialog(onClose) {
  const ref = useRef(null)
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })

  useEffect(() => {
    const token = {}
    stack.push(token)
    const opener = document.activeElement
    const node = ref.current
    if (node && !node.contains(document.activeElement)) node.focus({ preventScroll: true })

    const onKey = (e) => {
      if (stack[stack.length - 1] !== token) return
      if (e.key === 'Escape') {
        if (!closeRef.current) return
        e.preventDefault()
        e.stopImmediatePropagation()
        closeRef.current()
        return
      }
      if (e.key !== 'Tab' || !node) return
      const items = Array.from(node.querySelectorAll(FOCUSABLE))
        .filter(el => el.offsetParent !== null)
      if (items.length === 0) { e.preventDefault(); node.focus(); return }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || active === node || !node.contains(active))) {
        e.preventDefault(); last.focus()
      } else if (!e.shiftKey && (active === last || !node.contains(active))) {
        e.preventDefault(); first.focus()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      stack.splice(stack.indexOf(token), 1)
      if (opener && opener !== document.body && document.contains(opener)) {
        opener.focus({ preventScroll: true })
      }
    }
  }, [])

  return ref
}
