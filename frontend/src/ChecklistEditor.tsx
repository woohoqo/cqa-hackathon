import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { ChecklistCreate, ChecklistVersion, Criterion, Section } from './types';
import './ChecklistEditor.css';

type DraftStep = { key: string; value: string; label: string; description: string };
type DraftCriterion = Omit<Criterion, 'weight' | 'scale'> & { weight: string; scale: DraftStep[] };
type Draft = Omit<ChecklistCreate, 'criteria'> & { criteria: DraftCriterion[] };

interface ChecklistEditorProps {
  checklists: ChecklistVersion[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSave: (input: ChecklistCreate) => Promise<ChecklistVersion>;
  onUse: (id: string) => void;
}

const answerLabels: Record<Criterion['answer_type'], string> = {
  boolean: 'Да / Нет',
  scale: 'Числовая шкала',
  flag: 'Флаг грубой ошибки',
};
const uid = () => crypto.randomUUID();

function blankDraft(): Draft {
  return { key: uid(), name: '', description: '', sections: [{ id: uid(), title: 'Общие критерии' }], criteria: [] };
}

function editable(checklist: ChecklistCreate): Draft {
  return {
    key: checklist.key,
    name: checklist.name,
    description: checklist.description,
    sections: checklist.sections.map((section) => ({ ...section })),
    criteria: checklist.criteria.map((criterion) => ({
      ...criterion,
      weight: String(criterion.weight),
      scale: criterion.scale.map((step) => ({ ...step, key: uid(), value: String(step.value) })),
    })),
  };
}

function validate(draft: Draft): string[] {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push('Укажите название чек-листа.');
  if (!draft.criteria.length) errors.push('Добавьте хотя бы один критерий.');
  if (!draft.sections.length || draft.sections.some((section) => !section.title.trim())) errors.push('У каждой секции должно быть название.');
  draft.criteria.forEach((criterion, index) => {
    const title = criterion.title.trim() || `Критерий ${index + 1}`;
    if (!criterion.title.trim()) errors.push(`${title}: укажите название.`);
    if (!criterion.description.trim()) errors.push(`${title}: опишите правило оценки.`);
    if (!draft.sections.some((section) => section.id === criterion.section_id)) errors.push(`${title}: выберите секцию.`);
    if (!criterion.weight.trim() || !Number.isFinite(Number(criterion.weight)) || Number(criterion.weight) < 0) errors.push(`${title}: вес должен быть числом не меньше нуля.`);
    if (criterion.answer_type === 'flag' && Number(criterion.weight) !== 0) errors.push(`${title}: флаг грубой ошибки должен иметь нулевой вес.`);
    if (criterion.allow_na && !criterion.na_condition?.trim()) errors.push(`${title}: укажите условие N/A.`);
    if (criterion.answer_type === 'scale') {
      const values = criterion.scale.map((step) => Number(step.value));
      if (!values.length || criterion.scale.some((step) => !step.value.trim()) || values.some((value) => !Number.isFinite(value) || value < 0) || !values.some((value) => value > 0)) {
        errors.push(`${title}: задайте неотрицательные ступени шкалы, хотя бы одну больше нуля.`);
      }
      if (new Set(values).size !== values.length) errors.push(`${title}: значения ступеней не должны повторяться.`);
      if (criterion.scale.some((step) => !step.label.trim())) errors.push(`${title}: подпишите каждую ступень шкалы.`);
    }
  });
  return errors;
}

function payload(draft: Draft): ChecklistCreate {
  return {
    key: draft.key,
    name: draft.name.trim(),
    description: draft.description.trim(),
    sections: draft.sections.map((section) => ({ id: section.id, title: section.title.trim() })),
    criteria: draft.criteria.map((criterion) => ({
      id: criterion.id,
      section_id: criterion.section_id,
      title: criterion.title.trim(),
      description: criterion.description.trim(),
      answer_type: criterion.answer_type,
      weight: Number(criterion.weight),
      scale: criterion.answer_type === 'scale' ? criterion.scale.map((step) => ({ value: Number(step.value), label: step.label.trim(), description: step.description.trim() })) : [],
      allow_na: criterion.allow_na,
      na_condition: criterion.allow_na ? criterion.na_condition?.trim() || null : null,
    })),
  };
}

export function ChecklistEditor({ checklists, selectedId, onSelect, onSave, onUse }: ChecklistEditorProps) {
  const initial = checklists.find((item) => item.id === selectedId) ?? checklists[0];
  const [draft, setDraft] = useState<Draft>(() => initial ? editable(initial) : blankDraft());
  const [baseId, setBaseId] = useState(initial?.id ?? '');
  const [activeId, setActiveId] = useState(initial?.criteria[0]?.id ?? '');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState<ChecklistVersion | null>(null);
  const [newChecklist, setNewChecklist] = useState(!initial);
  const errorRef = useRef<HTMLDivElement>(null);
  const ordered = useMemo(() => [...checklists].sort((a, b) => b.created_at.localeCompare(a.created_at)), [checklists]);
  const base = checklists.find((item) => item.id === baseId);
  const active = draft.criteria.find((item) => item.id === activeId);

  useEffect(() => {
    const next = checklists.find((item) => item.id === selectedId);
    if (next && next.id !== baseId && !dirty && !busy) {
      setDraft(editable(next));
      setBaseId(next.id);
      setActiveId(next.criteria[0]?.id ?? '');
      setNewChecklist(false);
    }
  }, [checklists, selectedId, baseId, dirty, busy]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  useEffect(() => {
    if (errors.length) errorRef.current?.focus();
  }, [errors]);

  function update(next: Draft | ((previous: Draft) => Draft)) {
    setDraft(next);
    setDirty(true);
    setSaved(null);
    setErrors([]);
  }

  function updateCriterion(id: string, change: Partial<DraftCriterion>) {
    update((previous) => ({ ...previous, criteria: previous.criteria.map((criterion) => criterion.id === id ? { ...criterion, ...change } : criterion) }));
  }

  function switchVersion(id: string) {
    if (dirty && !window.confirm('В черновике есть несохранённые изменения. Открыть другую версию и отменить их?')) return;
    const next = checklists.find((item) => item.id === id);
    if (!next) return;
    setDraft(editable(next));
    setBaseId(next.id);
    setActiveId(next.criteria[0]?.id ?? '');
    setDirty(false);
    setSaved(null);
    setErrors([]);
    setNewChecklist(false);
    onSelect(id);
  }

  function startNew() {
    if (dirty && !window.confirm('В черновике есть несохранённые изменения. Создать новый чек-лист и отменить их?')) return;
    setDraft(blankDraft());
    setBaseId('');
    setActiveId('');
    setDirty(true);
    setSaved(null);
    setErrors([]);
    setNewChecklist(true);
  }

  function addCriterion() {
    const criterion: DraftCriterion = {
      id: uid(), section_id: draft.sections[0]?.id ?? '', title: '', description: '',
      weight: '10', answer_type: 'boolean', scale: [], allow_na: false, na_condition: null,
    };
    update((previous) => ({ ...previous, criteria: [...previous.criteria, criterion] }));
    setActiveId(criterion.id);
  }

  function deleteCriterion(criterion: DraftCriterion) {
    if (!window.confirm(`Удалить критерий «${criterion.title || 'Без названия'}» из новой версии? Ранее сохранённые оценки останутся доступными.`)) return;
    const remaining = draft.criteria.filter((item) => item.id !== criterion.id);
    update({ ...draft, criteria: remaining });
    setActiveId(remaining[0]?.id ?? '');
  }

  function deleteSection(section: Section) {
    if (draft.sections.length === 1) return;
    const destination = draft.sections.find((item) => item.id !== section.id)!;
    const affected = draft.criteria.filter((criterion) => criterion.section_id === section.id).length;
    if (!window.confirm(`Удалить секцию «${section.title || 'Без названия'}»?${affected ? ` Критерии (${affected}) будут перенесены в «${destination.title}».` : ''}`)) return;
    update({
      ...draft,
      sections: draft.sections.filter((item) => item.id !== section.id),
      criteria: draft.criteria.map((criterion) => criterion.section_id === section.id ? { ...criterion, section_id: destination.id } : criterion),
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const problems = validate(draft);
    if (problems.length) { setErrors(problems); return; }
    setBusy(true);
    setErrors([]);
    try {
      const result = await onSave(payload(draft));
      setDraft(editable(result));
      setBaseId(result.id);
      setDirty(false);
      setSaved(result);
      setNewChecklist(false);
      onSelect(result.id);
    } catch (error) {
      setErrors([error instanceof Error ? error.message : 'Не удалось сохранить версию. Попробуйте ещё раз.']);
    } finally { setBusy(false); }
  }

  return (
    <section className="checklist-editor" aria-label="Редактор чек-листа">
      <div className="checklist-topbar panel">
        <div className="field checklist-version-field">
          <label htmlFor="checklist-version">Версия для редактирования</label>
          <select id="checklist-version" value={newChecklist ? '' : baseId} onChange={(event) => switchVersion(event.target.value)} disabled={busy || !checklists.length}>
            {(newChecklist || !checklists.length) && <option value="">Новый чек-лист</option>}
            {ordered.map((checklist) => <option key={checklist.id} value={checklist.id}>{checklist.name} · версия {checklist.version}</option>)}
          </select>
        </div>
        <button type="button" className="button secondary" onClick={startNew} disabled={busy}>Новый чек-лист</button>
        <span className={`checklist-draft-state ${dirty ? 'is-dirty' : ''}`} role="status">{dirty ? 'Есть несохранённые изменения' : base ? `На основе версии ${base.version}` : 'Новый черновик'}</span>
      </div>

      {saved && <div className="checklist-saved panel" role="status">
        <div><strong>Версия {saved.version} сохранена</strong><p>Следующую оценку можно запустить с этим чек-листом. Предыдущие результаты сохраняют свою версию.</p></div>
        <button type="button" className="button primary" onClick={() => onUse(saved.id)}>Оценить с версией {saved.version}</button>
      </div>}

      {errors.length > 0 && <div className="checklist-errors" role="alert" tabIndex={-1} ref={errorRef}>
        <strong>Проверьте чек-лист</strong>
        <ul>{errors.map((error, index) => <li key={`${index}-${error}`}>{error}</li>)}</ul>
      </div>}

      <form onSubmit={save} noValidate>
        <fieldset className="checklist-form-fieldset" disabled={busy}>
          <div className="panel checklist-settings">
            <div className="field"><label htmlFor="checklist-name">Название чек-листа</label><input id="checklist-name" value={draft.name} onChange={(event) => update({ ...draft, name: event.target.value })} placeholder="Например, качество консультации" required /></div>
            <div className="field"><label htmlFor="checklist-description">Описание</label><textarea id="checklist-description" rows={2} value={draft.description} onChange={(event) => update({ ...draft, description: event.target.value })} placeholder="Когда и для каких диалогов применяется чек-лист" /></div>
            <details className="checklist-sections">
              <summary>Секции чек-листа <span>{draft.sections.length}</span></summary>
              <div className="checklist-sections-content">
                {draft.sections.map((section, index) => <div className="checklist-section-row" key={section.id}>
                  <label htmlFor={`section-${section.id}`} className="checklist-section-label">Секция {index + 1}</label>
                  <input id={`section-${section.id}`} value={section.title} onChange={(event) => update({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item) })} />
                  <button type="button" className="button danger" disabled={draft.sections.length === 1} aria-label={`Удалить секцию ${section.title || index + 1}`} onClick={() => deleteSection(section)}>Удалить</button>
                </div>)}
                <button type="button" className="button secondary" onClick={() => update({ ...draft, sections: [...draft.sections, { id: uid(), title: '' }] })}>Добавить секцию</button>
              </div>
            </details>
          </div>

          <div className="checklist-workspace">
            <aside className="panel checklist-outline" aria-label="Список критериев">
              <div className="checklist-outline-heading"><h2>Критерии</h2><span>{draft.criteria.length}</span></div>
              <p className="checklist-hint">Выберите критерий, чтобы изменить правило оценки.</p>
              <div className="checklist-criterion-list">
                {draft.criteria.map((criterion, index) => <button key={criterion.id} type="button" aria-pressed={criterion.id === activeId} className={`checklist-criterion-link ${criterion.id === activeId ? 'is-active' : ''}`} onClick={() => setActiveId(criterion.id)}>
                  <span className="checklist-criterion-index">{String(index + 1).padStart(2, '0')}</span>
                  <span><strong>{criterion.title || 'Новый критерий'}</strong><small>{answerLabels[criterion.answer_type]}{criterion.answer_type !== 'flag' ? ` · вес ${criterion.weight || '—'}` : ''}</small></span>
                </button>)}
              </div>
              <button type="button" className="button secondary checklist-add" onClick={addCriterion}>+ Добавить критерий</button>
            </aside>

            {active ? <article className="panel checklist-criterion-form" key={active.id}>
              <div className="checklist-criterion-heading"><div><span className="checklist-eyebrow">Правило оценки</span><h2>{active.title || 'Новый критерий'}</h2></div><button type="button" className="button danger" onClick={() => deleteCriterion(active)}>Удалить критерий</button></div>
              <div className="field"><label htmlFor="criterion-title">Название критерия</label><input id="criterion-title" value={active.title} onChange={(event) => updateCriterion(active.id, { title: event.target.value })} required /></div>
              <div className="field"><label htmlFor="criterion-description">Формулировка и правило оценки</label><textarea id="criterion-description" rows={4} value={active.description} onChange={(event) => updateCriterion(active.id, { description: event.target.value })} placeholder="Что должен сделать оператор и в каком случае критерий выполнен" required /></div>
              <div className="checklist-field-grid">
                <div className="field"><label htmlFor="criterion-section">Секция</label><select id="criterion-section" value={active.section_id} onChange={(event) => updateCriterion(active.id, { section_id: event.target.value })}>{draft.sections.map((section) => <option key={section.id} value={section.id}>{section.title || 'Секция без названия'}</option>)}</select></div>
                <div className="field"><label htmlFor="criterion-answer-type">Тип ответа</label><select id="criterion-answer-type" value={active.answer_type} onChange={(event) => {
                  const answer_type = event.target.value as Criterion['answer_type'];
                  updateCriterion(active.id, { answer_type, weight: answer_type === 'flag' ? '0' : active.weight, scale: answer_type === 'scale' ? active.scale : [] });
                }}>{Object.entries(answerLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
                <div className="field"><label htmlFor="criterion-weight">Вес критерия</label><input id="criterion-weight" type="number" min="0" step="any" value={active.weight} disabled={active.answer_type === 'flag'} onChange={(event) => updateCriterion(active.id, { weight: event.target.value })} required /></div>
              </div>
              {active.answer_type === 'flag' && <p className="checklist-hint checklist-note">Флаг грубой ошибки отображается отдельно от балла. Его вес равен нулю.</p>}
              {active.answer_type === 'scale' && <section className="checklist-scale" aria-label="Ступени шкалы">
                <h3>Ступени шкалы</h3><p className="checklist-hint">Задайте допустимые значения и пояснения к ним. Максимальная ступень соответствует полному весу критерия.</p>
                {active.scale.length === 0 && <p className="checklist-empty-inline">Ступеней пока нет. Добавьте хотя бы одну с положительным значением.</p>}
                {active.scale.map((step, index) => <div className="checklist-scale-step" key={step.key}>
                  <div className="field"><label htmlFor={`step-value-${step.key}`}>Значение {index + 1}</label><input id={`step-value-${step.key}`} type="number" min="0" step="any" value={step.value} onChange={(event) => updateCriterion(active.id, { scale: active.scale.map((item) => item.key === step.key ? { ...item, value: event.target.value } : item) })} /></div>
                  <div className="field"><label htmlFor={`step-label-${step.key}`}>Подпись</label><input id={`step-label-${step.key}`} value={step.label} onChange={(event) => updateCriterion(active.id, { scale: active.scale.map((item) => item.key === step.key ? { ...item, label: event.target.value } : item) })} /></div>
                  <button type="button" className="button danger checklist-remove-step" aria-label={`Удалить ступень ${index + 1}`} onClick={() => updateCriterion(active.id, { scale: active.scale.filter((item) => item.key !== step.key) })}>Удалить</button>
                  <div className="field checklist-scale-description"><label htmlFor={`step-description-${step.key}`}>Описание ступени {index + 1}</label><textarea id={`step-description-${step.key}`} rows={2} value={step.description} onChange={(event) => updateCriterion(active.id, { scale: active.scale.map((item) => item.key === step.key ? { ...item, description: event.target.value } : item) })} /></div>
                </div>)}
                <button type="button" className="button secondary" onClick={() => updateCriterion(active.id, { scale: [...active.scale, { key: uid(), value: '', label: '', description: '' }] })}>+ Добавить ступень</button>
              </section>}
              <div className="checklist-na">
                <label className="checklist-checkbox"><input type="checkbox" checked={active.allow_na} onChange={(event) => updateCriterion(active.id, { allow_na: event.target.checked })} /><span>Разрешить N/A — критерий неприменим</span></label>
                {active.allow_na && <div className="field"><label htmlFor="criterion-na-condition">Условие N/A</label><textarea id="criterion-na-condition" rows={3} value={active.na_condition ?? ''} onChange={(event) => updateCriterion(active.id, { na_condition: event.target.value })} placeholder="При каких условиях этот критерий не применяется" required /><small className="checklist-hint">Для N/A нужна причина в результате оценки. Пропущенное обязательное действие оценивается как «Нет».</small></div>}
              </div>
            </article> : <div className="panel checklist-empty"><span className="checklist-empty-symbol" aria-hidden="true">＋</span><h2>Добавьте первый критерий</h2><p>Название, правило оценки, тип ответа и вес задаются здесь. Все критерии попадут в новую версию.</p><button type="button" className="button primary" onClick={addCriterion}>Добавить критерий</button></div>}
          </div>
          <div className="panel checklist-savebar"><div><strong>{newChecklist ? 'Сохранение нового чек-листа' : 'Сохранение новой версии'}</strong><p>Баллы и итог рассчитывает сервер. Сохранённые версии не изменяются.</p></div><button type="submit" className="button primary" disabled={busy || (!dirty && Boolean(base))}>{busy ? 'Сохраняем…' : 'Сохранить новую версию'}</button></div>
        </fieldset>
      </form>
    </section>
  );
}
