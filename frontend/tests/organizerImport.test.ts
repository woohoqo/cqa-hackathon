import test from 'node:test';
import assert from 'node:assert/strict';
import { organizerImportRequest } from '../src/organizerImport.ts';

test('preserves a signed URL exactly while trimming input edges', () => {
  const api_url = 'https://example.invalid/api?sig=SYNTHETIC%2b%2F%3D&group_id=OLD';
  assert.deepEqual(organizerImportRequest(' DEV ', ` ${api_url} `), { group_id: 'DEV', api_url });
});
test('blank link allows existing server configuration', () => {
  assert.deepEqual(organizerImportRequest('CONTROL_1', '  '), { group_id: 'CONTROL_1' });
});
test('explains a link pasted into the group field', () => {
  for (const group of ['DEV https://example.invalid', '', 'x'.repeat(101), 'DEV&sig=test']) {
    assert.throws(() => organizerImportRequest(group), /Группа набора/);
  }
});
test('rejects unsafe and malformed links without reflecting secrets', () => {
  for (const link of ['http://example.invalid', 'https://u:secret@example.invalid', 'https://[',
    'https://example.invalid:65536', 'https://example.invalid:0', 'https://example.invalid/a b',
    'https://example.invalid/a\\b', 'https://example.invalid/a\nb', 'https://example.invalid/#secret',
    'https://example.invalid/' + 'x'.repeat(8192)]) {
    assert.throws(() => organizerImportRequest('DEV', link), error =>
      error instanceof Error && error.message.includes('HTTPS') && !error.message.includes('secret'));
  }
});
