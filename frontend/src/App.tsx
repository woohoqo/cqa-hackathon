import { useCallback, useEffect, useRef, useState } from 'react';
import { api, friendlyError } from './api';
import type { ChecklistCreate, ChecklistVersion, Conversation, Dataset, Evaluation, EvaluationBatch, EvaluationRun, ImportResult } from './types';
import { Empty, ErrorNotice, Icon, Loading, useHash } from './components';
import { DialogueList } from './DialogueList';
import { DialogueDetail } from './DialogueDetail';
import { ChecklistEditor } from './ChecklistEditor';
import { ImportDialog } from './ImportDialog';
import { newestFirst, pending } from './display';

function readSelection(key: string) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }
function storeSelection(datasetId: string, runId: string) { try { localStorage.setItem('cqa.dataset', datasetId); localStorage.setItem('cqa.run', runId); } catch { /* Storage may be disabled. Server records remain available. */ } }

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
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [datasetId, setDatasetId] = useState(() => readSelection('cqa.dataset'));
  const [runId, setRunId] = useState(() => readSelection('cqa.run'));
  const selection = useRef({ datasetId, runId });
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
      const [cs, es, cls, ds, rs] = await Promise.all([api.listConversations(nextController.signal), api.listEvaluations(nextController.signal), api.listChecklists(nextController.signal), api.listDatasets(nextController.signal), api.listRuns(nextController.signal)]);
      if (sequence !== loadSequence.current) return;
      ds.sort((a, b) => b.created_at.localeCompare(a.created_at)); rs.sort((a, b) => b.created_at.localeCompare(a.created_at));
      const selectedDataset = ds.some(item => item.id === selection.current.datasetId) ? selection.current.datasetId : ds[0]?.id || '';
      const selectedRun = rs.some(item => item.id === selection.current.runId && item.dataset_id === selectedDataset) ? selection.current.runId : '';
      const selectedBatch = selectedRun ? await api.getBatch(selectedRun) : null;
      if (sequence !== loadSequence.current) return;
      selection.current = { datasetId: selectedDataset, runId: selectedRun }; storeSelection(selectedDataset, selectedRun);
      setDatasetId(selectedDataset); setRunId(selectedRun); setDatasets(ds); setRuns(rs); setBatch(selectedBatch);
      const sorted = [...cls].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.version - a.version);
      const merged = new Map(es.map(item => [item.id, item])); selectedBatch?.evaluations.forEach(item => merged.set(item.id, item));
      setConversations(cs); setEvaluations([...merged.values()]); setChecklists(sorted);
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

  function selectContext(nextDatasetId: string, nextRunId: string) {
    selection.current = { datasetId: nextDatasetId, runId: nextRunId };
    storeSelection(nextDatasetId, nextRunId); setDatasetId(nextDatasetId); setRunId(nextRunId); setBatch(null);
    void refresh().catch(() => {});
  }

  function acceptBatch(result: EvaluationBatch) {
    controller.current?.abort(); ++loadSequence.current; setRefreshing(false);
    setBatch(result); setRunId(result.id);
    const nextDatasetId = result.run?.dataset_id || selection.current.datasetId;
    setDatasetId(nextDatasetId); selection.current = { datasetId: nextDatasetId, runId: result.id }; storeSelection(nextDatasetId, result.id);
    if (result.run) setRuns(previous => [result.run!, ...previous.filter(item => item.id !== result.run!.id)]);
    setEvaluations(previous => { const merged = new Map(previous.map(item => [item.id, item])); result.evaluations.forEach(item => merged.set(item.id, item)); return [...merged.values()]; });
  }

  async function evaluate(ids: string[]) {
    if (!checklistId || !datasetId || startGuard.current) return;
    const dataset = datasets.find(item => item.id === datasetId);
    if (!dataset || ids.some(id => !dataset.conversation_ids.includes(id))) { setError('Диалог не входит в выбранный набор. Выберите его набор перед новым запуском.'); return; }
    startGuard.current = true; setStarting(true); setNotice(''); setError('');
    try {
      const result = await api.evaluate(ids, checklistId, datasetId, crypto.randomUUID());
      // Ignore an older list request so a queued job cannot disappear from the UI.
      acceptBatch(result);
      setNotice(`Поставлено в очередь: ${result.evaluations.length}. Чек-лист v${result.evaluations[0]?.checklist_snapshot.version ?? ''}.`);
    } catch (err) { setError(friendlyError(err)); } finally { startGuard.current = false; setStarting(false); }
  }
  async function retryFailed(ids: string[]) {
    if (!runId || !ids.length || startGuard.current) return;
    startGuard.current = true; setStarting(true); setError(''); setNotice('');
    try {
      const result = await api.retryBatch(runId, ids);
      acceptBatch(result.batch);
      setNotice(`Повторно поставлено в очередь: ${result.retried}. Уже обработано или выполняется: ${result.skipped}. Успешные результаты сохранены.`);
    } catch (err) { setError(friendlyError(err)); }
    finally { startGuard.current = false; setStarting(false); }
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
    if (result.dataset) {
      setDatasets(previous => [result.dataset!, ...previous.filter(item => item.id !== result.dataset!.id)]);
      selection.current = { datasetId: result.dataset.id, runId: '' }; storeSelection(result.dataset.id, '');
      setDatasetId(result.dataset.id); setRunId(''); setBatch(null);
    }
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
        {loading ? <Loading /> : <>
          <div hidden={page !== 'dialogs'}><DialogueList conversations={conversations} evaluations={evaluations} checklists={checklists} datasets={datasets} datasetId={datasetId} onDataset={id => selectContext(id, '')} runs={runs} runId={runId} onRun={id => selectContext(datasetId, id)} batch={batch} checklistId={checklistId} onChecklist={setChecklistId} onEvaluate={evaluate} onRetry={retryFailed} busy={starting} refreshing={refreshing} onRefresh={() => void refresh().catch(() => {})} onImport={() => setImportOpen(true)} /></div>
          {page === 'detail' && (currentConversation ? <DialogueDetail key={`${currentConversation.id}:${runId}`} initialEvaluationId={batch?.evaluations.find(item => item.conversation_id === currentConversation.id)?.id} conversation={currentConversation} evaluations={evaluations} checklist={selectedChecklist} onEvaluate={evaluate} busy={starting} onUpdated={refresh} reviewMode={reviewMode} /> : <Empty title="Диалог не найден" action={<a className="button secondary" href="#dialogs">К списку диалогов</a>}>Обновите данные или выберите диалог из списка.</Empty>)}
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
