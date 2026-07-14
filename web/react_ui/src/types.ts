export type TelemetrySignal = {
  value: number | string | boolean | null;
  units?: string | null;
  quality?: string | null;
  ts?: { t_wall?: number; t_mono?: number };
};

export type TelemetryPayload = {
  device_id: string;
  signals: Record<string, TelemetrySignal>;
  ts?: { t_wall?: number; t_mono?: number };
};

export type TelemetryMessage = {
  topic: string;
  payload: TelemetryPayload;
};

export type StreamCatalogEntry = {
  device_id: string;
  stream: string;
  dtype?: string | null;
  shape?: number[];
  units?: string | null;
  description?: string | null;
};

export type StreamFramePayload = {
  version?: number;
  device_id: string;
  stream: string;
  seq?: number | null;
  t0_mono_ns?: number | null;
  t0_wall_ns?: number | null;
  dtype?: string | null;
  shape?: number[];
  values?: unknown;
  encoding?: string | null;
  byte_order?: string | null;
  byte_length?: number | null;
  context_id?: number | null;
  context_fields?: Record<string, unknown> | null;
  truncated?: boolean;
  original_shape?: number[];
  original_point_count?: number;
  max_payload_points?: number;
};

export type StreamFrameMessage = {
  topic: string;
  payload: StreamFramePayload;
};

export type StreamAnalysisOutputPayload = {
  version?: number;
  workspace_id?: string | null;
  output_id?: string | null;
  node_id?: string | null;
  kind?: string | null;
  device_id?: string | null;
  stream?: string | null;
  seq?: number | null;
  t0_mono_ns?: number | null;
  t0_wall_ns?: number | null;
  channel_index?: number | null;
  channel_count?: number | null;
  value?: unknown;
  encoding?: string | null;
  dtype?: string | null;
  byte_order?: string | null;
  byte_length?: number | null;
  context_id?: number | null;
  context_fields?: Record<string, unknown> | null;
  truncated?: boolean;
  original_shape?: number[];
  original_point_count?: number;
  max_payload_points?: number;
};

export type StreamAnalysisMessage = {
  topic: string;
  payload: StreamAnalysisOutputPayload;
};

export type LogEntry = {
  version?: number;
  severity?: string | null;
  topic?: string | null;
  source_kind?: string | null;
  source_id?: string | null;
  device_id?: string | null;
  process_id?: string | null;
  stream?: string | null;
  message?: string | null;
  payload_json?: string | null;
  ts?: { t_wall?: number; t_mono?: number };
};

export type LogMessage = {
  topic: string;
  payload: LogEntry;
};

export type DeviceStatus = {
  device_id: string;
  liveness: "ONLINE" | "OFFLINE" | "DISCONNECTED" | string;
  is_remote?: boolean;
  source_kind?: string | null;
  owner_peer_id?: string | null;
  remote_device_id?: string | null;
  registered?: boolean;
  hb_age_s: number | null;
  telemetry_age_s: number | null;
  driver_state?: string | null;
  device_state?: string | null;
  device_reachable?: boolean | null;
  last_error?: string | null;
};

export type ProcessStatus = {
  process_id: string;
  state: string;
  argv?: string[] | null;
  pid?: number | null;
  rss_bytes?: number | null;
  hb_age_s?: number | null;
  last_error?: string | null;
  restart_policy?: string | null;
  restart_count?: number | null;
  last_exit_code?: number | null;
  rpc_endpoint?: string | null;
  registered?: boolean;
  // Federation: set for mirrored (remote) processes.
  is_remote?: boolean;
  source_kind?: string | null;
  owner_peer_id?: string | null;
  remote_process_id?: string | null;
  liveness?: "ONLINE" | "OFFLINE" | "DISCONNECTED" | string;
};

export type CapabilityParam = {
  name: string;
  kind?: string;
  required?: boolean;
  default?: unknown;
  annotation?: string | null;
};

export type CapabilityMember = {
  name: string;
  params?: CapabilityParam[] | null;
  doc?: string | null;
  kind?: string;
  readable?: boolean;
  settable?: boolean;
  return_annotation?: string | null;
  // Federation: false when this action is denied across the federation link
  // (annotated server-side for mirrored processes). Undefined for local processes.
  federation_allowed?: boolean;
};

export type TraceKey = {
  deviceId: string;
  signal: string;
  units?: string | null;
  valueKind?: "number" | "boolean";
};

export type PinnedCommand = {
  action: string;
  label?: string | null;
};

export type CommandDeckTargetKind = "device" | "process";
export type CommandDeckEntryKind = "command" | "telemetry";

