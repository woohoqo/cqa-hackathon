import test from 'node:test';
import assert from 'node:assert/strict';
import { parseImport, safeAudioUrl, totalLabel, newestFirst, valueLabel } from '../src/display.ts';
import type { Evaluation } from '../src/types.ts';

test('organizer JSON uses the same import identity as backend and preserves transcript bytes', () => {
  const timestamp = '2026-11-07T12:34:56+05:00';
  const text = '  Сәлем!\n<не HTML>  ';
  const result = parseImport(JSON.stringify({ isSuccess: true, data: { dataset_group_id: 'CONTROL', total_records: 1, records: [{ conversation_id: 'synthetic-1', channel: 'voice call', language: 'KZ', audio_url: null, transcript: [{ timestamp, speaker: 'system', text }] }] } }));
  assert.equal(result[0].source, 'organizer:CONTROL');
  assert.equal(result[0].external_id, 'synthetic-1');
  assert.deepEqual(result[0].messages[0], { role: 'system', text, timestamp });
  assert.equal(result[0].metadata?.dataset_group_id, 'CONTROL');
});
test('rejects incomplete organizer envelopes, malformed JSON, and empty imports', () => {
  for (const value of ['{bad}', '[]', '{}', '{"conversations":[]}', '{"isSuccess":false,"data":{"records":[]}}', '{"isSuccess":true,"data":{"records":[],"dataset_group_id":"DEV","total_records":1}}']) assert.throws(() => parseImport(value));
});
test('canonical messages keep IDs, roles, null and MM:SS timestamps', () => {
  const input = { conversations: [{ source: 'synthetic', messages: [{ id: 'm1', role: 'bot', text: 'Test', timestamp: '00:02' }, { id: 'm2', role: 'system', text: 'End', timestamp: null }] }] };
  assert.deepEqual(parseImport(JSON.stringify(input)), input.conversations);
});
test('audio links allow only absolute HTTP(S) and reject executable schemes', () => {
  assert.equal(safeAudioUrl('https://example.com/test.mp3'), 'https://example.com/test.mp3');
  for (const value of ['javascript:alert(1)', 'data:text/html,hello', '/relative.mp3', '', null, 3]) assert.equal(safeAudioUrl(value), null);
});
test('null total remains distinct from a zero score and boolean false', () => {
  assert.equal(totalLabel(null), 'Не рассчитан');
  assert.equal(totalLabel(0), '0,00');
  assert.equal(valueLabel(false), 'Нет');
  assert.equal(valueLabel('N/A'), 'N/A');
});
test('history ties preserve newest server insertion and do not mutate input', () => {
  const entries = [{ id: 'old', created_at: '2026-11-07T00:00:00Z' }, { id: 'new', created_at: '2026-11-07T00:00:00Z' }] as Evaluation[];
  assert.deepEqual(newestFirst(entries).map(e => e.id), ['new', 'old']);
  assert.deepEqual(entries.map(e => e.id), ['old', 'new']);
});
