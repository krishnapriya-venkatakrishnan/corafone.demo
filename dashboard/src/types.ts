// Mirrors the Pydantic response models in app/dashboard_api.py.

export interface AccountSummary {
  account_id: number;
  customer_name: string;
  phone_number: string;
  current_balance: number;
  status: string;
}

export interface ComplianceSummary {
  total_calls: number;
  mini_miranda_pass_rate: number | null;
  avg_tone_score: number | null;
  hallucination_count: number;
  prohibited_conduct_count: number;
  total_judge_cost_usd: number | null;
}

export interface DashboardSummary {
  account: AccountSummary | null;
  compliance: ComplianceSummary;
}

export interface CallRecord {
  session_id: string;
  account_id: number;
  created_at: string | null;
  total_duration_seconds: number;
  avg_latency_ms: number;
  barge_in_count: number;
  disposition_code: string;
  error_count: number;
  transcript_path: string | null;
  mini_miranda_passed: boolean | null;
  pii_redacted_correctly: boolean | null;
  hallucination_detected: boolean | null;
  identity_verified_before_disclosure: boolean | null;
  prohibited_conduct_detected: boolean | null;
  right_to_cease_honored: boolean | null;
  tone_score: number | null;
  judge_reasoning: string | null;
  judge_cost_usd: number | null;
}

export interface PaymentPlanRecord {
  plan_id: number;
  account_id: number;
  num_installments: number;
  amount_per_installment: number;
  total_amount: number;
  start_date: string;
  status: string;
  created_at: string;
}

export interface ScheduledCallbackRecord {
  callback_id: number;
  account_id: number;
  callback_time: string;
  status: string;
  created_at: string;
}

export interface Commitments {
  payment_plans: PaymentPlanRecord[];
  scheduled_callbacks: ScheduledCallbackRecord[];
}

export interface TranscriptResponse {
  session_id: string;
  transcript: string;
}

export interface QueueRecommendation {
  account: AccountSummary | null;
  reasoning: string;
  candidates_considered: number;
}

// --- Scenario runner (SSE events from /api/dashboard/scenarios/run) ---
export interface ScenarioTrialDetail {
  trial: number;
  outcome_met: boolean;
  reasoning: string;
  multi_sentence_violations: string[];
  duplicate_tool_calls: string[];
  transcript: string[];
}

export interface ScenarioResultEvent {
  type: "scenario_result";
  scenario: string;
  expected_outcome: string;
  trials: number;
  judge_passes: number;
  hard_failures: string[];
  trial_details: ScenarioTrialDetail[];
}

export interface ScenarioDoneEvent {
  type: "done";
}

export type ScenarioEvent = ScenarioResultEvent | ScenarioDoneEvent;
