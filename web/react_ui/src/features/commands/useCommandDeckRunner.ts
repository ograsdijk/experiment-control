import { notifications } from "@mantine/notifications";

import { fetchCapabilities } from "../../api";
import { coerceParamValue } from "../../components/ParamInput";
import type {
  CapabilityMember,
  CommandDeckCommandEntry,
  CommandDeckTargetKind,
  CommandDeckTelemetryEntry,
  DeviceStatus,
  ProcessStatus,
} from "../../types";
import type { ApiError } from "../../api";
import { formatApiErrorToastMessage } from "../common/api_error";
import {
  effectiveDeviceMemberParams,
  mapDeviceActionForMember,
} from "../devices/command_schema";
import { useDevicesContext } from "../devices/DevicesContext";
import { useLayout } from "../layout/LayoutContext";
import { useCommands } from "./CommandsContext";
import { isCommandDeckCommandEntry, normalizeDeckGroup } from "./utils";

/**
 * Command-deck create / add-from-modal / run handlers.
 *
 * The "active" half of the command-deck surface that complements
 * `useCommandDeckMutations` (pure state mutators). These are the
 * handlers that:
 *
 * - **Create** new entries with sensible defaults pulled from the
 *   current device / process / signal state.
 * - **Push** entries from the inline device-command or process-command
 *   modals into the deck (`addToDeckFromCommandModal` /
 *   `addToDeckFromProcessCommandModal`).
 * - **Run** a deck entry — looks up the entry's capabilities,
 *   coerces params, dispatches via `sendDeviceCommand` /
 *   `sendProcessCommand`, and triggers downstream refreshes
 *   (process list / HDF / Influx writers).
 *
 * **Args**: every external dependency is explicit so this hook is
 * trivially testable. The hook reads only deck state + ordered
 * devices from context; everything process / command-modal /
 * capability / send-RPC related comes in as args because those live
 * across several App-level controllers.
 */

export interface CommandDeckRunnerArgs {
  // Process-side state from useProcessesController
  processes: ProcessStatus[];
  capabilitiesByProcess: Record<string, unknown[]>;
  ensureProcessCapabilitiesLoaded: (processId: string) => Promise<unknown[]>;
  refreshProcesses: () => Promise<ProcessStatus[]>;

  // Device-side capability cache (App-owned)
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  setCapabilitiesByDevice: React.Dispatch<
    React.SetStateAction<Record<string, CapabilityMember[]>>
  >;

  // Live telemetry signals (for telemetry-entry signal autodefault)
  latestByDevice: Record<string, Record<string, { value?: unknown }>>;

