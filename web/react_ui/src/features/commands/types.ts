import type { ApiResponse } from "../../api";

export type CommandTargetKind = "device" | "process";

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
