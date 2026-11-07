import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ChecklistVersion, Conversation, Dataset, Evaluation, EvaluationBatch, EvaluationRun } from './types';
import { channel, channelLabel, language, newestFirst, numberLabel, pending, statusLabels } from './display';
import { Empty, Icon, Status } from './components';
import { ascendingFinalScore, attemptsForRun, evaluationProvenance, paginate, selectedConversationIds } from './workflow';
import { ExportPanel } from './ExportPanel';

interface Props {
  intro: ReactNode; conversations: Conversation[]; checklists: ChecklistVersion[];
  datasets: Dataset[]; datasetId: string; onDataset: (id: string) => void;
  runs: EvaluationRun[]; runId: string; onRun: (id: string) => void; batch: EvaluationBatch | null;
  checklistId: string; onChecklist: (id: string) => void; onEvaluate: (ids: string[]) => Promise<void>;
  onRetry: (failedIds: string[]) => Promise<void>; busy: boolean; onImport: () => void;
}
export function DialogueList({ intro, conversations, checklists, datasets, datasetId, onDataset, runs, runId, onRun, batch, checklistId, onChecklist, onEvaluate, onRetry, busy, onImport }: Props) {
  const [query, setQuery] = useState(''); const [lang, setLang] = useState(''); const [kind, setKind] = useState(''); const [status, setStatus] = useState(''); const [gross, setGross] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scoreSort, setScoreSort] = useState(false);
  const [page, setPage] = useState(1);
  const dataset = datasets.find(item => item.id === datasetId);
  const datasetIds = useMemo(() => new Set(dataset?.conversation_ids || []), [dataset]);
  const datasetConversations = conversations.filter(item => datasetIds.has(item.id));
  const runIds = new Set(batch?.run?.conversation_ids || []);
  const scopedConversations = runId ? datasetConversations.filter(item => runIds.has(item.id)) : datasetConversations;
  const scopedEvaluations = attemptsForRun(batch?.evaluations || [], runId);
  const latest = useMemo(() => { const map = new Map<string, Evaluation>(); newestFirst(scopedEvaluations).forEach(item => { if (!map.has(item.conversation_id)) map.set(item.conversation_id, item); }); return map; }, [scopedEvaluations]);
  const pendingIds = new Set(scopedEvaluations.filter(pending).map(item => item.conversation_id));
  const filtered = scopedConversations.filter(conversation => {
    const result = latest.get(conversation.id);
    return `${conversation.external_id || ''} ${conversation.id}`.toLowerCase().includes(query.toLowerCase()) && (!lang || language(conversation) === lang) && (!kind || channel(conversation) === kind) && (!status || (result?.status || 'none') === status) && (!gross || result?.final_score?.gross_error === true);
  });
  // Sort the complete filtered result first. Pagination never changes selected IDs.
  const shown = scoreSort ? ascendingFinalScore(filtered, latest) : filtered;
  const pagination = paginate(shown, page, 25);
  const eligible = shown.filter(item => !pendingIds.has(item.id));
  const checkAll = useRef<HTMLInputElement>(null);
  const allSelected = eligible.length > 0 && eligible.every(item => selected.has(item.id));
  const anySelected = eligible.some(item => selected.has(item.id));
  useEffect(() => { if (checkAll.current) checkAll.current.indeterminate = anySelected && !allSelected; }, [anySelected, allSelected]);
  useEffect(() => { setPage(1); }, [query, lang, kind, status, gross, scoreSort, datasetId, runId]);
  useEffect(() => { setSelected(new Set()); }, [datasetId, runId]);
  const selectedIds = selectedConversationIds(scopedConversations, selected, pendingIds);
  const startIds = selectedIds.length ? selectedIds : scopedConversations.filter(item => !pendingIds.has(item.id)).map(item => item.id);
  const selectedVersion = checklists.find(item => item.id === checklistId);
  const failedIds = batch?.evaluations.filter(item => item.status === 'failed').map(item => item.id) || [];
  const counts = batch?.counts;
  const finished = counts ? counts.completed + counts.failed : 0;
  const ready = counts?.completed || 0;
  const running = (counts?.queued || 0) + (counts?.running || 0);
  const grossErrors = [...latest.values()].filter(item => item.final_score?.gross_error).length;
  return <>
    <section className="dialogue-overview">
      <div className="overview-copy">{intro}<div className="overview-actions"><button className="button primary" onClick={onImport} disabled={busy}>Импортировать <Icon name="arrow" size={18} /></button><span className="overview-note">RU / KZ / EN<br />Чаты и звонки</span></div><div className="overview-caption"><span>FREEDOM BROKER</span><span>КОНТРОЛЬ КАЧЕСТВА / CQA</span></div></div>
      <div className="overview-data">
        <div className="metrics"><div className="metric metric-total"><span>Всего диалогов</span><strong>{datasetConversations.length}</strong><small>в выбранном наборе</small></div><div className="metric metric-ready"><span>Оценено</span><strong>{ready}</strong><small>в выбранном запуске</small></div></div>
        <figure className="overview-photo"><figcaption><span className="photo-dot" />За каждым диалогом — человек.</figcaption><img src="/media/team-workspace.jpg" alt="" width="1200" height="800" decoding="async" /><div className="activity-metrics"><div className="metric metric-running"><span>В обработке</span><strong>{running}</strong><small>очередь и оценка</small></div><div className="metric alert-metric"><span>Грубые ошибки</span><strong>{grossErrors}</strong><small>требуют внимания</small></div></div></figure>
      </div>
    </section>
    <section className="panel dialogues-panel">
      <div className="panel-heading"><div><div className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</div><h2>Все диалоги <span className="count">{shown.length}</span></h2><p>{runId ? 'Показаны последние попытки выбранного запуска.' : 'Исходные диалоги выбранного набора. Для результатов выберите сохранённый запуск.'}</p></div><span className="section-index" aria-hidden="true">01 /</span></div>
      <div className="dataset-context" aria-label="Набор и запуск">
        <label className="field">Набор диалогов<select aria-label="Набор диалогов" value={datasetId} onChange={event => onDataset(event.target.value)} disabled={busy || !datasets.length}><option value="" disabled>Нет загруженных наборов</option>{datasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.kind.toUpperCase()} · {item.conversation_ids.length}</option>)}</select></label>
        <label className="field">Сохранённый запуск<select aria-label="Сохранённый запуск" value={runId} onChange={event => onRun(event.target.value)} disabled={busy || !dataset}><option value="">Все диалоги набора / новый запуск</option>{runs.filter(item => item.dataset_id === datasetId).map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString('ru-RU')} · v{item.checklist_snapshot.version} · {item.conversation_ids.length} диалогов · {item.id.slice(0, 8)}</option>)}</select></label>
      </div>
      {dataset && <p className="dataset-caption help">{dataset.conversation_ids.length} диалогов · {dataset.kind.toUpperCase()}{dataset.group_id ? ' · группа ' + dataset.group_id : ''} · {new Date(dataset.created_at).toLocaleString('ru-RU')}</p>}
      {batch && counts && <div className="run-progress" aria-live="polite"><div><strong>{finished < counts.total ? 'Обработка диалогов' : 'Обработка завершена'}</strong><span>{finished} из {counts.total}</span></div><progress value={finished} max={Math.max(1, counts.total)} /><p>Готово: {counts.completed} · ошибки: {counts.failed} · в работе: {counts.running} · в очереди: {counts.queued}</p><p className="help">Снимок запуска: {batch.run?.checklist_snapshot.name} · v{batch.run?.checklist_snapshot.version}. Результаты обновляются автоматически.</p>{failedIds.length > 0 && <button className="button secondary" disabled={busy} onClick={() => void onRetry(failedIds)}><Icon name="refresh" size={16} />Повторить только ошибки ({failedIds.length})</button>}</div>}
      <ExportPanel dataset={dataset} batch={batch} />
      <div className="filters"><label className="search-field"><Icon name="search" /><input aria-label="Поиск по ID" placeholder="Поиск по ID диалога" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="filter-field"><span className="sr-only">Язык</span><select aria-label="Язык" value={lang} onChange={event => setLang(event.target.value)}><option value="">Все языки</option>{Array.from(new Set(datasetConversations.map(language))).filter(item => item !== '—').sort().map(item => <option key={item}>{item}</option>)}</select></label><label className="filter-field"><span className="sr-only">Канал</span><select aria-label="Канал" value={kind} onChange={event => setKind(event.target.value)}><option value="">Все каналы</option>{Array.from(new Set(datasetConversations.map(channel))).map(item => <option key={item} value={item}>{channelLabel(item)}</option>)}</select></label><label className="filter-field"><span className="sr-only">Статус</span><select aria-label="Статус" value={status} onChange={event => setStatus(event.target.value)}><option value="">Все статусы</option>{Object.entries(statusLabels).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label className="check-label"><input type="checkbox" checked={gross} onChange={event => setGross(event.target.checked)} />Грубая ошибка</label></div>
      {!dataset ? <Empty title="Начните с первого набора" action={<button className="button primary" onClick={onImport}><Icon name="upload" />Загрузить диалоги</button>}>Импортируйте JSON или получите набор с сервера организаторов.</Empty> : shown.length === 0 ? <Empty title="Ничего не найдено" action={<button className="button secondary" onClick={() => { setQuery(''); setLang(''); setKind(''); setStatus(''); setGross(false); }}>Сбросить фильтры</button>}>Измените фильтры или выберите другой запуск.</Empty> : <div className="table-scroll"><table className="dialogue-table"><thead><tr><th className="checkbox-cell"><input ref={checkAll} type="checkbox" aria-label="Выбрать все отфильтрованные диалоги на всех страницах" checked={allSelected} disabled={!eligible.length || busy} onChange={event => setSelected(previous => { const next = new Set(previous); eligible.forEach(item => event.target.checked ? next.add(item.id) : next.delete(item.id)); return next; })} /></th><th>Диалог</th><th>Язык / канал</th><th>Статус</th><th aria-sort={scoreSort ? 'ascending' : 'none'}><button className="score-sort" onClick={() => setScoreSort(value => !value)}>Итог с учётом апелляций <span aria-hidden="true">{scoreSort ? '↑' : '↕'}</span></button></th><th>Грубая ошибка</th><th>Модель / версия / время</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{pagination.items.map(conversation => {
        const result = latest.get(conversation.id); const isPending = pendingIds.has(conversation.id); const provenance = result ? evaluationProvenance(result) : null;
        return <tr key={conversation.id} className={selected.has(conversation.id) ? 'is-selected' : ''}><td className="checkbox-cell"><input type="checkbox" aria-label={`Выбрать ${conversation.external_id || conversation.id}`} checked={selected.has(conversation.id)} disabled={isPending || busy} onChange={event => setSelected(previous => { const next = new Set(previous); event.target.checked ? next.add(conversation.id) : next.delete(conversation.id); return next; })} /></td><td><a className="dialogue-link" href={`#dialog/${encodeURIComponent(conversation.id)}`}>{conversation.external_id || conversation.id}</a><span className="table-subtitle">{conversation.messages.length} сообщений</span></td><td><span className="language-badge">{language(conversation)}</span><span className="table-subtitle">{channelLabel(channel(conversation))}</span></td><td><Status status={result?.status || 'none'} />{result?.status === 'failed' && <span className="table-subtitle failure-code" title={result.error?.message}>{result.error?.code}</span>}</td><td><span className="table-score">{result?.status === 'completed' ? result.final_score?.total == null ? 'Не рассчитан' : <>{numberLabel(result.final_score.total)}<small> / 100</small></> : '—'}</span></td><td>{result?.final_score ? <span className={result.final_score.gross_error ? 'gross-badge' : 'muted'}>{result.final_score.gross_error ? 'Выявлена' : 'Нет'}</span> : '—'}</td><td className="result-provenance">{provenance ? <><span>{provenance.model}</span><span className="table-subtitle">{provenance.checklist}</span><span className="table-subtitle">{provenance.completedAt ? new Date(provenance.completedAt).toLocaleString('ru-RU') : result?.status === 'completed' ? 'Время завершения не записано' : `Запуск: ${new Date(provenance.startedAt).toLocaleString('ru-RU')}`}</span></> : '—'}</td><td><a className="icon-button" aria-label={`Открыть ${conversation.external_id || conversation.id}`} href={`#dialog/${encodeURIComponent(conversation.id)}`}><Icon name="arrow" size={18} /></a></td></tr>;
      })}</tbody></table></div>}
      <div className="table-pagination"><span>{shown.length ? (pagination.page - 1) * 25 + 1 : 0}–{Math.min(pagination.page * 25, shown.length)} из {shown.length}</span><div><button className="button secondary" disabled={pagination.page <= 1} onClick={() => setPage(pagination.page - 1)}>Назад</button><span>Страница {pagination.page} / {pagination.pages}</span><button className="button secondary" disabled={pagination.page >= pagination.pages} onClick={() => setPage(pagination.page + 1)}>Далее</button></div></div>
      <div className="evaluation-toolbar"><div><strong>Выбрано: {selectedIds.length}</strong>{selectedIds.length > 0 && <button className="text-button" onClick={() => setSelected(new Set())}>Снять выбор</button>}<p className="help">Выбор сохраняется при фильтрации и сортировке. До 100 диалогов за запуск.</p></div><label className="field compact">Версия чек-листа<select aria-label="Версия чек-листа для оценки" value={checklistId} onChange={event => onChecklist(event.target.value)} disabled={busy || !checklists.length}><option value="" disabled>Выберите чек-лист</option>{checklists.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label><button className="button primary" disabled={busy || !startIds.length || startIds.length > 100 || !selectedVersion || !dataset} onClick={() => void onEvaluate(startIds)}><Icon name="play" size={17} />{busy ? 'Запускаем…' : selectedIds.length ? 'Оценить выбранные (' + selectedIds.length + ')' : runId ? 'Новый запуск (' + startIds.length + ')' : 'Оценить набор (' + startIds.length + ')'}</button></div>
    </section>
  </>;
}
