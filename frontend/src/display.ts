import type { AnswerValue, Conversation, ConversationInput, Evaluation } from './types.ts';

export const statusLabels = { queued: 'В очереди', running: 'Оценивается', completed: 'Готово', failed: 'Ошибка', none: 'Не оценён' };
export const roleLabels: Record<string, string> = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
export const valueLabel = (value: AnswerValue | undefined) => value === undefined ? '—' : value === true ? 'Да' : value === false ? 'Нет' : value === 'N/A' ? 'N/A' : String(value);
export const numberLabel = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(value);
export const totalLabel = (value: number | null | undefined) => value == null ? 'Не рассчитан' : new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
export const language = (c: Conversation) => typeof c.metadata.language === 'string' ? c.metadata.language : '—';
export const channel = (c: Conversation) => typeof c.metadata.channel === 'string' ? c.metadata.channel : 'unknown';
export const channelLabel = (value: string) => value === 'agent chat' ? 'Чат' : value === 'voice call' ? 'Звонок' : value === 'unknown' ? 'Не указан' : value;
export const pending = (e: Evaluation) => e.status === 'queued' || e.status === 'running';
export function newestFirst(items: Evaluation[]): Evaluation[] {
  // API lists insertion order. Reverse first to preserve the newest item on equal timestamps.
  return [...items].reverse().sort((a, b) => b.created_at.localeCompare(a.created_at));
}
export function safeAudioUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
export function parseImport(text: string): ConversationInput[] {
  let payload: unknown;
  try { payload = JSON.parse(text); } catch { throw new Error('Некорректный JSON. Проверьте файл или текст.'); }
  let list: unknown;
  if (object(payload) && Array.isArray(payload.conversations)) list = payload.conversations;
  else if (object(payload) && object(payload.data) && Array.isArray(payload.data.records)) {
    if (payload.isSuccess !== true) throw new Error('Набор организаторов не содержит успешного ответа.');
    const group = payload.data.dataset_group_id;
    if (typeof group !== 'string' || !group.trim() || payload.data.total_records !== payload.data.records.length) throw new Error('Проверьте dataset_group_id и total_records в наборе организаторов.');
    list = payload.data.records.map((item: unknown) => {
      if (!object(item) || !Array.isArray(item.transcript)) throw new Error('У записи отсутствует массив transcript.');
      return {
        external_id: item.conversation_id, source: `organizer:${group}`,
        messages: item.transcript.map((m: unknown) => {
          if (!object(m)) throw new Error('Некорректная реплика в transcript.');
          return { role: m.speaker, text: m.text, timestamp: m.timestamp ?? null };
        }),
        metadata: { channel: item.channel, language: item.language, audio_url: item.audio_url ?? null, dataset_group_id: group },
      };
    });
  } else if (Array.isArray(payload)) list = payload;
  if (!Array.isArray(list) || list.length === 0 || list.length > 1000) throw new Error('Нужен массив от 1 до 1000 диалогов: conversations или data.records.');
  for (const item of list) {
    if (!object(item) || !Array.isArray(item.messages) || !item.messages.length) throw new Error('Каждый диалог должен содержать непустой массив messages.');
    for (const m of item.messages) {
      if (!object(m) || typeof m.text !== 'string' || typeof m.role !== 'string' || (m.timestamp != null && typeof m.timestamp !== 'string')) throw new Error('У реплики должны быть строковые role, text и исходная timestamp (или null).');
    }
  }
  return list as ConversationInput[];
}
