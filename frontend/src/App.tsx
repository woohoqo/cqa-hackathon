import { useCallback, useEffect, useRef, useState } from 'react';
import { api, friendlyError } from './api';
import type { ChecklistCreate, ChecklistVersion, Conversation, Evaluation, EvaluationBatch, ImportResult } from './types';
import { Empty, ErrorNotice, Icon, Loading, useHash } from './components';
import { DialogueList } from './DialogueList';
import { DialogueDetail } from './DialogueDetail';
import { ChecklistEditor } from './ChecklistEditor';
import { ImportDialog } from './ImportDialog';
import { newestFirst, pending } from './display';

export default function App() {
  const hash = useHash();
  const page = hash.startsWith('#dialog/') ? 'detail' : hash === '#checklist' ? 'checklist' : hash === '#appeals' ? 'appeals' : 'dialogs';
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [checklists, setChecklists] = useState<ChecklistVersion[]>([]);
  const [checklistId, setChecklistId] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [reviewMode, setReviewMode] = useState(false);
  const [batch, setBatch] = useState<EvaluationBatch | null>(null);
  const [reviewId, setReviewId] = useState('');
  const loadSequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const startGuard = useRef(false);

  const refresh = useCallback(async () => {
    const sequence = ++loadSequence.current;
    controller.current?.abort();
    const nextController = new AbortController(); controller.current = nextController;
    setRefreshing(true);
    try {
      const [cs, es, cls] = await Promise.all([api.listConversations(nextController.signal), api.listEvaluations(nextController.signal), api.listChecklists(nextController.signal)]);
      if (sequence !== loadSequence.current) return;
      const sorted = [...cls].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.version - a.version);
      setConversations(cs); setEvaluations(es); setChecklists(sorted);
      setChecklistId(current => sorted.some(c => c.id === current) ? current : sorted[0]?.id || '');
      setConnected(true); setError('');
    } catch (err) {
      if (nextController.signal.aborted || sequence !== loadSequence.current) return;
      setConnected(false); setError(friendlyError(err));
      throw err;
    } finally {
      if (sequence === loadSequence.current) { setLoading(false); setRefreshing(false); }
    }
  }, []);
  useEffect(() => { void refresh().catch(() => {}); return () => { controller.current?.abort(); }; }, [refresh]);
  const active = evaluations.some(pending);
  useEffect(() => {
    if (!active || refreshing) return;
    const timer = window.setTimeout(() => { void refresh().catch(() => {}); }, connected ? 2000 : 5000);
    return () => window.clearTimeout(timer);
  }, [active, evaluations, connected, refreshing, refresh]);

  async function evaluate(ids: string[]) {
    if (!checklistId || startGuard.current) return;
    startGuard.current = true; setStarting(true); setNotice(''); setError('');
    try {
      const result = await api.evaluate(ids, checklistId);
      // Ignore an older list request so a queued job cannot disappear from the UI.
      controller.current?.abort(); ++loadSequence.current; setRefreshing(false);
      setBatch(result); setEvaluations(prev => [...prev, ...result.evaluations]);
      setNotice(`Поставлено в очередь: ${result.evaluations.length}. Чек-лист v${result.evaluations[0]?.checklist_snapshot.version ?? ''}.`);
    } catch (err) { setError(friendlyError(err)); } finally { startGuard.current = false; setStarting(false); }
  }
  async function saveChecklist(input: ChecklistCreate) {
    const saved = await api.saveChecklist(input);
    controller.current?.abort(); ++loadSequence.current; setRefreshing(false);
    setChecklists(prev => [saved, ...prev.filter(c => c.id !== saved.id)]);
    setChecklistId(saved.id);
    return saved;
  }
  async function imported(result: ImportResult) {
    controller.current?.abort(); ++loadSequence.current; setRefreshing(false);
    setConversations(prev => { const merged = new Map(prev.map(c => [c.id, c])); result.conversations.forEach(c => merged.set(c.id, c)); return [...merged.values()]; });
    setNotice(`Импорт завершён: добавлено ${result.imported}, уже существовало ${result.existing}.`);
    void refresh().catch(() => {});
  }
  const selectedChecklist = checklists.find(c => c.id === checklistId);
  let currentId = ''; try { currentId = decodeURIComponent(hash.slice('#dialog/'.length)); } catch { /* Invalid hash is shown as missing. */ }
  const currentConversation = conversations.find(c => c.id === currentId);
  const completed = newestFirst(evaluations).filter(e => e.status === 'completed');
  const reviewEvaluation = completed.find(e => e.id === reviewId) || completed[0];
  useEffect(() => { if (reviewEvaluation && reviewId !== reviewEvaluation.id) setReviewId(reviewEvaluation.id); }, [reviewId, reviewEvaluation?.id]);
  const reviewConversation = conversations.find(c => c.id === reviewEvaluation?.conversation_id);
  const tracked = batch?.evaluations.map(e => evaluations.find(current => current.id === e.id) || e) || [];
  const finished = tracked.filter(e => !pending(e)).length;
  const failed = tracked.filter(e => e.status === 'failed').length;

  const pageHeading = <div className={`page-heading page-heading-${page}`}>
    <div className="intro-copy"><div className="eyebrow">{page === 'dialogs' ? '01 / ДИАЛОГИ И КАЧЕСТВО' : page === 'checklist' ? '02 / ПРАВИЛА ОЦЕНКИ' : '03 / АПЕЛЛЯЦИИ'}</div>
      <h1>{page === 'dialogs' ? <>Качество<br />в каждом<br /><em>диалоге.</em></> : page === 'checklist' ? <>Ваши правила.<br /><em>Точная оценка.</em></> : <>Ясность в каждом<br /><em>решении.</em></>}</h1>
      <p>{page === 'dialogs' ? 'От исходной реплики к прозрачной оценке. Каждый вывод — с доказательством.' : page === 'checklist' ? 'Правила оценки, которые меняются вместе с вашей командой.' : 'Проверьте спорный критерий и сохраните обоснованное решение.'}</p>
      <button className="button secondary page-refresh" disabled={refreshing || starting} onClick={() => void refresh().catch(() => {})}><Icon name="refresh" size={17} />{refreshing ? 'Обновляем…' : 'Обновить'}</button>
    </div>
    {page !== 'dialogs' && <figure className="section-photo"><img src={page === 'checklist' ? '/media/office-detail.jpg' : '/media/team-workspace.jpg'} alt="" width="1200" height="800" decoding="async" /><figcaption><span className="photo-dot" />{page === 'checklist' ? 'Единые правила. Разные диалоги.' : 'Технологии помогают. Решение за человеком.'}</figcaption></figure>}
  </div>;

  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}>Перейти к содержимому</a>
    <header className="site-header">
      <a href="#dialogs" className="brand" aria-label="CQA — Диалоги"><span className="brand-mark" aria-hidden="true"><svg width="34" height="34" viewBox="0 0 34 34"><path fill="currentColor" d="M3 3h10v10H3zm11 0h17v10H14zM3 14h10v17H3zm11 0h10v10H14z" /><path fill="#c7f34d" d="M24 24h7v7H14V24z" /></svg></span><div><strong>CQA</strong><small>FREEDOM BROKER</small></div></a>
      <nav className="primary-nav" aria-label="Основная навигация"><a href="#dialogs" aria-label="Диалоги" className={page === 'dialogs' || page === 'detail' ? 'active' : ''}><span className="nav-index" aria-hidden="true">01</span><span>Диалоги</span><span className="nav-count">{conversations.length}</span></a><a href="#checklist" aria-label="Чек-листы" className={page === 'checklist' ? 'active' : ''}><span className="nav-index" aria-hidden="true">02</span><span>Чек-листы</span></a><a href="#appeals" aria-label="Апелляции" className={page === 'appeals' ? 'active' : ''}><span className="nav-index" aria-hidden="true">03</span><span>Апелляции</span></a></nav>
      <div className="header-tools"><span className={`connection ${connected ? 'online' : ''}`}><span />{loading ? 'Подключение…' : connected ? 'Сервер подключён' : 'Нет соединения'}</span><label className="role-switch"><span className="sr-only">Режим работы</span><select aria-label="Режим работы" value={reviewMode ? 'reviewer' : 'operator'} onChange={e => setReviewMode(e.target.value === 'reviewer')}><option value="operator">Оператор</option><option value="reviewer">Проверяющий</option></select></label><span className="avatar" aria-hidden="true">{reviewMode ? 'П' : 'О'}</span></div>
    </header>
    <div className="workspace">
      <main id="main-content" data-page={page} tabIndex={-1}>
        {page !== 'detail' && page !== 'dialogs' && pageHeading}
        {notice && <div className="notice success" role="status"><span><Icon name="check" size={18} />{notice}</span><button className="icon-button" onClick={() => setNotice('')} aria-label="Закрыть уведомление"><Icon name="close" size={16} /></button></div>}
        {error && <ErrorNotice message={error} onRetry={() => void refresh().catch(() => {})} />}
        {!!tracked.length && <div className="batch-progress" aria-live="polite"><div><strong>{finished < tracked.length ? 'Обработка диалогов' : 'Обработка завершена'}</strong><span>{finished} из {tracked.length}{failed ? ` · ошибок: ${failed}` : ''}</span></div><progress value={finished} max={tracked.length} /><p className="help">{finished < tracked.length ? 'Результаты обновляются автоматически. Можно продолжать работу.' : failed ? 'Причины ошибок доступны в карточках. Для повторного запуска выберите диалоги в списке.' : 'Откройте карточку, чтобы изучить оценки и доказательства.'}</p>{finished === tracked.length && <button className="text-button" onClick={() => setBatch(null)}>Скрыть</button>}</div>}
        {loading ? <Loading /> : <>
          {page === 'dialogs' && <DialogueList intro={pageHeading} conversations={conversations} evaluations={evaluations} checklists={checklists} checklistId={checklistId} onChecklist={setChecklistId} onEvaluate={evaluate} busy={starting} onImport={() => setImportOpen(true)} />}
          {page === 'detail' && (currentConversation ? <DialogueDetail key={currentConversation.id} conversation={currentConversation} evaluations={evaluations} checklist={selectedChecklist} onEvaluate={evaluate} busy={starting} onUpdated={refresh} reviewMode={reviewMode} /> : <Empty title="Диалог не найден" action={<a className="button secondary" href="#dialogs">К списку диалогов</a>}>Обновите данные или выберите диалог из списка.</Empty>)}
          {/* Keep the editor mounted to preserve unsaved work while navigating sections. */}
          <div hidden={page !== 'checklist'}><ChecklistEditor checklists={checklists} selectedId={checklistId} onSelect={setChecklistId} onSave={saveChecklist} onUse={id => { setChecklistId(id); setNotice('Версия выбрана. Отметьте диалоги и нажмите «Оценить выбранные».'); window.location.hash = 'dialogs'; }} /></div>
          {page === 'appeals' && (reviewEvaluation && reviewConversation ? <><div className="panel review-selector"><label className="field">Оценка для апелляции<select aria-label="Оценка для апелляции" value={reviewEvaluation.id} onChange={e => setReviewId(e.target.value)}>{completed.map(e => <option key={e.id} value={e.id}>{conversations.find(c => c.id === e.conversation_id)?.external_id || e.conversation_id} · {e.checklist_snapshot.name} v{e.checklist_snapshot.version} · {new Date(e.created_at).toLocaleString('ru-RU')}</option>)}</select></label><p className="help">Для принятия решения выберите режим «Проверяющий» в верхней панели.</p></div><DialogueDetail key={`review-${reviewEvaluation.id}`} initialEvaluationId={reviewEvaluation.id} conversation={reviewConversation} evaluations={evaluations} checklist={selectedChecklist} onEvaluate={evaluate} busy={starting} onUpdated={refresh} reviewMode={reviewMode} reviewOnly /></> : <Empty title="Нет завершённых оценок">Сначала оцените диалог. Затем можно будет выбрать критерий и подать апелляцию.</Empty>)}
        </>}
      </main>
      <footer className="page-footer"><div><span className="footer-brand">CQA<span> / Freedom Broker</span></span><p>Каждый вывод — с доказательством.</p></div><div className="footer-meta"><span>Оценки сохраняются на сервере</span><span>RU / KZ / EN</span></div></footer>
    </div>
    {importOpen && <ImportDialog onClose={() => setImportOpen(false)} onImported={imported} />}
  </div>;
}
