import { useEffect, useRef, useState } from 'react';
import { api, friendlyError } from './api';
import { ErrorNotice, Icon } from './components';
import { saveDownload, type ExportFormat } from './downloads';
import type { Dataset, EvaluationBatch, ExportIssue, OfficialExportReport } from './types';

export function ExportPanel({ dataset, batch }: { dataset?: Dataset; batch: EvaluationBatch | null }) {
  const [team, setTeam] = useState('cuda cuda');
  const [report, setReport] = useState<OfficialExportReport | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const guard = useRef(false);
  const sequence = useRef(0);
  // Invalidate preflight when the selected run, team or its saved results change.
  const revision = batch?.evaluations.map(item => `${item.id}:${item.updated_at}:${item.status}`).join('|');
  useEffect(() => { ++sequence.current; setReport(null); setError(''); setNotice(''); }, [batch?.id, revision, team]);

  async function official(download: boolean) {
    if (!batch || guard.current || !team.trim()) return;
    const current = sequence.current;
    guard.current = true; setBusy(download ? 'official' : 'validate'); setError(''); setNotice('');
    try {
      const result = await api.validateOfficialExport(batch.id, team.trim());
      if (current !== sequence.current) return;
      setReport(result);
      if (download && result.valid) {
        const file = await api.exportOfficial(batch.id, team.trim());
        if (current !== sequence.current) return;
        saveDownload(file); setNotice('results.json готов к сохранению.');
      }
    } catch (err) { if (current === sequence.current) setError(friendlyError(err)); }
    finally { guard.current = false; setBusy(''); }
  }
  async function archive(format: ExportFormat) {
    if (!batch || guard.current) return;
    const current = sequence.current;
    guard.current = true; setBusy(format); setError(''); setNotice('');
    try {
      const file = await api.exportEvaluations(format, batch.id);
      if (current !== sequence.current) return;
      saveDownload(file); setNotice(`Архив запуска (${format.toUpperCase()}) готов к сохранению.`);
    } catch (err) { if (current === sequence.current) setError(friendlyError(err)); }
    finally { guard.current = false; setBusy(''); }
  }
  const issues = (items: ExportIssue[]) => <ul className="export-issues">{items.map((item, index) => <li key={`${item.code}:${item.path}:${index}`}><strong>{item.conversation_id ? `Диалог ${item.conversation_id}` : item.path || 'Файл'}</strong><span>{item.message}</span></li>)}</ul>;
  return <section className="workflow-export" aria-label="Выгрузка выбранного запуска">
    <div className="workflow-step-title"><span>03</span><div><h3>Выгрузить результат</h3><p>Один набор и один запуск. Сервер проверит файл перед скачиванием.</p></div></div>
    <div className="official-export-controls"><label className="field">Название команды<input aria-label="Название команды для выгрузки" value={team} maxLength={200} disabled={!!busy} onChange={event => setTeam(event.target.value)} /></label><button className="button secondary" disabled={!batch || !!busy || !team.trim()} onClick={() => void official(false)}>{busy === 'validate' ? 'Проверяем…' : 'Проверить файл'}</button><button className="button primary" disabled={!batch || !!busy || !team.trim()} onClick={() => void official(true)}><Icon name="upload" size={17} />{busy === 'official' ? 'Готовим…' : 'Скачать results.json'}</button></div>
    <p className="help">{dataset ? `${dataset.name} · ${dataset.kind.toUpperCase()}` : 'Выберите набор'}{batch ? ` · запуск ${batch.id.slice(0, 8)}` : ' · затем выберите запуск оценки'}. Официальный формат для организаторов.</p>
    {report && <div className={`export-validation ${report.valid ? report.warnings.length ? 'warning' : 'valid' : 'invalid'}`} role="status"><strong>{report.valid ? report.warnings.length ? 'Файл допустим с предупреждениями' : 'Файл прошёл проверку' : 'Исправьте ошибки перед выгрузкой'}</strong><p>Ожидается: {report.counts.expected} · записей: {report.counts.actual} · OK: {report.counts.ok} · ERROR: {report.counts.error}</p>{issues(report.errors)}{issues(report.warnings)}{report.valid && report.counts.error > 0 && <p>Записи со status: error разрешены форматом. Они остаются в файле с явной ошибкой.</p>}</div>}
    {error && <ErrorNotice message={error} />}{notice && <p className="export-notice" role="status">{notice}</p>}
    <details className="archive-export"><summary>Архив запуска для внутренней работы</summary><p className="help">История оценок и решений только выбранного запуска. Это отдельный формат, который не предназначен для сдачи организаторам.</p><div className="export-actions">{(['json', 'csv'] as const).map(format => <button key={format} className="button secondary" disabled={!batch || !!busy} onClick={() => void archive(format)}>{busy === format ? 'Готовим…' : `Архив ${format.toUpperCase()}`}</button>)}</div></details>
  </section>;
}
