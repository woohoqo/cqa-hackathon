import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchEvaluationExport, fetchOfficialExport } from '../src/downloads.ts';

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

test('archive is limited to the selected run, including a safely encoded ID', async () => {
  const file = await fetchEvaluationExport('https://example.invalid/', 'json', async input => {
    assert.equal(input, 'https://example.invalid/api/v1/exports/evaluations?format=json&batch_id=run%2Fone');
    return new Response('{"evaluations":[]}', { headers: { 'content-type': 'application/json' } });
  }, 'run/one');
  assert.equal(await file.blob.text(), '{"evaluations":[]}');
});

test('official export posts team and run context and preserves the server file unchanged', async () => {
  const content = ' {"team_name":"cuda cuda","results":[]}\n';
  const file = await fetchOfficialExport('https://example.invalid/', 'run-one', 'cuda cuda', async (input, init) => {
    assert.equal(input, 'https://example.invalid/api/v1/batches/run-one/official-export');
    assert.equal(init?.method, 'POST');
    assert.deepEqual(JSON.parse(String(init?.body)), { team_name: 'cuda cuda' });
    return new Response(content, { headers: { 'content-type': 'application/json' } });
  });
  assert.equal(file.filename, 'results.json');
  assert.equal(await file.blob.text(), content);
});

test('failed server preflight cannot be saved as an official results file', async () => {
  await assert.rejects(fetchOfficialExport('', 'run-one', 'cuda cuda', async () => new Response(
    JSON.stringify({ error: { code: 'official_export_invalid', message: 'Диалог synthetic-12: оценка ещё выполняется.' } }),
    { status: 422, headers: { 'content-type': 'application/json' } },
  )), /synthetic-12/);
});
