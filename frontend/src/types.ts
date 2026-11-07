// Mirrors the shared Pydantic v1 contract. Scoring belongs exclusively to the backend.
export type AnswerValue = boolean | number | 'N/A';
export interface Message { id: string; role: string; text: string; timestamp: string | null }
export interface ConversationInput {
  external_id?: string | null; source?: string;
  messages: Array<Omit<Message, 'id'> & { id?: string | null }>;
  metadata?: Record<string, unknown>;
}
export interface Conversation extends ConversationInput {
  id: string; messages: Message[]; source: string; metadata: Record<string, unknown>; created_at: string;
}
export interface ScaleStep { value: number; label: string; description: string }
export interface Section { id: string; title: string }
export interface Criterion {
  id: string; section_id: string; title: string; description: string;
  weight: number; answer_type: 'boolean' | 'scale' | 'flag'; scale: ScaleStep[];
  allow_na: boolean; na_condition: string | null;
}
export interface ChecklistCreate { key: string; name: string; description: string; sections: Section[]; criteria: Criterion[] }
export interface ChecklistVersion extends ChecklistCreate { id: string; version: number; created_at: string }
export interface CriterionResult {
  criterion_id: string; value: AnswerValue; reasoning: string; evidence_message_ids: string[]; confidence?: number | null;
}
export interface CriterionScore { criterion_id: string; value: AnswerValue; applicable: boolean; weight: number; earned_points: number | null }
export interface SectionScore { section_id: string; applicable_weight: number; earned_points: number; score: number | null }
export interface ScoreSummary { criteria: CriterionScore[]; sections: SectionScore[]; applicable_weight: number; earned_points: number; total: number | null; gross_error: boolean }
export interface Evaluation {
  id: string; batch_id: string; conversation_id: string; checklist_snapshot: ChecklistVersion;
  status: 'queued' | 'running' | 'completed' | 'failed'; created_at: string; updated_at: string;
  dataset_id?: string | null; attempt_number?: number; retry_of?: string | null; completed_at?: string | null;
  ai_execution?: { models_used: string[]; attempts?: Array<{ model?: string | null; network_attempted?: boolean; [key: string]: unknown }>; [key: string]: unknown } | null;
  ai_draft: { criteria: CriterionResult[]; raw_response: string; provider: string; model: string; prompt_version: string; models_used?: string[] } | null;
  raw_response: string | null; original_score: ScoreSummary | null; final_score: ScoreSummary | null;
  final_criteria: CriterionResult[] | null; error: { code: string; message: string; retryable: boolean } | null;
}
export type DatasetKind = 'dev' | 'prod' | 'test';
export interface Dataset { id: string; name: string; kind: DatasetKind; group_id: string | null; conversation_ids: string[]; created_at: string }
export interface EvaluationRun { id: string; dataset_id: string; conversation_ids: string[]; checklist_snapshot: ChecklistVersion; created_at: string }
export interface EvaluationBatch { id: string; evaluations: Evaluation[]; run: EvaluationRun | null; counts: { total: number; queued: number; running: number; completed: number; failed: number } }
export interface RetryResult { batch: EvaluationBatch; retried: number; skipped: number }
export interface ExportIssue { code: string; path: string; message: string; conversation_id?: string }
export interface OfficialExportReport { valid: boolean; errors: ExportIssue[]; warnings: ExportIssue[]; counts: { expected: number; actual: number; ok: number; error: number }; dataset_kind: DatasetKind }
export interface AppealCreate { criterion_id: string; reason: string; author: string; proposed_value?: AnswerValue | null }
export interface Appeal extends AppealCreate { id: string; evaluation_id: string; status: 'open' | 'accepted' | 'rejected'; created_at: string }
export interface ReviewDecisionCreate { outcome: 'accepted' | 'rejected'; reviewer: string; reason: string; replacement?: CriterionResult | null }
export interface ReviewDecision extends ReviewDecisionCreate { id: string; appeal_id: string; created_at: string }
export interface ImportResult { conversations: Conversation[]; imported: number; existing: number; dataset: Dataset | null }