  // RPC senders
  sendDeviceCommand: (
    targetId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<{ ok: boolean; error?: ApiError }>;
  sendProcessCommand: (
    targetId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<{ ok: boolean; error?: ApiError }>;

  // Command-modal draft state (drives addToDeckFromCommandModal)
  commandDevice: string | null;
  commandAction: string;
  commandLabel: string;
  commandParamValues: Record<string, string>;

  // Process-command-modal draft state
  processCommandProcessId: string | null;
  processCommandAction: string;
  processCommandParamValues: Record<string, string>;

  // Pure mutator from useCommandDeckMutations
  updateCommandDeckCommandEntry: (
    entryId: string,
    patch: Partial<
      Pick<
        CommandDeckCommandEntry,
        "targetKind" | "targetId" | "action" | "label" | "group" | "paramsDraft"
      >
    >
  ) => void;

  // Writer-specific cross-refresh
  hdfWriterProcessId: string | null;
  refreshHdfWriterStatus: (processId: string) => Promise<unknown>;
  influxWriterProcessId: string | null;
  refreshInfluxStatus: (processId: string) => Promise<unknown>;
}

export function useCommandDeckRunner(args: CommandDeckRunnerArgs) {
  const {
    processes,
    capabilitiesByProcess,
    ensureProcessCapabilitiesLoaded,
    refreshProcesses,
    capabilitiesByDevice,
    setCapabilitiesByDevice,
    latestByDevice,
    sendDeviceCommand,
    sendProcessCommand,
    commandDevice,
    commandAction,
    commandLabel,
    commandParamValues,
    processCommandProcessId,
    processCommandAction,
    processCommandParamValues,
    updateCommandDeckCommandEntry,
    hdfWriterProcessId,
    refreshHdfWriterStatus,
    influxWriterProcessId,
    refreshInfluxStatus,
  } = args;

  const {
    commandDeck,
    setCommandDeck,
    commandDeckBusyById,
    setCommandDeckBusyById,
    commandDeckIdRef,
  } = useCommands();
  const { orderedDevices } = useDevicesContext();
  const { setDevicePanelTab } = useLayout();

  const createCommandDeckCommandEntry = (
    partial?: Partial<
      Pick<
        CommandDeckCommandEntry,
        "id" | "targetKind" | "targetId" | "action" | "label" | "group" | "paramsDraft"
      >
    >
  ): CommandDeckCommandEntry => {
    const id =
      typeof partial?.id === "string" && partial.id.trim().length > 0
        ? partial.id.trim()
        : `deck-${Date.now()}-${commandDeckIdRef.current++}`;
    const defaultTargetKind: CommandDeckTargetKind =
      partial?.targetKind ??
      (orderedDevices[0]?.device_id
        ? "device"
        : processes[0]?.process_id
        ? "process"
        : "device");
    const fallbackTargetId =
      defaultTargetKind === "process"
        ? (processes[0]?.process_id ?? "")
        : (orderedDevices[0]?.device_id ?? "");
    return {
      id,
      kind: "command",
      targetKind: defaultTargetKind,
      targetId: String(partial?.targetId ?? fallbackTargetId).trim(),
      action: String(partial?.action ?? "").trim(),
      label:
        typeof partial?.label === "string" && partial.label.trim().length > 0
          ? partial.label.trim()
          : undefined,
      group: normalizeDeckGroup(partial?.group),
      paramsDraft: { ...(partial?.paramsDraft ?? {}) },
      createdAt: Date.now(),
    };
  };

  const createCommandDeckTelemetryEntry = (
    partial?: Partial<
      Pick<
        CommandDeckTelemetryEntry,
        "id" | "deviceId" | "signal" | "format" | "decimals" | "label" | "group"
      >
    >
  ): CommandDeckTelemetryEntry => {
    const id =
      typeof partial?.id === "string" && partial.id.trim().length > 0
        ? partial.id.trim()
        : `deck-${Date.now()}-${commandDeckIdRef.current++}`;
    const fallbackDeviceId = orderedDevices[0]?.device_id ?? "";
    const deviceId = String(partial?.deviceId ?? fallbackDeviceId).trim();
    const signalOptions = [
      ...Object.keys(latestByDevice[deviceId] ?? {}),
    ].sort((a, b) => a.localeCompare(b));
    const fallbackSignal = signalOptions[0] ?? "";
    const formatRaw = String(partial?.format ?? "auto").trim().toLowerCase();
    const format =
      formatRaw === "fixed" || formatRaw === "scientific" ? formatRaw : "auto";
    const decimalsRaw = partial?.decimals;
    const decimals =
      typeof decimalsRaw === "number" && Number.isFinite(decimalsRaw)
        ? Math.max(0, Math.min(12, Math.trunc(decimalsRaw)))
        : 3;
    return {
      id,
      kind: "telemetry",
      deviceId,
      signal: String(partial?.signal ?? fallbackSignal).trim(),
      format,
      decimals,
      label:
        typeof partial?.label === "string" && partial.label.trim().length > 0
          ? partial.label.trim()
          : undefined,
      group: normalizeDeckGroup(partial?.group),
      createdAt: Date.now(),
    };
  };

  const addCommandDeckCommandEntry = (
    partial?: Partial<
      Pick<
        CommandDeckCommandEntry,
        "id" | "targetKind" | "targetId" | "action" | "label" | "group" | "paramsDraft"
      >
    >
  ) => {
    const next = createCommandDeckCommandEntry(partial);
    setCommandDeck((prev) => [...prev, next]);
    setDevicePanelTab("deck");
    return next;
  };

  const addCommandDeckTelemetryEntry = (
    partial?: Partial<
      Pick<
        CommandDeckTelemetryEntry,
        "id" | "deviceId" | "signal" | "format" | "decimals" | "label" | "group"
      >
    >
  ) => {
    const next = createCommandDeckTelemetryEntry(partial);
    setCommandDeck((prev) => [...prev, next]);
    setDevicePanelTab("deck");
    return next;
  };

  const addToDeckFromCommandModal = () => {
    if (!commandDevice || !commandAction) {
      notifications.show({
        color: "red",
        title: "Cannot add to deck",
        message: "Select device and action first.",
      });
      return;
    }
    addCommandDeckCommandEntry({
      targetKind: "device",
      targetId: commandDevice,
      action: commandAction,
      label: commandLabel,
      paramsDraft: { ...commandParamValues },
    });
    notifications.show({
      color: "teal",
      title: "Added to command deck",
      message: `${commandDevice}.${commandAction}`,
    });
  };

  const addToDeckFromProcessCommandModal = () => {
    if (!processCommandProcessId || !processCommandAction) {
      notifications.show({
        color: "red",
        title: "Cannot add to deck",
        message: "Select process and action first.",
      });
      return;
    }
    addCommandDeckCommandEntry({
      targetKind: "process",
      targetId: processCommandProcessId,
      action: processCommandAction,
      paramsDraft: { ...processCommandParamValues },
    });
    notifications.show({
      color: "teal",
      title: "Added to command deck",
      message: `${processCommandProcessId}.${processCommandAction}`,
    });
  };

  const setCommandDeckEntryTargetKind = (
    entryId: string,
    targetKind: CommandDeckTargetKind
  ) => {
    const fallbackTargetId =
      targetKind === "process"
        ? (processes[0]?.process_id ?? "")
        : (orderedDevices[0]?.device_id ?? "");
    updateCommandDeckCommandEntry(entryId, {
      targetKind,
      targetId: fallbackTargetId,
      action: "",
      paramsDraft: {},
    });
    if (targetKind === "process" && fallbackTargetId) {
      void ensureProcessCapabilitiesLoaded(fallbackTargetId);
    }
  };

  const runCommandDeckEntry = async (entryId: string) => {
    const entry = commandDeck.find((candidate) => candidate.id === entryId);
    if (!entry || !isCommandDeckCommandEntry(entry)) {
      return;
    }
    const targetId = entry.targetId.trim();
    const action = entry.action.trim();
    if (!targetId || !action) {
      notifications.show({
        color: "red",
        title: "Invalid deck command",
        message: "Target and action are required.",
      });
      return;
    }
    if (commandDeckBusyById[entryId]) {
      return;
    }
    setCommandDeckBusyById((prev) => ({ ...prev, [entryId]: true }));
    try {
      if (entry.targetKind === "process") {
        let capabilities = capabilitiesByProcess[targetId] ?? [];
        if (capabilities.length === 0) {
          capabilities = await ensureProcessCapabilitiesLoaded(targetId);
        }
        const member = (capabilities as Array<{ name: string; params?: Array<{ name: string; required?: boolean }> }>).find(
          (candidate) => candidate.name === action
        );
        const paramsMeta = member?.params ?? [];
        const draft = entry.paramsDraft ?? {};
        const params: Record<string, unknown> = {};
        for (const param of paramsMeta) {
          const raw = (draft[param.name] ?? "").trim();
          if (!raw) {
            if (param.required) {
              notifications.show({
                color: "red",
                title: "Missing parameter",
                message: `${targetId}.${action} requires ${param.name}`,
              });
              return;
            }
            continue;
          }
          params[param.name] = coerceParamValue(raw, param);
        }
        const resp = await sendProcessCommand(
          targetId,
          action,
          params,
          "command-deck"
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Command failed",
            message: formatApiErrorToastMessage(resp.error, {
              targetKind: "process",
              targetId,
              action,
            }),
            autoClose: 15000,
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Command sent",
          message: `${targetId}.${action}`,
        });
        await refreshProcesses();
        if (action.startsWith("hdf.") || hdfWriterProcessId === targetId) {
          await refreshHdfWriterStatus(targetId);
        }
        if (action.startsWith("influx.") || influxWriterProcessId === targetId) {
          await refreshInfluxStatus(targetId);
        }
      } else if (entry.targetKind === "device") {
        let capabilities = capabilitiesByDevice[targetId] ?? [];
        if (capabilities.length === 0) {
          const fetched = await fetchCapabilities(targetId);
          if (fetched.length > 0) {
            setCapabilitiesByDevice((prev) => ({ ...prev, [targetId]: fetched }));
            capabilities = fetched;
          }
        }
        const member = (capabilities as Array<{ name: string }>).find(
          (candidate) => candidate.name === action
        );
        const paramsMeta = effectiveDeviceMemberParams(member);
        const draft = entry.paramsDraft ?? {};
        const params: Record<string, unknown> = {};
        for (const param of paramsMeta) {
          const raw = (draft[param.name] ?? "").trim();
          if (!raw) {
            if (param.required) {
              notifications.show({
                color: "red",
                title: "Missing parameter",
                message: `${targetId}.${action} requires ${param.name}`,
              });
              return;
            }
            continue;
          }
          params[param.name] = coerceParamValue(raw, param);
        }
        const mapped = mapDeviceActionForMember(member, action, params);
        const resp = await sendDeviceCommand(
          targetId,
          mapped.action,
          mapped.params,
          "command-deck"
        );
        if (!resp.ok) {
          notifications.show({
            color: "red",
            title: "Command failed",
            message: formatApiErrorToastMessage(resp.error, {
              targetKind: "device",
              targetId,
              action: mapped.action,
            }),
            autoClose: 15000,
          });
          return;
        }
        notifications.show({
          color: "teal",
          title: "Command sent",
          message: `${targetId}.${mapped.action}`,
        });
      }
    } finally {
      setCommandDeckBusyById((prev) => ({ ...prev, [entryId]: false }));
    }
  };

  return {
    createCommandDeckCommandEntry,
    createCommandDeckTelemetryEntry,
    addCommandDeckCommandEntry,
    addCommandDeckTelemetryEntry,
    addToDeckFromCommandModal,
    addToDeckFromProcessCommandModal,
    setCommandDeckEntryTargetKind,
    runCommandDeckEntry,
  };
}
