import { useMemo, useRef, useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { ChecklistVersion, Conversation, Evaluation } from './types';
import { channel, channelLabel, language, newestFirst, numberLabel, pending, statusLabels } from './display';
import { Empty, ErrorNotice, Icon, Status } from './components';
import { api, friendlyError } from './api';
import type { ExportFormat } from './downloads';

interface Props { intro: ReactNode; conversations: Conversation[]; evaluations: Evaluation[]; checklists: ChecklistVersion[]; checklistId: string; onChecklist: (id: string) => void; onEvaluate: (ids: string[]) => Promise<void>; busy: boolean; onImport: () => void }
export function DialogueList({ intro, conversations, evaluations, checklists, checklistId, onChecklist, onEvaluate, busy, onImport }: Props) {
  const [query, setQuery] = useState(''); const [lang, setLang] = useState(''); const [kind, setKind] = useState(''); const [status, setStatus] = useState(''); const [gross, setGross] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState('');
  const [exportNotice, setExportNotice] = useState('');
  const exportGuard = useRef(false);

  async function download(format: ExportFormat) {
    if (exportGuard.current) return;
    exportGuard.current = true; setExporting(format); setExportError(''); setExportNotice('');
    try {
      const file = await api.exportEvaluations(format);
      const url = URL.createObjectURL(file.blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.filename;
      document.body.append(link);
      try { link.click(); } finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
      setExportNotice(`Файл ${format.toUpperCase()} готов к сохранению.`);
    } catch (error) { setExportError(friendlyError(error)); }
    finally { exportGuard.current = false; setExporting(null); }
  }
  const latest = useMemo(() => { const map = new Map<string, Evaluation>(); newestFirst(evaluations).forEach(e => { if (!map.has(e.conversation_id)) map.set(e.conversation_id, e); }); return map; }, [evaluations]);
  const pendingIds = new Set(evaluations.filter(pending).map(e => e.conversation_id));
  const shown = conversations.filter(c => {
    const result = latest.get(c.id);
    return `${c.external_id || ''} ${c.id}`.toLowerCase().includes(query.toLowerCase()) && (!lang || language(c) === lang) && (!kind || channel(c) === kind) && (!status || (result?.status || 'none') === status) && (!gross || result?.final_score?.gross_error === true);
  });
  const eligible = shown.filter(c => !pendingIds.has(c.id));
  const checkAll = useRef<HTMLInputElement>(null);
  const allSelected = eligible.length > 0 && eligible.every(c => selected.has(c.id));
  const anySelected = eligible.some(c => selected.has(c.id));
  useEffect(() => { if (checkAll.current) checkAll.current.indeterminate = anySelected && !allSelected; }, [anySelected, allSelected]);
  const selectedIds = conversations.filter(c => selected.has(c.id) && !pendingIds.has(c.id)).map(c => c.id);
  const selectedVersion = checklists.find(c => c.id === checklistId);
  const ready = [...latest.values()].filter(e => e.status === 'completed').length;
  const running = [...latest.values()].filter(pending).length;
  const errors = [...latest.values()].filter(e => e.final_score?.gross_error).length;
  return <>
    <section className="dialogue-overview">
      <div className="overview-copy">{intro}<div className="overview-actions"><button className="button primary" onClick={onImport}>Импортировать <Icon name="arrow" size={18} /></button><span className="overview-note">RU / KZ / EN<br />Чаты и звонки</span></div><div className="overview-caption"><span>FREEDOM BROKER</span><span>КОНТРОЛЬ КАЧЕСТВА / CQA</span></div></div>
      <div className="overview-data">
        <div className="metrics"><div className="metric metric-total"><span>Всего диалогов</span><strong>{conversations.length}</strong><small>в рабочем пространстве</small></div><div className="metric metric-ready"><span>Оценено</span><strong>{ready}</strong><small>результат готов</small></div></div>
        <figure className="overview-photo"><figcaption><span className="photo-dot" />За каждым диалогом — человек.</figcaption><img src="/media/team-workspace.jpg" alt="" width="1200" height="800" decoding="async" /><div className="activity-metrics"><div className="metric metric-running"><span>В обработке</span><strong>{running}</strong><small>очередь и оценка</small></div><div className="metric alert-metric"><span>Грубые ошибки</span><strong>{errors}</strong><small>требуют внимания</small></div></div></figure>
      </div>
    </section>
    <section className="panel dialogues-panel">
      <div className="panel-heading"><div><div className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</div><h2>Все диалоги <span className="count">{shown.length}</span></h2><p>Выберите диалоги и запустите оценку по нужной версии чек-листа.</p></div><span className="section-index" aria-hidden="true">01 /</span></div>
      <div className="export-toolbar" aria-label="Выгрузка результатов"><div><strong>Выгрузка результатов</strong><p className="help">Все сохранённые оценки: JSON с историей решений или CSV с итогами. Фильтры списка не ограничивают выгрузку.</p></div><div className="export-actions">{(['json', 'csv'] as const).map(format => <button key={format} className="button secondary" disabled={exporting !== null || !evaluations.length} onClick={() => void download(format)}>{exporting === format ? 'Готовим файл…' : `Скачать ${format.toUpperCase()}`}</button>)}</div></div>
      {exportError && <ErrorNotice message={exportError} />}
      {exportNotice && <p className="export-notice" role="status">{exportNotice}</p>}
      <div className="filters"><label className="search-field"><Icon name="search" /><input aria-label="Поиск по ID" placeholder="Поиск по ID диалога" value={query} onChange={e => setQuery(e.target.value)} /></label><label className="filter-field"><span className="sr-only">Язык</span><select aria-label="Язык" value={lang} onChange={e => setLang(e.target.value)}><option value="">Все языки</option>{Array.from(new Set(conversations.map(language))).filter(x => x !== '—').sort().map(x => <option key={x}>{x}</option>)}</select></label><label className="filter-field"><span className="sr-only">Канал</span><select aria-label="Канал" value={kind} onChange={e => setKind(e.target.value)}><option value="">Все каналы</option>{Array.from(new Set(conversations.map(channel))).map(x => <option key={x} value={x}>{channelLabel(x)}</option>)}</select></label><label className="filter-field"><span className="sr-only">Статус</span><select aria-label="Статус" value={status} onChange={e => setStatus(e.target.value)}><option value="">Все статусы</option>{Object.entries(statusLabels).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label className="check-label"><input type="checkbox" checked={gross} onChange={e => setGross(e.target.checked)} />Грубая ошибка</label></div>
      {conversations.length === 0 ? <Empty title="Начните с первого диалога" action={<button className="button primary" onClick={onImport}><Icon name="upload" />Импортировать диалоги</button>}>Загрузите JSON или получите DEV с сервера организаторов.</Empty> : shown.length === 0 ? <Empty title="Ничего не найдено" action={<button className="button secondary" onClick={() => { setQuery(''); setLang(''); setKind(''); setStatus(''); setGross(false); }}>Сбросить фильтры</button>}>Попробуйте другой ID или измените фильтры.</Empty> : <div className="table-scroll"><table className="dialogue-table"><thead><tr><th className="checkbox-cell"><input ref={checkAll} type="checkbox" aria-label="Выбрать все видимые диалоги" checked={allSelected} disabled={!eligible.length || busy} onChange={e => setSelected(prev => { const next = new Set(prev); eligible.forEach(c => e.target.checked ? next.add(c.id) : next.delete(c.id)); return next; })} /></th><th>Диалог</th><th>Язык</th><th>Канал</th><th>Статус</th><th>Итоговый балл</th><th>Грубая ошибка</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{shown.map(c => { const result = latest.get(c.id); const isPending = pendingIds.has(c.id); return <tr key={c.id} className={selected.has(c.id) ? 'is-selected' : ''}><td className="checkbox-cell"><input type="checkbox" aria-label={`Выбрать ${c.external_id || c.id}`} checked={selected.has(c.id)} disabled={isPending || busy} onChange={e => setSelected(prev => { const next = new Set(prev); e.target.checked ? next.add(c.id) : next.delete(c.id); return next; })} /></td><td><a className="dialogue-link" href={`#dialog/${encodeURIComponent(c.id)}`}>{c.external_id || c.id}</a><span className="table-subtitle">{c.messages.length} сообщений{result ? ` · чек-лист v${result.checklist_snapshot.version}` : ''}</span></td><td><span className="language-badge">{language(c)}</span></td><td><span className="channel-label"><Icon name={channel(c) === 'voice call' ? 'phone' : 'chat'} size={16} />{channelLabel(channel(c))}</span></td><td><Status status={result?.status || 'none'} /></td><td><span className="table-score">{result?.status === 'completed' ? result.final_score?.total == null ? 'Не рассчитан' : <>{numberLabel(result.final_score.total)}<small> / 100</small></> : '—'}</span></td><td>{result?.final_score ? <span className={result.final_score.gross_error ? 'gross-badge' : 'muted'}>{result.final_score.gross_error ? 'Выявлена' : 'Нет'}</span> : '—'}</td><td><a className="icon-button" aria-label={`Открыть ${c.external_id || c.id}`} href={`#dialog/${encodeURIComponent(c.id)}`}><Icon name="arrow" size={18} /></a></td></tr>; })}</tbody></table></div>}
      <div className="evaluation-toolbar"><div><strong>Выбрано: {selectedIds.length}</strong>{selectedIds.length > 0 && <button className="text-button" onClick={() => setSelected(new Set())}>Снять выбор</button>}<p className="help">Выбор сохраняется при фильтрации. До 100 диалогов за запуск.</p></div><label className="field compact">Версия чек-листа<select aria-label="Версия чек-листа для оценки" value={checklistId} onChange={e => onChecklist(e.target.value)} disabled={busy || !checklists.length}><option value="" disabled>Выберите чек-лист</option>{checklists.map(c => <option key={c.id} value={c.id}>{c.name} · v{c.version}</option>)}</select></label><button className="button primary" disabled={busy || !selectedIds.length || selectedIds.length > 100 || !selectedVersion} onClick={() => void onEvaluate(selectedIds)}><Icon name="play" size={17} />{busy ? 'Запускаем…' : 'Оценить выбранные'}</button></div>
    </section>
  </>;
}
