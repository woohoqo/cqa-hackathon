import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Evaluation } from './types';
import { statusLabels } from './display';

export function Icon({ name, size = 20 }: { name: 'chat' | 'checklist' | 'appeals' | 'upload' | 'play' | 'arrow' | 'refresh' | 'check' | 'close' | 'search' | 'phone' | 'link'; size?: number }) {
  const paths: Record<typeof name, ReactNode> = {
    chat: <path d="M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 1 1 19 0Z" />,
    checklist: <><rect x="5" y="3" width="15" height="19" rx="2" /><path d="m8 9 1 1 2-2m2 1h4M8 15l1 1 2-2m2 1h4M9 3V1m7 2V1" /></>,
    appeals: <><path d="M12 3v18m-6 0h12M4 6h16M6 6l-4 9h8L6 6Zm12 0-4 9h8l-4-9Z" /><path d="M2 15c0 4 8 4 8 0m4 0c0 4 8 4 8 0" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M3 15v5h18v-5" /></>,
    play: <path d="m8 4 12 8-12 8V4Z" />,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    refresh: <><path d="M20 8a8 8 0 1 0 1 8M20 3v6h-6" /></>,
    check: <path d="m4 12 5 5L20 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    phone: <path d="m6 3 4 5-3 3c2 3 3 4 6 6l3-3 5 4c-2 7-9 2-13-2S-1 5 6 3Z" />,
    link: <><path d="M14 3h7v7m0-7L10 14M11 3H3v18h18v-8" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function Status({ status }: { status: Evaluation['status'] | 'none' }) {
  return <span className={`status ${status}`}><span className="status-dot" />{statusLabels[status]}</span>;
}
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="notice error" role="alert"><span>{message}</span>{onRetry && <button className="button secondary" onClick={onRetry}>Повторить</button>}</div>;
}
export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Icon name="chat" size={26} /></div><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}
export function Modal({ title, onClose, children, busy = false }: { title: string; onClose: () => void; children: ReactNode; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); return () => ref.current?.close(); }, []);
  return <dialog className="modal" ref={ref} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}><Icon name="close" /></button></div>
    {children}
  </dialog>;
}
export function Loading({ text = 'Загружаем данные…' }: { text?: string }) { return <div className="loading" role="status"><span className="spinner" />{text}</div>; }
export function useHash() {
  const [hash, setHash] = useState(window.location.hash || '#dialogs');
  useEffect(() => { const update = () => setHash(window.location.hash || '#dialogs'); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  return hash;
}
