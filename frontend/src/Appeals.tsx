import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, friendlyError } from './api';
import type {
  AnswerValue, Appeal, AppealCreate, Conversation, Criterion, CriterionResult,
  Evaluation, ReviewDecision, ReviewDecisionCreate,
} from './types';
import './appeals.css';

type AppealsPanelProps = {
  evaluation: Evaluation;
  conversation: Conversation;
  onUpdated: () => Promise<void>;
  onEvidence: (ids: string[]) => void;
  reviewMode: boolean;
  initialCriterionId?: string;
};

const roleLabels: Record<string, string> = {
  operator: 'Оператор', client: 'Клиент', bot: 'Бот', system: 'Система',
};

function answerLabel(value: AnswerValue | undefined): string {
  if (value === undefined) return 'Нет результата';
  if (value === 'N/A') return 'N/A — неприменимо';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  return String(value);
}

function scoreLabel(value: number | null | undefined, flag: boolean): string {
  if (flag) return 'Без баллов';
  return value == null ? 'Не рассчитано' : `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} балла`;
}

function EvidenceLinks({ result, conversation, onEvidence }: {
  result: CriterionResult | null | undefined;
  conversation: Conversation;
  onEvidence: (ids: string[]) => void;
}) {
  const messages = new Map(conversation.messages.map((message) => [message.id, message]));
  if (!result?.evidence_message_ids.length) return <p className="appeals-muted">Ссылки на сообщения не переданы.</p>;
  return <div className="appeals-evidence">
    <span className="appeals-caption">Доказательства / проверенный участок</span>
    {result.evidence_message_ids.map((id) => {
      const message = messages.get(id);
      return <button type="button" className="appeals-evidence-link" key={id}
        disabled={!message} onClick={() => onEvidence([id])}>
        <strong>{message ? `${roleLabels[message.role] ?? message.role}${message.timestamp ? ` · ${message.timestamp}` : ''}` : 'Сообщение недоступно'}</strong>
        {message && <span>{message.text}</span>}
      </button>;
    })}
    {result.evidence_message_ids.length > 1 && <button type="button" className="button secondary appeals-small-button"
      onClick={() => onEvidence(result.evidence_message_ids)}>
      Показать весь участок ({result.evidence_message_ids.length})
    </button>}
  </div>;
}

