import { notifications } from "@mantine/notifications";
import { useMemo, useState } from "react";
import { coerceParamValue } from "../../components/ParamInput";
import type { ApiResponse } from "../../api";
import type { CapabilityMember } from "../../types";
import { formatApiErrorToastMessage } from "../common/api_error";
import { buildParamDefaults } from "../devices/command_schema";

type UseProcessCommandControllerArgs = {
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  sendProcessCommand: (
    processId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<ApiResponse<unknown>>;
  refreshProcesses: () => Promise<unknown>;
  refreshHdfWriterStatus: (processId: string) => Promise<unknown>;
  hdfWriterProcessId: string | null;
};

export function useProcessCommandController({
  capabilitiesByProcess,
  sendProcessCommand,
  refreshProcesses,
  refreshHdfWriterStatus,
  hdfWriterProcessId,
}: UseProcessCommandControllerArgs) {
  const [processCommandOpen, setProcessCommandOpen] = useState(false);
  const [processCommandProcessId, setProcessCommandProcessId] = useState<
    string | null
  >(null);
  const [processCommandAction, setProcessCommandAction] = useState("");
  const [processCommandParams, setProcessCommandParams] = useState("{}");
  const [processCommandParamValues, setProcessCommandParamValues] = useState<
    Record<string, string>
  >({});
  const [processShowAdvancedParams, setProcessShowAdvancedParams] =
    useState(false);

  const capabilitiesForProcessCommand = processCommandProcessId
    ? capabilitiesByProcess[processCommandProcessId] ?? []
    : [];
  const activeProcessMember = capabilitiesForProcessCommand.find(
    (member) => member.name === processCommandAction
  );
  const activeProcessParams = activeProcessMember?.params ?? [];

  const openProcessCommand = (processId: string, action?: string) => {
    const nextAction = action ?? "";
    setProcessCommandProcessId(processId);
    setProcessCommandAction(nextAction);
    setProcessCommandParams("{}");
    setProcessShowAdvancedParams(false);
    const member = (capabilitiesByProcess[processId] ?? []).find(
      (capability) => capability.name === nextAction
    );
    setProcessCommandParamValues(buildParamDefaults(member));
    setProcessCommandOpen(true);
  };

  const handleProcessCommandActionChange = (value: string | null) => {
    const nextAction = value ?? "";
    setProcessCommandAction(nextAction);
    if (!processCommandProcessId) {
      setProcessCommandParamValues({});
      return;
    }
    const member = (capabilitiesByProcess[processCommandProcessId] ?? []).find(
      (capability) => capability.name === nextAction
    );
    setProcessCommandParamValues(buildParamDefaults(member));
  };

  const executeProcessCommand = async () => {
    if (!processCommandProcessId || !processCommandAction) {
      notifications.show({
        color: "red",
        title: "Missing process command",
        message: "Select a process command before executing.",
      });
      return;
    }
    let params: Record<string, unknown> = {};
    if (processShowAdvancedParams) {
      try {
        params = processCommandParams.trim()
          ? JSON.parse(processCommandParams)
          : {};
      } catch {
        notifications.show({
          color: "red",
          title: "Invalid params",
          message: "Params must be valid JSON.",
        });
        return;
      }
    } else if (activeProcessParams.length > 0) {
      for (const param of activeProcessParams) {
        const raw = (processCommandParamValues[param.name] ?? "").trim();
        if (!raw) {
          if (param.required) {
            notifications.show({
              color: "red",
              title: "Missing parameter",
              message: `Parameter ${param.name} is required.`,
            });
            return;
          }
          continue;
        }
        params[param.name] = coerceParamValue(raw, param);
      }
    }
    const resp = await sendProcessCommand(
      processCommandProcessId,
      processCommandAction,
      params,
      "process-command-modal"
    );
    if (resp.ok) {
      notifications.show({
        color: "teal",
        title: "Process command sent",
        message: `${processCommandProcessId}.${processCommandAction}`,
      });
      await refreshProcesses();
      if (
        processCommandAction.startsWith("hdf.") ||
        hdfWriterProcessId === processCommandProcessId
      ) {
        await refreshHdfWriterStatus(processCommandProcessId);
      }
      return;
    }
    notifications.show({
      color: "red",
      title: "Process command failed",
      message: formatApiErrorToastMessage(resp.error, {
        targetKind: "process",
        targetId: processCommandProcessId,
        action: processCommandAction,
      }),
    });
  };

  const modalTitle = useMemo(
    () => `Process Command ${processCommandProcessId ?? ""}`,
    [processCommandProcessId]
  );

  return {
    processCommandOpen,
    setProcessCommandOpen,
    processCommandProcessId,
    processCommandAction,
    setProcessCommandAction,
    processCommandParams,
    setProcessCommandParams,
    processCommandParamValues,
    setProcessCommandParamValues,
    processShowAdvancedParams,
    setProcessShowAdvancedParams,
    capabilitiesForProcessCommand,
    activeProcessMember,
    activeProcessParams,
    openProcessCommand,
    handleProcessCommandActionChange,
    executeProcessCommand,
    processCommandTitle: modalTitle,
  };
}
