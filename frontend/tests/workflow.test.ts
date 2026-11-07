import test from 'node:test';
import assert from 'node:assert/strict';
import { ascendingFinalScore, attemptsForRun, evaluationProvenance, paginate } from '../src/workflow.ts';
import type { Conversation, Evaluation } from '../src/types.ts';

const conversation = (id: string) => ({ id }) as Conversation;
const evaluation = (id: string, total: number | null, status = 'completed') => ({ conversation_id: id, status, final_score: { total } }) as Evaluation;
const evaluations = (...items: Evaluation[]) => new Map(items.map(item => [item.conversation_id, item]));

test('scores sort numerically, zero first, equal values stable, missing/failed/running last', () => {
  const items = ['hundred', 'null', 'ten', 'two', 'zero', 'tie', 'failed', 'running', 'missing'].map(conversation);
  const result = ascendingFinalScore(items, evaluations(evaluation('hundred', 100), evaluation('ten', 10), evaluation('two', 2), evaluation('zero', 0), evaluation('tie', 10), evaluation('null', null), evaluation('failed', 1, 'failed'), evaluation('running', 3, 'running')));
  assert.deepEqual(result.map(item => item.id), ['zero', 'two', 'ten', 'tie', 'hundred', 'null', 'failed', 'running', 'missing']);
  assert.equal(items[0].id, 'hundred');
});

test('sort covers the entire filtered selection before pagination and leaves selection intact', () => {
  const items = Array.from({ length: 55 }, (_, index) => conversation(String(index)));
  const selected = new Set(['2', '43']);
  const results = evaluations(...items.map((item, index) => evaluation(item.id, 100 - index)));
  const filtered = items.filter(item => Number(item.id) % 2 === 0);
  const sorted = ascendingFinalScore(filtered, results);
  assert.deepEqual(paginate(sorted, 1, 25).items.slice(0, 3).map(item => item.id), ['54', '52', '50']);
  assert.deepEqual(paginate(sorted, 2, 25).items.map(item => item.id), ['4', '2', '0']);
  assert.deepEqual([...selected], ['2', '43']);
});

test('pagination clamps after a filter shrinks the list and handles empty data', () => {
  assert.deepEqual(paginate([1, 2], 5, 25), { page: 1, pages: 1, items: [1, 2] });
  assert.deepEqual(paginate([], 0, 25), { page: 1, pages: 1, items: [] });
});

test('provenance reads the historical evaluation and keeps missing model explicit', () => {
  const saved = { checklist_snapshot: { name: 'Saved rules', version: 2 }, created_at: '2026-01-01T00:00:00Z', ai_draft: { model: 'saved-model', prompt_version: 'saved-prompt' } } as Evaluation;
  assert.deepEqual(evaluationProvenance(saved), { checklist: 'Saved rules · v2', model: 'saved-model', prompt: 'saved-prompt', startedAt: '2026-01-01T00:00:00Z', completedAt: null });
  assert.equal(evaluationProvenance({ ...saved, ai_draft: null }).model, 'Не записана');
  assert.equal(evaluationProvenance({ ...saved, ai_draft: { ...saved.ai_draft!, models_used: ['model-a', 'model-b'] } }).model, 'model-a, model-b');
  assert.equal(evaluationProvenance({ ...saved, status: 'failed', ai_draft: null, ai_execution: { models_used: [], attempts: [{ model: 'attempted-model', network_attempted: true }] } }).model, 'Попытки: attempted-model');
});

test('missing run has no results and selected run retains only its own attempt history', () => {
  const history = [
    { id: 'older-run-success', conversation_id: 'same-dialog', batch_id: 'old', status: 'completed' },
    { id: 'retry-source', conversation_id: 'same-dialog', batch_id: 'selected', status: 'failed' },
    { id: 'retry-success', conversation_id: 'same-dialog', batch_id: 'selected', status: 'completed' },
  ] as Evaluation[];
  assert.deepEqual(attemptsForRun(history, ''), []);
  assert.deepEqual(attemptsForRun(history, 'unknown'), []);
  assert.deepEqual(attemptsForRun(history, 'selected').map(item => item.id), ['retry-source', 'retry-success']);
  assert.deepEqual(attemptsForRun(history, 'selected').filter(item => item.status === 'completed').map(item => item.id), ['retry-success']);
  assert.equal(history.length, 3);
});

test('failed provenance lists only actual network attempts, without reusing successful-model metadata', () => {
  const failed = { status: 'failed', checklist_snapshot: { name: 'Saved', version: 1 }, created_at: '2026-01-01T00:00:00Z', ai_draft: null,
    ai_execution: { models_used: ['not-an-attempt'], attempts: [
      { model: 'unconfigured', network_attempted: false }, { model: 'primary', network_attempted: true },
      { model: 'primary', network_attempted: true }, { model: 'backup', network_attempted: true },
      { model: null, network_attempted: true }, { model: 'unknown-attempt' },
    ] } } as Evaluation;
  assert.equal(evaluationProvenance(failed).model, 'Попытки: primary, backup');
  assert.equal(evaluationProvenance({ ...failed, ai_execution: { models_used: ['configured-only'] } }).model, 'Сетевая попытка не записана');
});
