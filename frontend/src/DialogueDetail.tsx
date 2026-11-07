import { useEffect, useRef, useState } from 'react';
import type { ChecklistVersion, Conversation, Evaluation } from './types';
import { channel, channelLabel, language, newestFirst, numberLabel, pending, roleLabels, safeAudioUrl, totalLabel, valueLabel } from './display';
import { Empty, ErrorNotice, Icon, Status } from './components';
import { AppealsPanel } from './Appeals';

interface Props {
  conversation: Conversation; evaluations: Evaluation[]; checklist: ChecklistVersion | undefined;
  onEvaluate: (ids: string[]) => Promise<void>; busy: boolean; onUpdated: () => Promise<void>;
  reviewMode: boolean; initialEvaluationId?: string; reviewOnly?: boolean;
}
export function DialogueDetail({ conversation, evaluations, checklist, onEvaluate, busy, onUpdated, reviewMode, initialEvaluationId, reviewOnly }: Props) {
  const history = newestFirst(evaluations.filter(e => e.conversation_id === conversation.id));
  const [evaluationId, setEvaluationId] = useState(initialEvaluationId || history[0]?.id || '');
  const evaluation = history.find(e => e.id === evaluationId) || history[0];
  useEffect(() => { if (evaluation && evaluationId !== evaluation.id) setEvaluationId(evaluation.id); }, [evaluationId, evaluation?.id]);
  const [view, setView] = useState<'final' | 'original'>('final');
  const [highlight, setHighlight] = useState<string[]>([]);
  const [evidenceError, setEvidenceError] = useState('');
  const [criterionId, setCriterionId] = useState<string>();
  const messageRefs = useRef(new Map<string, HTMLLIElement>());
  const appealRef = useRef<HTMLElement>(null);
  useEffect(() => { setHighlight([]); setEvidenceError(''); setCriterionId(undefined); }, [evaluation?.id]);
  const reveal = (ids: string[]) => {
    const valid = ids.filter(id => messageRefs.current.has(id));
    setHighlight(valid);
    setEvidenceError(valid.length !== ids.length || !valid.length ? 'Часть ссылок на сообщения отсутствует в транскрипте. Проверьте доказательства на сервере.' : '');
    if (valid[0]) {
      messageRefs.current.get(valid[0])?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
      messageRefs.current.get(valid[0])?.focus({ preventScroll: true });
    }
  };
  const score = view === 'original' ? evaluation?.original_score : evaluation?.final_score;
  const results = view === 'original' ? evaluation?.ai_draft?.criteria : evaluation?.final_criteria;
  const audio = safeAudioUrl(conversation.metadata.audio_url);
  const isRunning = history.some(pending);
  const openAppeal = (id: string) => { setCriterionId(id); window.setTimeout(() => appealRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0); };
  return <>
    <div className="detail-heading"><div><a className="back-link" href="#dialogs">← Все диалоги</a><h1>{conversation.external_id || conversation.id}</h1><div className="detail-meta"><span className="language-badge">{language(conversation)}</span><span>{channelLabel(channel(conversation))}</span><span>{conversation.messages.length} сообщений</span><Status status={evaluation?.status || 'none'} /></div></div><div className="detail-actions"><span className="help">Новая оценка: {checklist ? `${checklist.name} · v${checklist.version}` : 'выберите чек-лист в списке'}</span><button className="button primary" disabled={busy || isRunning || !checklist} onClick={() => { void onEvaluate([conversation.id]).then(() => setEvaluationId('')); }}><Icon name="play" size={17} />{busy ? 'Запускаем…' : evaluation ? 'Оценить повторно' : 'Оценить диалог'}</button></div></div>
    <div className="detail-grid">
      <section className="panel transcript-panel" aria-label="Транскрипт диалога"><div className="panel-heading"><div><h2>Транскрипт</h2><p>Исходные сообщения и время</p></div><span className="small-tag">{language(conversation)}</span></div>
        {audio && <div className="audio-block"><div className="audio-title"><Icon name="phone" size={18} /><strong>Запись звонка</strong><a href={audio} target="_blank" rel="noopener noreferrer" className="text-button">Открыть запись <Icon name="link" size={14} /></a></div><audio controls preload="none" src={audio}>Ваш браузер не поддерживает аудиоплеер.</audio><p className="help">Точное соответствие времени записи и транскрипта не предоставлено. Переходы ведут к сообщениям.</p></div>}
        {channel(conversation) === 'voice call' && !audio && <div className="notice">Ссылка на аудиозапись отсутствует или имеет неподдерживаемый формат.</div>}
        {evidenceError && <ErrorNotice message={evidenceError} />}
        {highlight.length > 0 && <div className="evidence-toolbar"><span>Выделено сообщений: {highlight.length}</span><button className="text-button" onClick={() => setHighlight([])}>Снять выделение</button></div>}
        <ol className="messages">{conversation.messages.map((m, index) => <li key={m.id} ref={element => { if (element) messageRefs.current.set(m.id, element); else messageRefs.current.delete(m.id); }} tabIndex={-1} className={`message message-${['client', 'operator', 'bot', 'system'].includes(m.role) ? m.role : 'other'} ${highlight.includes(m.id) ? 'highlighted' : ''}`}><div className="message-header"><span className="message-role">{roleLabels[m.role] || m.role}</span><span className="message-index">#{index + 1}</span>{m.timestamp != null && <span className="message-time">{m.timestamp}</span>}</div><p>{m.text}</p></li>)}</ol>
      </section>
      <section className="results-column" aria-label="Оценка диалога">
        {reviewOnly && evaluation ? <AppealsPanel key={evaluation.id} evaluation={evaluation} conversation={conversation} onUpdated={onUpdated} onEvidence={reveal} reviewMode={reviewMode} /> : <>
        <div className="panel evaluation-panel"><div className="panel-heading"><div><h2>Результат оценки</h2><p>{evaluation ? `${evaluation.checklist_snapshot.name} · версия ${evaluation.checklist_snapshot.version}` : 'Выберите версию чек-листа и запустите оценку'}</p></div></div>
          {history.length > 1 && <label className="field history-field">История запусков<select aria-label="История оценок" value={evaluation?.id || ''} onChange={e => setEvaluationId(e.target.value)}>{history.map((e, i) => <option key={e.id} value={e.id}>{i === 0 ? 'Последняя · ' : ''}{new Date(e.created_at).toLocaleString('ru-RU')} · v{e.checklist_snapshot.version} · {e.id.slice(0, 8)}</option>)}</select></label>}
          {!evaluation ? <Empty title="Диалог ещё не оценён">Результат появится здесь после обработки сервером.</Empty> : pending(evaluation) ? <div className="processing"><span className="spinner" /><h3>{evaluation.status === 'queued' ? 'Ожидает своей очереди' : 'Оцениваем диалог'}</h3><p>Проверяем критерии и собираем доказательства. Статус обновляется автоматически.</p></div> : evaluation.status === 'failed' ? <div className="result-error"><ErrorNotice message={evaluation.error?.message || 'Оценка не завершена.'} /><p className="help">Можно повторить запуск после устранения причины. Предыдущие результаты сохранены в истории.</p></div> : <>
            <div className="score-comparison"><div><span>Автоматический итог</span><strong>{totalLabel(evaluation.original_score?.total)}<small>{evaluation.original_score?.total != null ? '/ 100' : ''}</small></strong></div><div className="final-score"><span>Окончательный итог</span><strong>{totalLabel(evaluation.final_score?.total)}<small>{evaluation.final_score?.total != null ? '/ 100' : ''}</small></strong></div></div>
            <div className={`gross-notice ${score?.gross_error ? 'has-error' : ''}`}><Icon name={score?.gross_error ? 'appeals' : 'check'} size={19} /><span>{score?.gross_error ? 'Выявлена грубая ошибка' : 'Грубых ошибок не выявлено'}<small>{view === 'original' ? 'Исходный автоматический флаг.' : 'Окончательный флаг.'} Итоговый балл показан отдельно.</small></span></div>
            <div className="segmented result-tabs"><button className={view === 'final' ? 'active' : ''} onClick={() => setView('final')}>Окончательный результат</button><button className={view === 'original' ? 'active' : ''} onClick={() => setView('original')}>Исходный AI</button></div>
            {score?.total == null && <p className="notice">Итог не рассчитан: нет применимых критериев с положительным весом.</p>}
            {!!score?.sections.length && <div className="section-scores" aria-label="Результаты секций">{score.sections.map(section => <div key={section.section_id}><span>{evaluation.checklist_snapshot.sections.find(s => s.id === section.section_id)?.title || section.section_id}</span><strong>{section.score == null ? 'Без баллов' : `${numberLabel(section.score)}%`}</strong></div>)}</div>}
            <p className="score-help">Баллы и веса критериев показаны до перераспределения N/A. Итог и результаты секций рассчитаны сервером.</p>
          </>}
        </div>
        {evaluation?.status === 'completed' && evaluation.checklist_snapshot.criteria.map((criterion, index) => {
          const result = results?.find(r => r.criterion_id === criterion.id); const points = score?.criteria.find(c => c.criterion_id === criterion.id);
          return <article className="panel criterion-result" key={criterion.id}><div className="criterion-heading"><span className="criterion-number">{String(index + 1).padStart(2, '0')}</span><h3>{criterion.title}</h3><span className={`answer-badge ${result?.value === false ? 'negative' : result?.value === 'N/A' ? 'na' : ''}`}>{valueLabel(result?.value)}</span></div>
            <div className="criterion-points"><span>Баллы: <strong>{criterion.answer_type === 'flag' ? 'Без баллов' : numberLabel(points?.earned_points)}</strong></span><span>Вес: <strong>{numberLabel(points?.weight ?? criterion.weight)}</strong></span></div>
            {result ? <><p className="reasoning">{result.reasoning}</p>{result.value === 'N/A' && <div className="na-reason"><strong>Причина N/A</strong><p>{result.reasoning}</p>{criterion.na_condition && <small>Условие: {criterion.na_condition}</small>}</div>}<div className="evidence-block"><span className="eyebrow">{result.value === false ? 'Проверенный участок' : result.value === 'N/A' ? 'Контекст неприменимости' : 'Доказательства'}</span>{result.evidence_message_ids.length ? <><button className="evidence-link" onClick={() => reveal(result.evidence_message_ids)}><Icon name="chat" size={16} />Показать {result.evidence_message_ids.length === 1 ? 'сообщение' : `сообщения (${result.evidence_message_ids.length})`}<Icon name="arrow" size={15} /></button><div className="evidence-chips">{result.evidence_message_ids.map(id => { const msgIndex = conversation.messages.findIndex(m => m.id === id); const msg = conversation.messages[msgIndex]; return <button key={id} onClick={() => reveal([id])} disabled={!msg} title={msg?.text}>{msg ? `#${msgIndex + 1} · ${roleLabels[msg.role] || msg.role}${msg.timestamp != null ? ` · ${msg.timestamp}` : ''}` : 'Сообщение не найдено'}</button>; })}</div></> : <p className="help">Сервер не вернул ссылки на проверенные сообщения.</p>}</div></> : <p className="notice error">Для критерия не получен результат. Обновите данные.</p>}
            <button className="text-button appeal-link" onClick={() => openAppeal(criterion.id)}>Оспорить критерий <Icon name="arrow" size={15} /></button>
          </article>;
        })}
        </>}
      </section>
    </div>
    {!reviewOnly && evaluation?.status === 'completed' && <section ref={appealRef} className="appeals-section" aria-label="Апелляции по оценке"><AppealsPanel key={evaluation.id} evaluation={evaluation} conversation={conversation} onUpdated={onUpdated} onEvidence={reveal} reviewMode={reviewMode} initialCriterionId={criterionId} /></section>}
  </>;
}