function ReviewForm({ appeal, criterion, current, conversation, busy, onSave }: {
  appeal: Appeal;
  criterion: Criterion;
  current: CriterionResult | undefined;
  conversation: Conversation;
  busy: boolean;
  onSave: (payload: ReviewDecisionCreate) => Promise<void>;
}) {
  const formId = useId();
  const [action, setAction] = useState<'confirm' | 'change'>('confirm');
  const [reason, setReason] = useState('');
  const [reviewer, setReviewer] = useState('Проверяющий');
  const initialValue = current?.value ?? (criterion.answer_type === 'scale' ? criterion.scale[0]?.value ?? 0 : false);
  const [value, setValue] = useState<AnswerValue>(initialValue);
  const [reasoning, setReasoning] = useState(current?.reasoning ?? '');
  const [evidence, setEvidence] = useState<string[]>(current?.evidence_message_ids ?? []);
  const [validation, setValidation] = useState('');
  const values: { value: AnswerValue; label: string }[] = criterion.answer_type === 'scale'
    ? criterion.scale.map((step) => ({ value: step.value, label: `${step.value} — ${step.label}` }))
    : [{ value: true, label: 'Да' }, { value: false, label: 'Нет' }];
  if (criterion.allow_na) values.push({ value: 'N/A', label: 'N/A — неприменимо' });

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setValidation('');
    if (!reviewer.trim() || !reason.trim()) {
      setValidation('Укажите проверяющего и причину решения.');
      return;
    }
    if (action === 'change' && (!reasoning.trim() || !evidence.length)) {
      setValidation('Для новой оценки нужны обоснование и хотя бы одно сообщение-доказательство.');
      return;
    }
    if (action === 'change' && !values.some((option) => option.value === value)) {
      setValidation('Выберите допустимое значение из шкалы критерия.');
      return;
    }
    const payload: ReviewDecisionCreate = {
      outcome: action === 'change' ? 'accepted' : 'rejected',
      reviewer: reviewer.trim(),
      reason: reason.trim(),
      ...(action === 'change' ? { replacement: {
        criterion_id: appeal.criterion_id,
        value,
        reasoning: reasoning.trim(),
        evidence_message_ids: evidence,
      } } : {}),
    };
    await onSave(payload);
  }

  return <form className="appeals-review-form" onSubmit={submit}>
    <h4>Решение проверяющего</h4>
    <fieldset className="appeals-choice" disabled={busy}>
      <legend>Что сделать с оценкой?</legend>
      <label><input type="radio" name={`${formId}-action`} checked={action === 'confirm'}
        onChange={() => setAction('confirm')} /> Подтвердить оценку</label>
      <label><input type="radio" name={`${formId}-action`} checked={action === 'change'}
        onChange={() => setAction('change')} /> Изменить оценку</label>
    </fieldset>
    <p className="appeals-muted">{action === 'confirm'
      ? 'Апелляция будет отклонена. Текущая оценка останется без изменений.'
      : 'Апелляция будет принята. Сервер сохранит решение и пересчитает окончательный результат.'}</p>
    {action === 'change' && <div className="appeals-replacement">
      <label className="field" htmlFor={`${formId}-value`}>Новый ответ
        <select id={`${formId}-value`} value={JSON.stringify(value)} disabled={busy}
          onChange={(event) => setValue(JSON.parse(event.target.value) as AnswerValue)}>
          {values.map((option) => <option key={JSON.stringify(option.value)} value={JSON.stringify(option.value)}>{option.label}</option>)}
        </select>
      </label>
      {value === 'N/A' && <p className="appeals-note">Условие N/A: {criterion.na_condition || 'Не указано в чек-листе'}. В обосновании укажите, почему оно выполнено.</p>}
      <label className="field" htmlFor={`${formId}-reasoning`}>Обоснование новой оценки
        <textarea id={`${formId}-reasoning`} required rows={3} value={reasoning} disabled={busy}
          onChange={(event) => setReasoning(event.target.value)} />
      </label>
      <fieldset className="appeals-message-picker" disabled={busy}>
        <legend>Доказательства или проверенный участок · выбрано {evidence.length}</legend>
        <p className="appeals-muted">Для пропущенного действия отметьте сообщения, где оно ожидалось; для N/A — сообщения, подтверждающие неприменимость.</p>
        <div className="appeals-message-options">
          {conversation.messages.map((message) => <label key={message.id} className="appeals-message-option">
            <input type="checkbox" checked={evidence.includes(message.id)} onChange={(event) => {
              setEvidence((ids) => event.target.checked ? [...ids, message.id] : ids.filter((id) => id !== message.id));
            }} />
            <span><strong>{roleLabels[message.role] ?? message.role}{message.timestamp ? ` · ${message.timestamp}` : ''}</strong><span>{message.text}</span></span>
          </label>)}
        </div>
      </fieldset>
    </div>}
    <label className="field" htmlFor={`${formId}-reviewer`}>Проверяющий
      <input id={`${formId}-reviewer`} required value={reviewer} disabled={busy}
        onChange={(event) => setReviewer(event.target.value)} autoComplete="name" />
    </label>
    <label className="field" htmlFor={`${formId}-reason`}>Причина решения
      <textarea id={`${formId}-reason`} required rows={3} value={reason} disabled={busy}
        placeholder="Объясните, почему подтверждаете или меняете оценку"
        onChange={(event) => setReason(event.target.value)} />
    </label>
    {validation && <p className="appeals-error" role="alert">{validation}</p>}
    <button className="button primary" type="submit" disabled={busy}>
      {busy ? 'Сохраняем решение…' : action === 'change' ? 'Сохранить новую оценку' : 'Подтвердить оценку и закрыть апелляцию'}
    </button>
  </form>;
}

