export type ExportFormat = 'json' | 'csv';

// Downloads the server's stored results; never rebuilds scores from the UI state.
export async function fetchEvaluationExport(base: string, format: ExportFormat, fetcher: typeof fetch = fetch) {
  const response = await fetcher(`${base.replace(/\/$/, '')}/api/v1/exports/evaluations?format=${format}`);
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
  return { blob, filename: `cqa-evaluations.${format}` };
}
