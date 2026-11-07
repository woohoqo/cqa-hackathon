import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchEvaluationExport } from '../src/downloads.ts';

test('JSON download uses configured API origin and preserves original server bytes', async () => {
  const content = ' {"schema_version":"1","evaluations":[],"appeals":[],"decisions":[]}\n';
  const fetcher: typeof fetch = async input => {
    assert.equal(input, 'https://example.invalid/api/v1/exports/evaluations?format=json');
    return new Response(content, { headers: { 'content-type': 'application/json' } });
  };
  const file = await fetchEvaluationExport('https://example.invalid/', 'json', fetcher);
  assert.equal(file.filename, 'cqa-evaluations.json');
  assert.equal(await file.blob.text(), content);
});

test('CSV download uses same-origin proxy and retains UTF-8 BOM', async () => {
  const content = '\ufeffevaluation_id,final_total\r\nsynthetic,85.38\r\n';
  const file = await fetchEvaluationExport('', 'csv', async input => {
    assert.equal(input, '/api/v1/exports/evaluations?format=csv');
    return new Response(content, { headers: { 'content-type': 'text/csv; charset=utf-8' } });
  });
  assert.equal(file.filename, 'cqa-evaluations.csv');
  assert.deepEqual(new Uint8Array(await file.blob.arrayBuffer()), new TextEncoder().encode(content));
});

test('API failure is surfaced and not saved as a result file', async () => {
  await assert.rejects(fetchEvaluationExport('', 'json', async () => new Response(
    JSON.stringify({ error: { code: 'unavailable', message: 'Сервис недоступен.' } }),
    { status: 503, headers: { 'content-type': 'application/json' } },
  )), /Сервис недоступен/);
});

test('wrong-origin HTML and empty successful responses are rejected', async () => {
  await assert.rejects(fetchEvaluationExport('', 'json', async () => new Response('<html>SPA</html>', {
    headers: { 'content-type': 'text/html' },
  })), /неизвестном формате/);
  await assert.rejects(fetchEvaluationExport('', 'csv', async () => new Response('', {
    headers: { 'content-type': 'text/csv' },
  })), /пустой файл/);
});
