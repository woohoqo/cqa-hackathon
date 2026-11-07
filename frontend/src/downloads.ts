export type ExportFormat = 'json' | 'csv';

// Downloads the server's stored results; never rebuilds scores from the UI state.
export async function fetchEvaluationExport(base: string, format: ExportFormat, fetcher: typeof fetch = fetch, batchId?: string) {
  const scope = batchId ? `&batch_id=${encodeURIComponent(batchId)}` : '';
  const response = await fetcher(`${base.replace(/\/$/, '')}/api/v1/exports/evaluations?format=${format}${scope}`);
  return readDownload(response, format, batchId ? `cqa-archive-${batchId}.${format}` : `cqa-evaluations.${format}`);
}

export async function fetchOfficialExport(base: string, batchId: string, teamName: string, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`${base.replace(/\/$/, '')}/api/v1/batches/${encodeURIComponent(batchId)}/official-export`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ team_name: teamName }),
  });
  return readDownload(response, 'json', 'results.json');
}

async function readDownload(response: Response, format: ExportFormat, filename: string) {
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const message = typeof data?.error?.message === 'string' ? data.error.message : `Не удалось выгрузить результаты: ошибка сервера ${response.status}. Повторите попытку.`;
    throw new Error(message);
  }
  const expected = format === 'json' ? 'application/json' : 'text/csv';
  if (!response.headers.get('content-type')?.toLowerCase().includes(expected)) {
    throw new Error('Сервер вернул файл в неизвестном формате. Проверьте адрес API и повторите выгрузку.');
  }
  const blob = await response.blob();
  if (!blob.size) throw new Error('Сервер вернул пустой файл. Повторите выгрузку.');
  return { blob, filename };
}

export function saveDownload(file: { blob: Blob; filename: string }) {
  const url = URL.createObjectURL(file.blob);
  const link = document.createElement('a');
  link.href = url; link.download = file.filename; document.body.append(link);
  try { link.click(); } finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
}
