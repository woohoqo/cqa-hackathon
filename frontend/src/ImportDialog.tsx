import { useState } from 'react';
import { api, friendlyError } from './api';
import { parseImport } from './display';
import { ErrorNotice, Icon, Modal } from './components';
import type { ImportResult } from './types';

export function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (result: ImportResult) => Promise<void> }) {
  const [mode, setMode] = useState<'json' | 'dev'>('json');
  const [text, setText] = useState('');
  const [groupId, setGroupId] = useState('DEV');
  const [apiUrl, setApiUrl] = useState('');
  const [submitting, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const busy = submitting || reading;
  const [error, setError] = useState('');
  async function submit() {
    setError(''); setBusy(true);
    try {
      const result = mode === 'dev' ? await api.importDev(groupId, apiUrl) : await api.importJson(parseImport(text));
      await onImported(result); onClose();
    } catch (err) { setError(friendlyError(err)); } finally { setBusy(false); }
  }
  return <Modal title="Импорт диалогов" busy={busy} onClose={onClose}>
    <div className="segmented" aria-label="Способ импорта"><button disabled={busy} className={mode === 'json' ? 'active' : ''} onClick={() => { setMode('json'); setError(''); }}>JSON-файл</button><button disabled={busy} className={mode === 'dev' ? 'active' : ''} onClick={() => { setMode('dev'); setError(''); }}>API организаторов</button></div>
    {mode === 'json' ? <>
      <p className="muted">Загрузите файл организаторов или JSON в формате <code>conversations</code>. Исходные тексты и метки времени сохранятся.</p>
      <label className="file-picker"><Icon name="upload" />Выбрать JSON-файл<input type="file" accept=".json,application/json" disabled={busy} onChange={async e => {
        const file = e.target.files?.[0]; if (!file) return;
        if (file.size > 10 * 1024 * 1024) { setError('Файл превышает 10 МБ. Разделите его на несколько наборов.'); return; }
        setReading(true); setText('');
        try { setText(await file.text()); setError(''); } catch { setError('Не удалось прочитать файл.'); } finally { setReading(false); }
      }} /></label>
      <label className="field">Или вставьте JSON<textarea rows={9} value={text} disabled={busy} spellCheck={false} onChange={e => setText(e.target.value)} placeholder={'{"conversations": [{"external_id": "…", "messages": […]}]}'} /></label>
      <p className="help">До 1000 диалогов за один импорт. Пример с вымышленными данными доступен в frontend/examples/.</p>
    </> : <div className="import-dev">
      <div className="empty-icon"><Icon name="chat" size={26} /></div>
      <h3>Набор организаторов</h3>
      <p>Вставьте ссылку API и укажите группу набора. Сервер загрузит диалоги по этой ссылке.</p>
      <label className="field">Ссылка API организаторов
        <input type="url" value={apiUrl} maxLength={8192} disabled={busy} autoComplete="off" spellCheck={false}
          onChange={e => setApiUrl(e.target.value)} placeholder="https://…" aria-describedby="import-url-help" />
      </label>
      <p id="import-url-help" className="help">Ссылка используется только для текущего импорта и очищается при закрытии окна. Если оставить поле пустым, используется настройка сервера.</p>
      <label className="field">Группа набора
        <input value={groupId} maxLength={100} disabled={busy} autoComplete="off" spellCheck={false}
          onChange={e => setGroupId(e.target.value)} placeholder="DEV" aria-describedby="import-group-help" />
      </label>
      <p id="import-group-help" className="help">Только ID группы, например DEV или CONTROL_1. Для контрольного набора введите group_id от организаторов.</p>
    </div>}
    {error && <ErrorNotice message={error} />}
    <div className="modal-actions"><button className="button secondary" onClick={onClose} disabled={busy}>Отмена</button><button className="button primary" disabled={busy || (mode === 'json' ? !text.trim() : !groupId.trim())} onClick={() => void submit()}>{busy ? 'Импортируем…' : 'Импортировать'}</button></div>
  </Modal>;
}
