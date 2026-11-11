import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  wide?: boolean;
  drawer?: boolean;
}

export function Modal({ isOpen, onClose, title, children, wide, drawer }: ModalProps) {
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
      className={`${drawer ? 'fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-2xl' : `w-[calc(100%-2rem)] ${wide ? 'max-w-4xl' : 'max-w-lg'} rounded-2xl`} max-h-[90dvh] overflow-y-auto border border-slate-300 bg-white text-slate-900 shadow-2xl backdrop:bg-slate-950/70 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100`}>
      <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
        <h2 className="text-lg font-semibold">{title}</h2>
        <button onClick={onClose} aria-label="Close dialog" className="ops-button"><X size={18} /></button>
      </div>
      <div className="p-4 sm:p-6">{children}</div>
    </dialog>
  );
}
