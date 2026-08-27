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

export type ResearchOverview = {
  as_of: string;
  window: string;
  publication_scope: string;
  rule_version: string;
  summary: { direction: string; positive_strength: number; negative_strength: number; event_count: number; confidence: number };
  events: Array<{ event: EventItem; analysis_status: string; direction: string; positive_strength: number; negative_strength: number; confidence: number; horizon?: string | null; affected_targets: Array<{ target_id: string; name: string; target_type: string; direction: string; magnitude: string; horizon: string }>; explanation: string }>;
  targets: Array<{ target_id: string; target_type: string; target_code: string; canonical_name: string; direction: string; net_score: number; confidence: number; event_count: number }>;
  risks: Array<Record<string, unknown>>;
  data_quality: Record<string, unknown>;
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

export type ReviewPolicy = {
  id: string;
  mode: "agent" | "human";
  min_confidence: number;
  source: string;
  updated_by?: string | null;
  updated_at?: string | null;
  emergency_disabled: boolean;
};

export type ReviewQueueItem = ReviewTask & {
  display: {
    title: string;
    type_label: string;
    subtitle: string;
    summary: string;
    href: string;
    reference_id: string;
  };
  context?: {
    event_id?: string;
    event_title?: string;
    event_type?: string;
    occurred_at?: string;
    importance?: number;
    candidate_count?: number;
  };
  risk_level: "high" | "normal";
  priority_score: number;
  priority_band: "critical" | "high" | "normal";
  priority_reasons: string[];
  review_state: string;
  last_auto_review_status?: string | null;
  last_auto_review_at?: string | null;
  last_auto_review_confidence?: number | null;
  last_auto_review_reason?: string | null;
  auto_review_attempt_count: number;
  reviewer_type: "agent" | "human" | "none";
  age_seconds: number;
  sla_seconds: number;
};

export type ReviewQueueOverview = {
  counts: Record<string, number>;
  total: number;
  oldest_pending_at?: string | null;
  refreshed_at: string;
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
  analysis_payload?: Record<string, unknown>;
  quality_report?: {
    gate_passed?: boolean;
    evidence_coverage?: number;
    blockers?: string[];
    warnings?: string[];
  };
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

export type EventTypeRegistryEntry = {
  type_label: string;
  status: string;
  event_count: number;
  promotion_ready: boolean;
  decided_by?: string | null;
  decided_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ImpactPortfolioTarget = {
  id: string;
  target_type: string;
  target_code: string;
  canonical_name: string;
  taxonomy_version: string;
};

export type ImpactSnapshot = {
  id: string;
  target_id: string;
  as_of: string;
  horizon: string;
  scenario_set_id: string;
  positive_gross: number;
  negative_gross: number;
  net_score: number;
  direction: string;
  magnitude: string;
  confidence: number;
  dominant_event_id?: string | null;
  previous_direction?: string | null;
  change_type?: string | null;
  explanation: string;
  contributions: Array<Record<string, unknown>>;
};

export type ImpactDashboardContribution = {
  contribution_id: string;
  event_id: string;
  event_title: string;
  event_occurred_at?: string | null;
  direction: string;
  magnitude: string;
  horizon: string;
  effective_strength: number;
  contribution_share: number;
  base_strength: number;
  event_importance: number;
  assessment_confidence: number;
  path_confidence: number;
  dependency_weight: number;
  time_weight: number;
  valid_from?: string | null;
  expected_peak_at?: string | null;
  valid_to?: string | null;
  rationale: string;
  source_url?: string | null;
  analysis_id?: string | null;
  analysis_version?: number | null;
  target_role?: string;
  relationship_confidence?: number;
  inference_kind?: string;
  publication_scope?: string;
  evidence_refs?: Array<Record<string, unknown>>;
  conditions?: string[];
  invalidation_conditions?: string[];
};

export type ImpactDashboard = {
  target: ImpactPortfolioTarget;
  snapshot: ImpactSnapshot | null;
  contributions: ImpactDashboardContribution[];
  events: Array<Record<string, unknown>>;
  dimensions?: Array<{
    dimension: string;
    positive_gross: number;
    negative_gross: number;
    net_score: number;
    direction: string;
    confidence: number;
  }>;
  calculation: {
    formula: string;
    rule_version: string;
    as_of: string;
  };
};

export type ImpactTimeline = {
  target_id: string;
  granularity: string;
  points: Array<{
    point_at: string;
    positive_gross: number;
    negative_gross: number;
    net_score: number;
    direction: string;
    confidence: number;
    dominant_event_id?: string | null;
  }>;
};

export type ForwardImpactWindow = {
  id: string;
  target_id: string;
  as_of: string;
  window_start: string;
  window_end: string;
  granularity: string;
  scenario_set_id: string;
  status: string;
};

export type ForwardImpactPoint = {
  id: string;
  window_id: string;
  point_at: string;
  scenario_id: string;
  positive_conditional: number;
  negative_conditional: number;
  net_conditional: number;
  positive_expected?: number | null;
  negative_expected?: number | null;
  net_expected?: number | null;
  direction: string;
  confidence: number;
  dominant_catalyst_id?: string | null;
};

export type FutureCalendarSummary = {
  date: string;
  event_count: number;
  event_previews: Array<{
    id: string;
    title: string;
    target_name?: string | null;
    event_type: string;
    scheduled_from?: string | null;
    time_precision: string;
    status: string;
    importance: number;
    direction: string;
  }>;
  hidden_event_count: number;
  uncertain_time_count: number;
  major_event_count: number;
  positive_strength: number;
  negative_strength: number;
  net_strength: number;
  direction: string;
  has_conflict: boolean;
};

export type FutureCalendarEvent = {
  id: string;
  target_id: string;
  target_name?: string;
  target_type?: string | null;
  kind: string;
  title: string;
  event_type: string;
  scheduled_from?: string | null;
  scheduled_to?: string | null;
  time_precision: string;
  status: string;
  importance: number;
  probability_base?: number | null;
  direction: string;
  magnitude: string;
  trigger_definition?: Record<string, unknown>;
  evidence_refs?: Array<Record<string, unknown>>;
};

export type FutureEventDetail = {
  event: { id: string; event_type: string; kind: string };
  current_revision?: {
    title: string;
    description?: string;
    scheduled_from?: string | null;
    scheduled_to?: string | null;
    status: string;
    importance: number;
    probability_base?: number | null;
    source_url?: string | null;
    evidence_refs?: Array<Record<string, unknown>>;
  } | null;
  target_impacts: Array<{
    target_id: string;
    direction: string;
    magnitude: string;
    rationale?: string;
    conditional_strength: number;
  }>;
};

export type FutureCalendarDay = {
  date: string;
  timezone: string;
  scheduled_events: FutureCalendarEvent[];
  active_impacts: Array<{
    catalyst_id: string;
    target_id: string;
    target_name: string;
    event_title: string;
    direction: string;
    magnitude: string;
    conditional_strength: number;
    occurrence_probability?: number | null;
    rationale: string;
  }>;
  target_summary: Array<{
    target_id: string;
    target_name: string;
    positive_strength: number;
    negative_strength: number;
    net_strength: number;
    direction: string;
    event_count: number;
  }>;
};

export type MarketInstrument = {
  id: string;
  market: "cn" | "hk" | "us";
  symbol: string;
  name: string;
  instrument_type: "index" | "sector" | "etf" | "stock";
  exchange: string;
  currency: string;
  timezone: string;
};

export type IndustryTaxonomy = {
  id: string;
  standard: string;
  version: string;
  name: string;
  status: string;
  source: string;
};

export type IndustryClassification = {
  id: string;
  taxonomy_id: string;
  code: string;
  name: string;
  level: number;
  aliases: string[];
  status: string;
};

export type ImpactTargetMapping = {
  id: string;
  target_id: string;
  mapping_type: "instrument" | "industry" | "market";
  mapping_code: string;
  weight: number;
  confidence: number;
  status: "proposed" | "approved" | "rejected" | "retired";
  reason: string;
  source: string;
  reviewed_at?: string | null;
};

export type MarketMasterDataImportRun = {
  id: string;
  standard: string;
  version: string;
  source: string;
  source_hash: string;
  status: "validated" | "rejected" | "published";
  classification_count: number;
  membership_count: number;
  errors: string[];
  warnings: string[];
  source_metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
  published_at?: string | null;
};

export type MarketOutlook = {
  instrument_id: string;
  as_of: string;
  horizon: number;
  direction: "positive" | "mixed" | "negative" | "unknown";
  probabilities: { up: number; flat: number; down: number } | null;
  expected_return_p10: number | null;
  expected_return_p50: number | null;
  expected_return_p90: number | null;
  confidence: number;
  forecast_status: "insufficient_data" | "uncalibrated" | "ready" | string;
  data_status: string;
  rule_version: string;
  calibration_version_id: string | null;
  calibration_method: string | null;
  contributions: Array<{
    source: string;
    score: number;
    weight: number;
    configured_weight: number;
    status: "available" | "unavailable" | string;
    confidence: number;
    explanation: string;
    provenance: {
      reason?: string | null;
      source_hash?: string;
      rule_version?: string;
      sources?: Array<{
        target_id: string;
        target_name: string;
        snapshot_id: string;
        dominant_event_id: string | null;
        confidence: number;
      }>;
    };
  }>;
  risks: string[];
  blocking_reasons: string[];
  available_observations: number;
  required_observations: number;
  coverage: number;
  factor_coverage: number;
  latest_observed_at: string | null;
};

export type ChampionChallengerComparison = {
  comparable_sample_count: number;
  entries: Array<{
    model_key: string;
    report: MarketForecastEvaluation["report"];
  }>;
  incumbent_model_key: string | null;
  recommended_model_key: string | null;
  decision: string;
  decision_reasons: string[];
};

export type HistoricalForecastReplayReceipt = {
  forecast_from: string;
  forecast_to: string;
  scheduled_slots: number;
  processed_slots: number;
  created_count: number;
  reused_count: number;
  insufficient_count: number;
  settled_count: number;
  pending_outcome_count: number;
  excluded_outcome_count: number;
  evaluation_as_of: string | null;
  run_ids: string[];
  warnings: string[];
  status: string;
  source_provider: string;
  rule_version: string;
};

export type ForecastCalibrationBin = {
  lower: number;
  upper: number;
  count: number;
  mean_confidence: number | null;
  empirical_accuracy: number | null;
};

export type MarketForecastEvaluation = {
  report: {
    sample_count: number;
    eligible_count: number;
    coverage: number | null;
    accuracy: number | null;
    brier_score: number | null;
    log_loss: number | null;
    expected_calibration_error: number | null;
    class_counts: { up: number; flat: number; down: number };
    calibration_bins: ForecastCalibrationBin[];
    rule_version: string;
  };
  exclusions: Record<string, number>;
};

export type MarketCalibrationVersion = {
  id: string;
  model_key: string;
  version: string;
  horizon: number;
  market: string;
  status: string;
  method: string;
  parameters: Record<string, number>;
  metrics: Record<string, unknown>;
  train_start: string;
  train_end: string;
  sample_count: number;
  created_by: string;
  created_at: string;
  published_at: string | null;
};
