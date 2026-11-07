// Executes pure functions from the actual frontend source; no React, API or provider.
// Run with Node >=22.12: node --experimental-strip-types --test qa/test_ui_contract.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const source = process.env.CQA_UI_SOURCE || path.resolve(__dirname, '../frontend/src/display.ts');
const helpers = import(pathToFileURL(source).href);
const record = () => ({ conversation_id: 'synthetic-ui-001', channel: 'voice call', language: 'KZ', audio_url: null,
  transcript: [{ speaker: 'client', text: 'Сәлем', timestamp: '00:00:02' }, { speaker: 'operator', text: 'Hello / Здравствуйте', timestamp: null }] });
const envelope = (records = [record()]) => ({ isSuccess: true, data: { dataset_group_id: 'synthetic', total_records: records.length, records } });

test('organizer mapping preserves speaker, multilingual text, source timestamp and metadata', async () => {
  const { parseImport } = await helpers;
  const [got] = parseImport(JSON.stringify(envelope()));
  assert.equal(got.external_id, 'synthetic-ui-001');
  assert.equal(got.source, 'organizer:synthetic');
  assert.deepEqual(got.messages, [{ role: 'client', text: 'Сәлем', timestamp: '00:00:02' }, { role: 'operator', text: 'Hello / Здравствуйте', timestamp: null }]);
  assert.deepEqual(got.metadata, { channel: 'voice call', language: 'KZ', audio_url: null, dataset_group_id: 'synthetic' });
});
test('failed organizer response cannot be imported as successful', async () => {
  const { parseImport } = await helpers;
  assert.throws(() => parseImport(JSON.stringify({ ...envelope(), isSuccess: false })));
});
test('incomplete organizer count is rejected', async () => {
  const { parseImport } = await helpers;
  const input = envelope(); input.data.total_records = 65;
  assert.throws(() => parseImport(JSON.stringify(input)));
});
test('empty transcript and invalid message types are rejected', async () => {
  const { parseImport } = await helpers;
  assert.throws(() => parseImport(JSON.stringify(envelope([{ ...record(), transcript: [] }]))));
  assert.throws(() => parseImport(JSON.stringify([{ messages: [{ role: 'client', text: 5 }] }])));
});
test('generic JSON retains stable message IDs needed for evidence', async () => {
  const { parseImport } = await helpers;
  const item = { messages: [{ id: 'm1', role: 'client', text: 'Synthetic text', timestamp: null }] };
  assert.deepEqual(parseImport(JSON.stringify({ conversations: [item] })), [item]);
});
test('N/A, false and zero are distinct display values', async () => {
  const { valueLabel } = await helpers;
  assert.equal(valueLabel('N/A'), 'N/A'); assert.equal(valueLabel(false), 'Нет');
  assert.equal(valueLabel(0), '0'); assert.equal(valueLabel(true), 'Да');
});
test('uncomputable total is distinct from zero and 85.38 is only formatted', async () => {
  const { totalLabel } = await helpers;
  assert.equal(totalLabel(null), 'Не рассчитан'); assert.equal(totalLabel(0), '0,00');
  assert.equal(totalLabel(85.38), '85,38');
});
test('failed status is explicit and is not considered processing', async () => {
  const { statusLabels, pending } = await helpers;
  assert.equal(statusLabels.failed, 'Ошибка'); assert.equal(pending({ status: 'failed' }), false);
  assert.equal(pending({ status: 'queued' }), true); assert.equal(pending({ status: 'running' }), true);
});
test('latest list ordering does not mutate API results and favors latest insertion on ties', async () => {
  const { newestFirst } = await helpers;
  const input = [{ id: 'old', created_at: '2026-01-01T00:00:00+00:00' }, { id: 'new', created_at: '2026-01-01T00:00:00+00:00' }];
  assert.deepEqual(newestFirst(input).map(x => x.id), ['new', 'old']);
  assert.equal(input[0].id, 'old');
});
test('audio accepts HTTP(S) only', async () => {
  const { safeAudioUrl } = await helpers;
  for (const value of ['javascript:alert(1)', 'data:text/plain,test', 'file:///tmp/x', null]) assert.equal(safeAudioUrl(value), null);
  assert.equal(safeAudioUrl('https://example.invalid/synthetic-audio'), 'https://example.invalid/synthetic-audio');
});
