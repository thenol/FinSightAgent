export type Role = "researcher" | "reviewer" | "publisher" | "admin";

export type Envelope<T> = {
  data: T;
  meta?: {
    request_id?: string;
    schema_version?: string;
    next_cursor?: string | null;
  };
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
};

export type Source = {
  id: string;
  code: string;
  name: string;
  trust_tier: string;
  feed_url: string;
  allowed_domains: string[];
  status: string;
  adapter_type?: string;
  rate_limit_per_minute?: number;
  crawl_interval_seconds?: number;
  license?: string;
  consecutive_failures?: number;
  next_retry_at?: string | null;
  last_error_code?: string | null;
  last_success_at?: string | null;
};

export type IngestRun = {
  id: string;
  source_id: string;
  trigger: string;
  started_at: string;
  finished_at?: string | null;
  status: string;
  fetched: number;
  processed: number;
  quarantined: number;
  message?: string | null;
  request_id?: string | null;
};

export type SourceHealth = {
  source: Source;
  health: string;
  consecutive_failures: number;
  last_success_at?: string | null;
  last_run?: IngestRun | null;
  recent_runs: IngestRun[];
};

export type EventItem = {
  id: string;
  event_type: string;
  status: string;
  title: string;
  entity_ids: string[];
  document_ids: string[];
  importance: number;
  urgency: string;
  occurred_at: string;
  version: number;
  confidence?: number;
  key_fields?: Record<string, unknown>;
  missing_required?: string[];
};

export type Claim = {
  id: string;
  subject_text: string;
  predicate: string;
  object_value: Record<string, unknown>;
  status: string;
  confidence: number;
  evidence_ids: string[];
  as_of: string;
};

export type Conflict = {
  id: string;
  event_id: string;
  conflict_type: string;
  severity: string;
  status: string;
  summary: string;
  claim_ids: string[];
  resolution?: string | null;
  version: number;
};

export type EventDetail = EventItem & {
  claims: Claim[];
  fact_card_id?: string | null;
};

export type Evidence = {
  id: string;
  document_id: string;
  revision_id: string;
  locator: Record<string, unknown>;
  excerpt: string;
  locator_type: string;
  extraction_method: string;
  extraction_version: string;
  created_at: string;
  document_title?: string | null;
  document_url?: string | null;
  document_content?: string | null;
};

export type Report = {
  id: string;
  event_id: string;
  version: number;
  status: string;
  report_type: string;
  title: string;
  summary: string;
  claim_ids: string[];
  as_of: string;
  disclaimer: string;
  supersedes_report_id?: string | null;
  change_reason?: string | null;
  content?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
};

export type ReviewTask = {
  id: string;
  object_type: string;
  object_id: string;
  reason_code: string;
  allowed_decisions: string[];
  status: string;
  decision?: string | null;
  reviewer_id?: string | null;
  comment?: string | null;
  resume_from?: string | null;
  blackboard_version?: number | null;
  created_at?: string | null;
  decided_at?: string | null;
};

export type MergeReviewTask = {
  id: string;
  document_id: string;
  candidates: string[];
  status: string;
  decision?: string | null;
  reviewer_id?: string | null;
  decided_at?: string | null;
  created_at?: string | null;
};

export type Workflow = {
  id: string;
  event_id: string;
  trigger_id: string;
  status: string;
  as_of: string;
  current_node?: string | null;
  state_version: number;
  blackboard: Record<string, unknown>;
  error_code?: string | null;
  budget_profile?: string;
};

export type BudgetEntry = {
  id: string;
  workflow_id: string;
  node_name?: string | null;
  dimension: string;
  entry_type: string;
  amount: number;
  created_at?: string | null;
};

export type NodeAttempt = {
  id: string;
  workflow_id: string;
  node_name: string;
  attempt_no: number;
  input_hash: string;
  status: string;
  error_code?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
};

export type Brief = {
  id: string;
  brief_date: string;
  entries: Array<{
    report_id: string;
    event_id: string;
    entity_ids: string[];
    title: string;
    importance: number;
    urgency: string;
    confidence: number;
    novelty: number;
    recency: number;
    score: number;
    rank: number;
  }>;
  candidate_count: number;
  rule_version: string;
};

export type AuditLog = {
  id: string;
  actor_id?: string | null;
  action: string;
  object_type: string;
  object_id?: string | null;
  request_id?: string | null;
  details: Record<string, unknown>;
  created_at?: string | null;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
};

export type LlmProvider = {
  id: string;
  code: string;
  display_name: string;
  protocol: string;
  base_url: string;
  model: string;
  status: string;
  is_default: boolean;
  timeout_seconds: number;
  max_tokens: number;
  temperature: number;
  extra_config?: Record<string, unknown>;
  api_key_configured: boolean;
  api_key_hint: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type LlmPreset = {
  code: string;
  display_name: string;
  protocol: string;
  base_url: string;
  models: string[];
  default_model: string;
};

export type LlmAgentBinding = {
  agent_key: string;
  provider_id?: string | null;
  model_override?: string | null;
  updated_at?: string | null;
};

export type AdminMetrics = {
  workflows: {
    total: number;
    by_status: Record<string, number>;
    success_rate: number | null;
  };
  models: {
    total_runs: number;
    failures: number;
    total_cost_usd: number;
    avg_latency_ms: number | null;
    last_24h_runs: number;
    last_24h_cost_usd: number;
  };
  sources: {
    total: number;
    by_status: Record<string, number>;
    open_quarantine: number;
  };
  reviews: {
    pending: number;
    decided: number;
    manual_review_rate: number;
  };
  outbox: {
    pending: number;
    dead_lettered: number;
  };
  users: {
    total: number;
    active: number;
  };
  citations: {
    completeness_rate: number | null;
    claims_with_evidence: number;
    total_claims: number;
  };
};

export type TransmissionStep = {
  step: number;
  description: string;
};

export type TransmissionChain = {
  chain_id: string;
  mechanism: string;
  steps: TransmissionStep[];
  confidence: number;
};

export type ImpactTarget = {
  target_type: string;
  target_name: string;
  target_code?: string | null;
  direction: string;
  magnitude: string;
  horizon: string;
  confidence: number;
  rationale: string;
  chain_refs?: string[];
  claim_ids?: string[];
};

export type ImpactAnalysis = {
  id: string;
  event_id: string;
  version: number;
  status: string;
  event_title_snapshot: string;
  summary: string;
  transmission_chains: TransmissionChain[];
  impacts: ImpactTarget[];
  macro_assumptions: string[];
  watch_items: string[];
  generated_by: string;
  model_run_id?: string | null;
  degraded: boolean;
  supersedes_id?: string | null;
  created_at?: string | null;
};

export type ResearchTask = {
  id: string;
  plan_id: string;
  name: string;
  agent_key: string;
  description: string;
  dependencies: string[];
  required: boolean;
  status: string;
  input_fields: string[];
  output_field?: string | null;
  output_schema?: string | null;
  output_snapshot?: Record<string, unknown> | null;
  review_reason?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
};

export type ResearchPlan = {
  id: string;
  workflow_id: string;
  question: string;
  objective: string;
  as_of: string;
  status: string;
  budget_profile: string;
  completion_criteria: Record<string, unknown>;
  metadata: Record<string, unknown>;
  tasks: ResearchTask[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type ResearchPlanListItem = {
  id: string;
  workflow_id: string;
  question: string;
  objective: string;
  as_of: string;
  status: string;
  budget_profile: string;
  metadata: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ResearchBlackboard = {
  workflow_id: string;
  research_plan: Record<string, unknown>;
  task_outputs: Record<string, unknown>;
};
