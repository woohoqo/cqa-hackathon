import type { Conversation, Evaluation } from './types.ts';

/** A missing/unfinished score never becomes zero; ties retain dataset order. */
export function ascendingFinalScore(conversations: Conversation[], evaluations: ReadonlyMap<string, Evaluation>): Conversation[] {
  const score = (conversation: Conversation): number | null => {
    const evaluation = evaluations.get(conversation.id);
    const value = evaluation?.final_score?.total;
    return evaluation?.status === 'completed' && typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  return conversations.map((conversation, index) => ({ conversation, index, score: score(conversation) }))
    .sort((a, b) => a.score === null ? b.score === null ? a.index - b.index : 1 : b.score === null ? -1 : a.score - b.score || a.index - b.index)
    .map(item => item.conversation);
}

export function paginate<T>(items: T[], requestedPage: number, pageSize: number) {
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const page = Math.min(Math.max(1, requestedPage), pages);
  return { page, pages, items: items.slice((page - 1) * pageSize, page * pageSize) };
}

/** An absent selection has no results; never substitute another run's history. */
export function attemptsForRun(evaluations: Evaluation[], runId: string): Evaluation[] {
  return runId ? evaluations.filter(evaluation => evaluation.batch_id === runId) : [];
}

export function selectedConversationIds(conversations: Conversation[], selected: ReadonlySet<string>, pendingIds: ReadonlySet<string>): string[] {
  return conversations.filter(conversation => selected.has(conversation.id) && !pendingIds.has(conversation.id)).map(conversation => conversation.id);
}

export interface EvaluationSubmissionIntent { fingerprint: string; key: string }

/** Keep the same logical POST until a response acknowledges it, even if transport failed. */
export function evaluationSubmissionIntent(previous: EvaluationSubmissionIntent | null, datasetId: string, checklistId: string, conversationIds: string[], createKey: () => string): EvaluationSubmissionIntent {
  const fingerprint = JSON.stringify([datasetId, checklistId, [...conversationIds].sort()]);
  return previous?.fingerprint === fingerprint ? previous : { fingerprint, key: createKey() };
}

/** Follow the latest server attempt unless the reviewer explicitly pinned history. */
export function displayedAttempt(history: Evaluation[], latestId?: string, selectedId?: string | null): Evaluation | undefined {
  const latest = history.find(evaluation => evaluation.id === latestId) || history[0];
  return selectedId ? history.find(evaluation => evaluation.id === selectedId) || latest : latest;
}

/** Provenance always comes from the saved evaluation, never a current selector. */
export function evaluationProvenance(evaluation: Evaluation) {
  const attemptedModels = [...new Set((evaluation.ai_execution?.attempts || [])
    .filter(attempt => attempt.network_attempted === true && typeof attempt.model === 'string' && attempt.model.trim())
    .map(attempt => attempt.model as string))];
  return {
    checklist: `${evaluation.checklist_snapshot.name} · v${evaluation.checklist_snapshot.version}`,
    model: evaluation.status === 'failed' ? attemptedModels.length ? `Попытки: ${attemptedModels.join(', ')}` : 'Сетевая попытка не записана' : evaluation.ai_draft?.models_used?.length ? evaluation.ai_draft.models_used.join(', ') : evaluation.ai_draft?.model || 'Не записана',
    prompt: evaluation.ai_draft?.prompt_version || 'Не записана',
    startedAt: evaluation.created_at,
    completedAt: evaluation.completed_at || null,
  };
}