export type CommandDeckCommandEntry = {
  id: string;
  kind?: "command";
  targetKind: CommandDeckTargetKind;
  targetId: string;
  action: string;
  label?: string | null;
  group?: string | null;
  paramsDraft?: Record<string, string>;
  createdAt?: number | null;
};

export type CommandDeckTelemetryEntry = {
  id: string;
  kind: "telemetry";
  deviceId: string;
  signal: string;
  format?: "auto" | "fixed" | "scientific";
  decimals?: number | null;
  label?: string | null;
  group?: string | null;
  createdAt?: number | null;
};

export type CommandDeckEntry = CommandDeckCommandEntry | CommandDeckTelemetryEntry;

export type RouteMatch = {
  device_id: string;
  action: string;
};

export type FollowerEffectStatus = {
  device_id: string;
  action: string;
  param: string;
};

export type FollowerRuleStatus = {
  rule_id: string;
  name: string;
  enabled: boolean;
  device_id: string;
  trigger_action: string;
  trigger_param: string;
  min_freq_hz: number;
  max_freq_hz: number;
  max_step_hz?: number | null;
  current_freq_signal?: string | null;
  telemetry_max_age_s?: number | null;
  csv_path: string;
  effects: FollowerEffectStatus[];
};

export type InterlockTelemetryStatus = {
  as: string;
  device_id: string;
  signal: string;
  max_age_s: number;
  required: boolean;
};

export type InterlockRuleStatus = {
  rule_id: string;
  name: string;
  enabled: boolean;
  match: RouteMatch;
  telemetry: InterlockTelemetryStatus[];
  condition?: unknown;
  on_block?: { code?: string | null; message?: string | null } | null;
  has_allow_transform: boolean;
};

export type InterlockInterceptorStatus = {
  interceptor_id: string;
  enabled: boolean;
  source?: string | null;
  rule_count: number;
  enabled_rule_count?: number;
  routes: RouteMatch[];
  rules: InterlockRuleStatus[];
};

export type WatchdogRuleStatus = {
  name: string;
  severity: string;
  message?: string | null;
  condition?: unknown;
  telemetry?: InterlockTelemetryStatus[];
  actions?: {
    // Device commands carry device_id; process actions carry process_id.
    device_id?: string;
    process_id?: string;
    action: string;
    params: Record<string, unknown>;
    timeout_s?: number | null;
    retries?: number;
  }[];
  stable_for_s?: number | null;
  cooldown_s?: number | null;
  latch?: boolean;
  on_unknown?: string | null;
  latched: boolean;
  alarm?: boolean | null;
  unknown?: boolean | null;
  snapshot?: Record<string, unknown> | null;
  last_evaluated_mono?: number | null;
  stable_since_mono?: number | null;
  last_trigger_mono?: number | null;
};

export type WatchdogStatus = {
  watchdog_id: string;
  enabled: boolean;
  rules: WatchdogRuleStatus[];
};

export type CommandInterceptorRoute = {
  order: number;
  process_id: string;
  device_id: string;
  action: string;
};

export type StateMachineTransition = {
  from_state?: string | null;
  to_state?: string | null;
  reason?: string | null;
  ts?: { t_wall?: number; t_mono?: number } | null;
  metadata?: Record<string, unknown> | null;
};

export type StateMachineStatus = {
  state: string;
  active_states?: string[];
  state_since?: { t_wall?: number; t_mono?: number } | null;
  state_age_s?: number | null;
  last_error?: string | null;
  last_transition?: StateMachineTransition | null;
  allowed_next_states: string[];
  status_detail?: Record<string, unknown> | null;
  status_age_s?: number | null;
};

export type StateMachineHistoryEntry = {
  event?: string | null;
  from_state?: string | null;
  to_state?: string | null;
  state?: string | null;
  reason?: string | null;
  message?: string | null;
  ok?: boolean | null;
  source?: string | null;
  trigger_type?: string | null;
  trigger_name?: string | null;
  result?: string | null;
  error?: string | null;
  ts?: { t_wall?: number; t_mono?: number } | null;
  metadata?: Record<string, unknown> | null;
  raw?: unknown;
};

export type StateMachineGraphTransition = {
  from_state?: string | null;
  to_state?: string | null;
  note?: string | null;
};

export type StateMachineGraphEffect = {
  device_id: string;
  device_action: string;
  params?: Record<string, unknown> | null;
  note?: string | null;
};

export type StateMachineGraphAction = {
  name: string;
  doc?: string | null;
  params?: CapabilityParam[] | null;
  transitions?: StateMachineGraphTransition[];
  effects?: StateMachineGraphEffect[];
};

export type StateMachineGraph = {
  namespace: string;
  initial_state?: string | null;
  states: string[];
  transitions: StateMachineGraphTransition[];
  actions: StateMachineGraphAction[];
};
