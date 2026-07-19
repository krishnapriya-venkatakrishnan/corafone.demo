// Mirrors the Pydantic response models in app/dashboard_api.py.

export interface AccountSummary {
  account_id: number;
  customer_name: string;
  phone_number: string;
  current_balance: number;
  status: string;
  requires_manual_review: boolean;
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

// --- Scenario runner ---
export interface ScenarioInfo {
  name: string;
  expected_outcome: string;
}

export interface ScenarioResult {
  scenario: string;
  expected_outcome: string;
  passed: boolean;
  reasoning: string;
  hard_failures: string[];
  transcript: string[];
}

// SSE events from /api/dashboard/scenarios/run (run-all)
export interface ScenarioResultEvent extends ScenarioResult {
  type: "scenario_result";
}

export interface ScenarioDoneEvent {
  type: "done";
}

export type ScenarioEvent = ScenarioResultEvent | ScenarioDoneEvent;

// --- Validator playground ---
export type Cadence = "once" | "weekly" | "biweekly" | "monthly";

export interface ValidateRequest {
  total_amount: number;
  number_of_payments: number;
  cadence: Cadence;
  first_payment_date: string; // YYYY-MM-DD
  discount_already_countered: boolean;
}

export interface ValidateOffer {
  tier: string;
  total: string;
  payments: string[];
  dates: string[];
  cadence: string;
}

export interface ValidateResponse {
  decision: "ACCEPT" | "COUNTER" | "NO_AGREEMENT";
  reason: string;
  offer: ValidateOffer | null;
  violations: string[];
}
