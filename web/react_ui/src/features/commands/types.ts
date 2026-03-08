import type { ApiResponse } from "../../api";

export type CommandTargetKind = "device" | "process";
export type CommandHistoryMode = "live" | "journal" | "restore";

export type CommandHistoryEntry = {
  id: string;
  ts_wall_s: number;
  target_kind: CommandTargetKind;
  target_id: string;
  action: string;
  params: Record<string, unknown>;
  response: ApiResponse<unknown>;
  source: string;
};

export type CommandJournalStatus = {
  enabled: boolean;
  path?: string | null;
  start_error?: string | null;
  queue_depth?: number;
  queue_max?: number;
  batch_size?: number;
  flush_interval_ms?: number;
  retention?: {
    max_rows?: number | null;
    max_age_days?: number | null;
  } | null;
  written?: number;
  dropped?: number;
  write_errors?: number;
  pruned_rows?: number;
  last_error?: string | null;
  thread_alive?: boolean;
};

export type CommandJournalEntry = {
  id: number;
  ts_wall_s: number;
  ts_mono_s: number;
  instance_id: string;
  device_id: string;
  target_kind: CommandTargetKind;
  target_id: string;
  action: string;
  params_json: string;
  params: Record<string, unknown> | null;
  params_parse_error: string | null;
  ok: boolean;
  status: string | null;
  error_json: string;
  error: Record<string, unknown> | null;
  result_json: string;
  result: unknown;
  request_id: string | null;
  caller_process_id: string | null;
  source_kind: string | null;
  source_id: string | null;
  source: string;
  is_remote_target: boolean;
};

export type CommandRestorePreviewRow = {
  id: number;
  ts_wall_s: number;
  target_kind: CommandTargetKind;
  target_id: string;
  action: string;
  source: string;
  ok: boolean;
  is_remote_target: boolean;
  include: boolean;
  skip_reason: string | null;
  params: Record<string, unknown> | null;
  params_json: string;
};

export type CommandRestoreExecutionRow = {
  id: number;
  target_kind: CommandTargetKind;
  target_id: string;
  action: string;
  ok: boolean;
  error_code: string | null;
  error_message: string | null;
};

export type CommandRestoreExecutionReport = {
  attempted: number;
  executed: number;
  skipped: number;
  ok: number;
  error: number;
  rows: CommandRestoreExecutionRow[];
};