export function AppealsPanel({ evaluation, conversation, onUpdated, onEvidence, reviewMode, initialCriterionId }: AppealsPanelProps) {
  const formId = useId();
  const [appeals, setAppeals] = useState<Appeal[]>([]);
  const [decisions, setDecisions] = useState<ReviewDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [criterionId, setCriterionId] = useState(initialCriterionId ?? evaluation.checklist_snapshot.criteria[0]?.id ?? '');
  const [reason, setReason] = useState('');
  const [author, setAuthor] = useState('Оператор');
  const [busy, setBusy] = useState<string | null>(null);
  const requestId = useRef(0);
  const currentEvaluation = useRef(evaluation.id);
  const mounted = useRef(true);
  const saving = useRef(false);
  currentEvaluation.current = evaluation.id;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestId.current += 1; };
  }, []);

  const refresh = useCallback(async () => {
    const request = ++requestId.current;
    const id = evaluation.id;
    setLoading(true);
    setError('');
    try {
      const [nextAppeals, nextDecisions] = await Promise.all([api.listAppeals(id), api.listDecisions(id)]);
      if (mounted.current && currentEvaluation.current === id && requestId.current === request) {
        setAppeals(nextAppeals);
        setDecisions(nextDecisions);
      }
    } catch (cause) {
      if (mounted.current && currentEvaluation.current === id && requestId.current === request) setError(friendlyError(cause));
    } finally {
      if (mounted.current && currentEvaluation.current === id && requestId.current === request) setLoading(false);
    }
  }, [evaluation.id]);

  useEffect(() => {
    setAppeals([]);
    setDecisions([]);
    setReason('');
    setNotice('');
    setBusy(null);
    saving.current = false;
    setCriterionId(initialCriterionId ?? evaluation.checklist_snapshot.criteria[0]?.id ?? '');
    if (evaluation.status === 'completed') void refresh();
    else setLoading(false);
    return () => { requestId.current += 1; };
    // The selected evaluation is the boundary of this form and its request history.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evaluation.id, evaluation.status, refresh]);

  useEffect(() => {
    if (initialCriterionId && evaluation.checklist_snapshot.criteria.some((criterion) => criterion.id === initialCriterionId)) {
      setCriterionId(initialCriterionId);
    }
  }, [initialCriterionId, evaluation.checklist_snapshot]);

  const selectedCriterion = evaluation.checklist_snapshot.criteria.find((criterion) => criterion.id === criterionId);
  const existingAppeal = appeals.some((appeal) => appeal.criterion_id === criterionId && appeal.status === 'open');
  const selectedOriginal = evaluation.ai_draft?.criteria.find((result) => result.criterion_id === criterionId);

  async function createAppeal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving.current || !selectedCriterion || existingAppeal || loading) return;
    setError('');
    setNotice('');
    if (!reason.trim() || !author.trim()) {
      setError('Укажите автора и комментарий к апелляции.');
      return;
    }
    const id = evaluation.id;
    saving.current = true;
    setBusy('new');
    try {
      const payload: AppealCreate = { criterion_id: criterionId, reason: reason.trim(), author: author.trim() };
      const saved = await api.createAppeal(id, payload);
      if (!mounted.current || currentEvaluation.current !== id) return;
      setAppeals((items) => [...items.filter((item) => item.id !== saved.id), saved]);
      setReason('');
      setNotice('Апелляция сохранена. Она доступна проверяющему.');
    } catch (cause) {
      if (mounted.current && currentEvaluation.current === id) setError(friendlyError(cause));
    } finally {
      if (mounted.current && currentEvaluation.current === id) {
        saving.current = false;
        setBusy(null);
      }
    }
  }

  async function saveDecision(appealId: string, payload: ReviewDecisionCreate) {
    if (saving.current) return;
    const id = evaluation.id;
    saving.current = true;
    setBusy(appealId);
    setError('');
    setNotice('');
    try {
      const saved = await api.decideAppeal(appealId, payload);
      if (!mounted.current || currentEvaluation.current !== id) return;
      setDecisions((items) => [...items.filter((item) => item.id !== saved.id), saved]);
      setAppeals((items) => items.map((appeal) => appeal.id === appealId ? { ...appeal, status: payload.outcome } : appeal));
      setNotice('Решение сохранено. Обновляем окончательный результат…');
      try {
        await onUpdated();
        if (mounted.current && currentEvaluation.current === id) setNotice('Решение и окончательный результат сохранены.');
      } catch (cause) {
        if (mounted.current && currentEvaluation.current === id) {
          setNotice('Решение сохранено.');
          setError(`Не удалось обновить окончательный результат: ${friendlyError(cause)} Обновите карточку.`);
        }
      }
    } catch (cause) {
      if (mounted.current && currentEvaluation.current === id) setError(friendlyError(cause));
    } finally {
      if (mounted.current && currentEvaluation.current === id) {
        saving.current = false;
        setBusy(null);
      }
    }
  }

  if (evaluation.status !== 'completed') return <section className="panel appeals-panel">
    <h2>Апелляции</h2><p className="appeals-muted">Апелляцию можно подать после успешного завершения оценки.</p>
  </section>;

  return <section className="appeals-panel" aria-labelledby={`${formId}-heading`}>
    <div className="appeals-heading">
      <div><h2 id={`${formId}-heading`}>{reviewMode ? 'Проверка апелляций' : 'Апелляции'}</h2>
        <p className="appeals-muted">{evaluation.checklist_snapshot.name} · версия {evaluation.checklist_snapshot.version}</p></div>
      <button type="button" className="button secondary" disabled={loading || busy !== null} onClick={() => void refresh()}>Обновить обращения</button>
    </div>
    {error && <div className="appeals-error" role="alert"><p>{error}</p>
      {!busy && <button type="button" className="button secondary" onClick={() => void refresh()}>Повторить загрузку обращений</button>}
    </div>}
    {notice && <p className="appeals-success" role="status">{notice}</p>}
    {loading && <p className="appeals-muted" role="status">Загружаем обращения и историю решений…</p>}
    {!reviewMode && <form className="panel appeals-new" onSubmit={createAppeal}>
      <h3>Оспорить критерий</h3>
      <label className="field" htmlFor={`${formId}-criterion`}>Спорный критерий
        <select id={`${formId}-criterion`} value={criterionId} disabled={busy !== null || loading}
          onChange={(event) => setCriterionId(event.target.value)}>
          {evaluation.checklist_snapshot.criteria.map((criterion) => <option key={criterion.id} value={criterion.id}>{criterion.title}</option>)}
        </select>
      </label>
      {selectedOriginal && <div className="appeals-original-preview">
        <span className="appeals-caption">Исходная автоматическая оценка</span>
        <strong>{answerLabel(selectedOriginal.value)}</strong>
        <p>{selectedOriginal.reasoning}</p>
        <EvidenceLinks result={selectedOriginal} conversation={conversation} onEvidence={onEvidence} />
      </div>}
      {existingAppeal && <p className="appeals-note">По этому критерию уже есть открытая апелляция. Дождитесь решения проверяющего.</p>}
      <label className="field" htmlFor={`${formId}-author`}>Автор обращения
        <input id={`${formId}-author`} required value={author} disabled={busy !== null} autoComplete="name"
          onChange={(event) => setAuthor(event.target.value)} />
      </label>
      <label className="field" htmlFor={`${formId}-comment`}>Комментарий
        <textarea id={`${formId}-comment`} required rows={3} value={reason} disabled={busy !== null}
          placeholder="С чем вы не согласны? Укажите реплику или обстоятельство, которое нужно проверить."
          onChange={(event) => setReason(event.target.value)} />
      </label>
      <button type="submit" className="button primary" disabled={busy !== null || loading || existingAppeal || !selectedCriterion}>
        {busy === 'new' ? 'Сохраняем…' : 'Сохранить апелляцию'}
      </button>
    </form>}
    {!loading && appeals.length === 0 && <div className="panel appeals-empty">
      <h3>Обращений пока нет</h3><p>{reviewMode ? 'Оператор может выбрать критерий и оставить комментарий в режиме подачи апелляции.' : 'Сохранённые обращения и решения проверяющего появятся здесь.'}</p>
    </div>}
    <div className="appeals-list">
      {[...appeals].reverse().map((appeal) => {
        const criterion = evaluation.checklist_snapshot.criteria.find((item) => item.id === appeal.criterion_id);
        if (!criterion) return <article className="panel" key={appeal.id}><p className="appeals-error">Критерий обращения отсутствует в сохранённом чек-листе.</p></article>;
        const original = evaluation.ai_draft?.criteria.find((result) => result.criterion_id === appeal.criterion_id);
        const final = evaluation.final_criteria?.find((result) => result.criterion_id === appeal.criterion_id);
        const originalScore = evaluation.original_score?.criteria.find((score) => score.criterion_id === appeal.criterion_id);
        const finalScore = evaluation.final_score?.criteria.find((score) => score.criterion_id === appeal.criterion_id);
        const decision = decisions.find((item) => item.appeal_id === appeal.id);
        return <article className="panel appeals-card" key={appeal.id}>
          <div className="appeals-heading"><h3>{criterion.title}</h3>
            <span className={`appeals-status appeals-status-${appeal.status}`}>{appeal.status === 'open' ? 'Ожидает решения' : appeal.status === 'accepted' ? 'Оценка изменена' : 'Оценка подтверждена'}</span>
          </div>
          <div className="appeals-comment"><span className="appeals-caption">Комментарий · {appeal.author}</span><p>{appeal.reason}</p></div>
          <div className="appeals-comparison">
            <div><span className="appeals-caption">1. Автоматическая оценка</span><strong>{answerLabel(original?.value)}</strong>
              <span>{scoreLabel(originalScore?.earned_points, criterion.answer_type === 'flag')}</span><p>{original?.reasoning ?? 'Обоснование не передано.'}</p>
            </div>
            <div><span className="appeals-caption">2. Решение человека</span><strong>{decision ? decision.outcome === 'accepted' ? answerLabel(decision.replacement?.value) : 'Подтверждено' : 'Ожидается'}</strong>
              {decision ? <><span>{decision.reviewer}</span><p>{decision.reason}</p>
                {decision.replacement && <p>{decision.replacement.reasoning}</p>}</> : <p>Оценка ещё не пересмотрена.</p>}
            </div>
            <div><span className="appeals-caption">3. Окончательная оценка</span><strong>{answerLabel(final?.value)}</strong>
              <span>{scoreLabel(finalScore?.earned_points, criterion.answer_type === 'flag')}</span><p>{final?.reasoning ?? 'Обоснование не передано.'}</p>
            </div>
          </div>
          <details className="appeals-evidence-details" open={appeal.status === 'open'}>
            <summary>Исходные доказательства</summary><EvidenceLinks result={original} conversation={conversation} onEvidence={onEvidence} />
          </details>
          {decision?.replacement && <details className="appeals-evidence-details">
            <summary>Доказательства решения проверяющего</summary><EvidenceLinks result={decision.replacement} conversation={conversation} onEvidence={onEvidence} />
          </details>}
          {reviewMode && appeal.status === 'open' && <ReviewForm appeal={appeal} criterion={criterion} current={final ?? original}
            conversation={conversation} busy={busy !== null} onSave={(payload) => saveDecision(appeal.id, payload)} />}
        </article>;
      })}
    </div>
  </section>;
}
