import type { Appeal, AppealCreate, ChecklistCreate, ChecklistVersion, Conversation, ConversationInput, Dataset, DatasetKind, Evaluation, EvaluationBatch, EvaluationRun, ImportResult, OfficialExportReport, RetryResult, ReviewDecision, ReviewDecisionCreate } from './types';
import { fetchEvaluationExport, fetchOfficialExport, type ExportFormat } from './downloads';
import { organizerImportRequest } from './organizerImport';

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}
export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return 'Не удалось связаться с сервером. Проверьте соединение и повторите попытку.';
  return error instanceof Error ? error.message : 'Не удалось выполнить действие. Повторите попытку.';
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}/api/v1${path}`, {
    ...init, headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(data?.error?.message || (response.status === 404 ? 'Метод или запись не найдены на сервере. Проверьте версию backend.' : `Сервер вернул ошибку ${response.status}. Повторите попытку.`), response.status, data?.error?.code);
  }
  if (data === null) throw new ApiError('Сервер вернул пустой или некорректный ответ.', response.status);
  return data as T;
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });
const id = encodeURIComponent;
async function listAll<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [];
  for (let offset = 0; ; offset += 200) {
    const page = await request<T[]>(`${path}${path.includes('?') ? '&' : '?'}limit=200&offset=${offset}`, { signal });
    if (!Array.isArray(page)) throw new ApiError('Сервер вернул список в неизвестном формате.', 200);
    items.push(...page);
    if (page.length < 200) return items;
  }
}
export const api = {
  exportEvaluations: (format: ExportFormat, batchId: string) => fetchEvaluationExport(base, format, fetch, batchId),
  validateOfficialExport: (batchId: string, team_name: string) => post<OfficialExportReport>(`/batches/${id(batchId)}/official-export/validate`, { team_name }),
  exportOfficial: (batchId: string, teamName: string) => fetchOfficialExport(base, batchId, teamName),
  listDatasets: (signal?: AbortSignal) => request<Dataset[]>('/datasets', { signal }),
  listRuns: (signal?: AbortSignal) => request<EvaluationRun[]>('/batches', { signal }),
  listConversations: (signal?: AbortSignal) => listAll<Conversation>('/conversations', signal),
  getConversation: (key: string) => request<Conversation>(`/conversations/${id(key)}`),
  listChecklists: (signal?: AbortSignal) => listAll<ChecklistVersion>('/checklists', signal),
  saveChecklist: (input: ChecklistCreate) => post<ChecklistVersion>('/checklists', input),
  listEvaluations: (signal?: AbortSignal) => listAll<Evaluation>('/evaluations', signal),
  getEvaluation: (key: string) => request<Evaluation>(`/evaluations/${id(key)}`),
  evaluate: (conversation_ids: string[], checklist_id: string, dataset_id: string, idempotency_key: string) => post<EvaluationBatch>('/evaluations', { conversation_ids, checklist_id, dataset_id, idempotency_key }),
  getBatch: (key: string) => request<EvaluationBatch>(`/batches/${id(key)}`),
  retryBatch: (key: string, failed_evaluation_ids: string[]) => post<RetryResult>(`/batches/${id(key)}/retry`, { failed_evaluation_ids }),
  getAttempts: (key: string) => request<Evaluation[]>(`/batches/${id(key)}/attempts`),
  importJson: (conversations: ConversationInput[], dataset_kind: DatasetKind, dataset_name?: string) => post<ImportResult>('/imports/json', { conversations, dataset_kind, ...(dataset_name?.trim() ? { dataset_name: dataset_name.trim() } : {}) }),
  importDev: (group_id = 'DEV', api_url = '', dataset_kind: DatasetKind = 'dev', dataset_name?: string) => post<ImportResult>('/imports/dev', { ...organizerImportRequest(group_id, api_url), dataset_kind, ...(dataset_name?.trim() ? { dataset_name: dataset_name.trim() } : {}) }),
  listAppeals: (key: string) => request<Appeal[]>(`/evaluations/${id(key)}/appeals`),
  listDecisions: (key: string) => request<ReviewDecision[]>(`/evaluations/${id(key)}/decisions`),
  createAppeal: (key: string, input: AppealCreate) => post<Appeal>(`/evaluations/${id(key)}/appeals`, input),
  decideAppeal: (key: string, input: ReviewDecisionCreate) => post<ReviewDecision>(`/appeals/${id(key)}/decisions`, input),
};
