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

/** Provenance always comes from the saved evaluation, never a current selector. */
export function evaluationProvenance(evaluation: Evaluation) {
  return {
    checklist: `${evaluation.checklist_snapshot.name} · v${evaluation.checklist_snapshot.version}`,
    model: evaluation.status === 'failed' && evaluation.ai_execution?.models_used?.length ? `Попытки: ${evaluation.ai_execution.models_used.join(', ')}` : evaluation.ai_draft?.models_used?.length ? evaluation.ai_draft.models_used.join(', ') : evaluation.ai_draft?.model || 'Не записана',
    prompt: evaluation.ai_draft?.prompt_version || 'Не записана',
    startedAt: evaluation.created_at,
    completedAt: evaluation.completed_at || null,
  };
}
