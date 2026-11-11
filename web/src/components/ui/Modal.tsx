import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
}

export function Modal({ isOpen, onClose, title, children, wide }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || !isOpen) return;
    const previous = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [isOpen]);

  return (
    <dialog ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); onClose(); }}
      className={`w-[calc(100%-2rem)] ${wide ? 'max-w-4xl' : 'max-w-lg'} max-h-[90dvh] overflow-y-auto rounded-2xl border border-slate-300 bg-white text-slate-900 shadow-2xl backdrop:bg-slate-950/70 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100`}>
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
        <h2 className="text-lg font-semibold">{title}</h2>
        <button onClick={onClose} aria-label="Close dialog" className="ops-button"><X size={18} /></button>
      </div>
      <div className="p-4 sm:p-6">{children}</div>
    </dialog>
  );
}
