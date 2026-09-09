import React, { useEffect, useRef } from 'react'

/** Native modality plus explicit keyboard behavior for consistent focus restoration. */
export function Modal({ titleId, onDismiss, children }: { titleId: string; onDismiss: () => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  const dismiss = useRef(onDismiss); dismiss.current = onDismiss
  useEffect(() => {
    const dialog = ref.current!
    const trigger = document.activeElement as HTMLElement | null
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '')
    const initial = dialog.querySelector<HTMLElement>('[data-initial-focus]') ?? dialog.querySelector<HTMLElement>('button')
    initial?.focus()
    const contain = (event: FocusEvent) => { if (!dialog.contains(event.target as Node)) initial?.focus() }
    document.addEventListener('focusin', contain)
    return () => {
      document.removeEventListener('focusin', contain)
      if (dialog.close) dialog.close()
      if (trigger?.isConnected) trigger.focus()
    }
  }, [])
  return <dialog ref={ref} className="dialog" aria-modal="true" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); dismiss.current() }} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismiss.current(); return }
    if (event.key !== 'Tab') return
    const controls = Array.from(ref.current!.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]'))
    const first = controls[0], last = controls[controls.length - 1]
    if (!first) { event.preventDefault(); return }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }}>{children}</dialog>
}
