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
  attrs?: Record<string, unknown> | null;
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
  context_id?: number | null;
  context_fields?: Record<string, unknown> | null;
  truncated?: boolean;
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
  context_id?: number | null;
  context_fields?: Record<string, unknown> | null;
  truncated?: boolean;
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
  hb_age_s?: number | null;
  last_error?: string | null;
  restart_policy?: string | null;
  restart_count?: number | null;
  last_exit_code?: number | null;
  rpc_endpoint?: string | null;
  registered?: boolean;
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
};

export type InterlockRuleStatus = {
  rule_id: string;
  name: string;
  enabled: boolean;
  match: RouteMatch;
  telemetry: InterlockTelemetryStatus[];
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

export type CommandInterceptorRoute = {
  order: number;
  process_id: string;
  device_id: string;
  action: string;
};
