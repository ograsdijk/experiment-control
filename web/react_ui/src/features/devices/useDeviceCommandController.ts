import { notifications } from "@mantine/notifications";
import { useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { fetchCapabilities, type ApiResponse } from "../../api";
import { coerceParamValue } from "../../components/ParamInput";
import type { PinnedCommandMap, PinnedParamDrafts } from "../profile/types";
import type { CapabilityMember } from "../../types";
import { pinnedCommandKey } from "../runtime/helpers";
import { formatApiErrorToastMessage } from "../common/api_error";
import {
  buildParamDefaults,
  effectiveDeviceMemberParams,
  mapDeviceActionForMember,
} from "./command_schema";

type UseDeviceCommandControllerArgs = {
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  setCapabilitiesByDevice: Dispatch<
    SetStateAction<Record<string, CapabilityMember[]>>
  >;
  invalidateDeviceCapabilities: (deviceId: string) => void;
  pinnedCommands: PinnedCommandMap;
  setPinnedCommands: Dispatch<SetStateAction<PinnedCommandMap>>;
  pinnedParamDrafts: PinnedParamDrafts;
  setPinnedParamDrafts: Dispatch<SetStateAction<PinnedParamDrafts>>;
  pinnedBusyByKey: Record<string, boolean>;
  setPinnedBusyByKey: Dispatch<SetStateAction<Record<string, boolean>>>;
  sendDeviceCommand: (
    deviceId: string,
    action: string,
    params: Record<string, unknown>,
    source: string
  ) => Promise<ApiResponse<unknown>>;
};

export function useDeviceCommandController({
  capabilitiesByDevice,
  setCapabilitiesByDevice,
  invalidateDeviceCapabilities: _invalidateDeviceCapabilities,
  pinnedCommands,
  setPinnedCommands,
  pinnedParamDrafts,
  setPinnedParamDrafts,
  pinnedBusyByKey,
  setPinnedBusyByKey,
  sendDeviceCommand,
}: UseDeviceCommandControllerArgs) {
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandDevice, setCommandDevice] = useState<string | null>(null);
  const [commandAction, setCommandAction] = useState("");
  const [commandParams, setCommandParams] = useState("{}");
  const [commandLabel, setCommandLabel] = useState("");
  const [commandParamValues, setCommandParamValues] = useState<
    Record<string, string>
  >({});
  const [showAdvancedParams, setShowAdvancedParams] = useState(false);

  const capabilitiesForActive = commandDevice
    ? capabilitiesByDevice[commandDevice] ?? []
    : [];
  const activeMember = useMemo(
    () => capabilitiesForActive.find((member) => member.name === commandAction),
    [capabilitiesForActive, commandAction]
  );
  const activeParams = useMemo(
    () => effectiveDeviceMemberParams(activeMember),
    [activeMember, effectiveDeviceMemberParams]
  );
  const pinnedEntry =
    commandDevice && commandAction
      ? (pinnedCommands[commandDevice] ?? []).find(
          (entry) => entry.action === commandAction
        )
      : undefined;
  const isPinned = Boolean(pinnedEntry);

  const togglePinnedCommand = (deviceId: string, action: string, label?: string) => {
    if (!deviceId || !action) {
      return;
    }
    setPinnedCommands((prev) => {
      const existing = prev[deviceId] ?? [];
      const current = new Map(existing.map((entry) => [entry.action, entry]));
      if (current.has(action)) {
        current.delete(action);
      } else {
        const cleanLabel = label?.trim();
        current.set(action, {
          action,
          label: cleanLabel ? cleanLabel : undefined,
        });
      }
      return {
        ...prev,
        [deviceId]: Array.from(current.values()).sort((a, b) =>
          a.action.localeCompare(b.action)
        ),
      };
    });
  };

  const setPinnedCommandLabel = (deviceId: string, action: string, label: string) => {
    const cleanLabel = label.trim();
    setPinnedCommands((prev) => {
      const existing = prev[deviceId] ?? [];
      const next = existing.map((entry) =>
        entry.action === action
          ? {
              ...entry,
              label: cleanLabel.length > 0 ? cleanLabel : undefined,
            }
          : entry
      );
      return {
        ...prev,
        [deviceId]: next,
      };
    });
  };

  const openCommand = async (deviceId: string, action?: string) => {
    setCommandDevice(deviceId);
    setCommandAction(action ?? "");
    setCommandParams("{}");
    setCommandParamValues({});
    setShowAdvancedParams(false);
    if (action) {
      const pinned = (pinnedCommands[deviceId] ?? []).find(
        (entry) => entry.action === action
      );
      setCommandLabel(pinned?.label ?? "");
    } else {
      setCommandLabel("");
    }
    setCommandOpen(true);
    let caps = capabilitiesByDevice[deviceId] ?? [];
    if (caps.length === 0) {
      const fetched = await fetchCapabilities(deviceId);
      caps = fetched;
      if (fetched.length > 0) {
        setCapabilitiesByDevice((prev) => ({ ...prev, [deviceId]: fetched }));
      }
    }
    if (action) {
      const member = caps.find((item) => item.name === action);
      setCommandParamValues(buildParamDefaults(member));
    }
  };

  const handleActionChange = (value: string | null) => {
    const nextAction = value ?? "";
    setCommandAction(nextAction);
    if (commandDevice && nextAction) {
      const nextPinned = (pinnedCommands[commandDevice] ?? []).find(
        (entry) => entry.action === nextAction
      );
      setCommandLabel(nextPinned?.label ?? "");
    } else {
      setCommandLabel("");
    }
    const nextMember = capabilitiesForActive.find(
      (member) => member.name === nextAction
    );
    setCommandParamValues(buildParamDefaults(nextMember));
  };

  const handleLabelChange = (value: string) => {
    setCommandLabel(value);
    if (commandDevice && commandAction && isPinned) {
      setPinnedCommandLabel(commandDevice, commandAction, value);
    }
  };

  const handlePinClick = () => {
    if (!commandDevice || !commandAction) {
      notifications.show({
        color: "red",
        title: "Select a command first",
        message: "Choose an action before pinning.",
      });
      return;
    }
    const nextPinned = !isPinned;
    togglePinnedCommand(commandDevice, commandAction, commandLabel);
    notifications.show({
      color: "teal",
      title: nextPinned ? "Pinned command" : "Unpinned command",
      message: `${commandDevice}.${commandAction}`,
    });
  };

  const executeCommand = async () => {
    if (!commandDevice || !commandAction) {
      notifications.show({
        color: "red",
        title: "Missing action",
        message: "Select a command before executing.",
      });
      return;
    }
    let params: Record<string, unknown> = {};
    if (showAdvancedParams) {
      try {
        params = commandParams.trim() ? JSON.parse(commandParams) : {};
      } catch {
        notifications.show({
          color: "red",
          title: "Invalid params",
          message: "Params must be valid JSON.",
        });
        return;
      }
    } else if (activeParams.length > 0) {
      for (const param of activeParams) {
        const raw = (commandParamValues[param.name] ?? "").trim();
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
    const mapped = mapDeviceActionForMember(activeMember, commandAction, params);
    const resp = await sendDeviceCommand(
      commandDevice,
      mapped.action,
      mapped.params,
      "device-command-modal"
    );
    if (resp.ok) {
      notifications.show({
        color: "teal",
        title: "Command sent",
        message: `${commandDevice}.${mapped.action}`,
      });
    } else {
      notifications.show({
        color: "red",
        title: "Command failed",
        message: formatApiErrorToastMessage(resp.error, {
          targetKind: "device",
          targetId: commandDevice,
          action: mapped.action,
        }),
        autoClose: 15000,
      });
    }
  };

  const handlePinnedParamChange = (
    deviceId: string,
    action: string,
    paramName: string,
    value: string
  ) => {
    const key = pinnedCommandKey(deviceId, action);
    setPinnedParamDrafts((prev) => {
      const current = prev[key] ?? {};
      if (current[paramName] === value) {
        return prev;
      }
      return {
        ...prev,
        [key]: {
          ...current,
          [paramName]: value,
        },
      };
    });
  };

  const handlePinnedCommandSend = async (deviceId: string, action: string) => {
    const key = pinnedCommandKey(deviceId, action);
    if (pinnedBusyByKey[key]) {
      return;
    }
    setPinnedBusyByKey((prev) => ({ ...prev, [key]: true }));
    try {
      let capabilities = capabilitiesByDevice[deviceId] ?? [];
      if (capabilities.length === 0) {
        const fetched = await fetchCapabilities(deviceId);
        if (fetched.length > 0) {
          setCapabilitiesByDevice((prev) => ({ ...prev, [deviceId]: fetched }));
          capabilities = fetched;
        }
      }
      const member = capabilities.find((item) => item.name === action);
      const paramsMeta = effectiveDeviceMemberParams(member);
      const draft = pinnedParamDrafts[key] ?? {};
      const params: Record<string, unknown> = {};
      for (const param of paramsMeta) {
        const raw = (draft[param.name] ?? "").trim();
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
      const mapped = mapDeviceActionForMember(member, action, params);
      const resp = await sendDeviceCommand(
        deviceId,
        mapped.action,
        mapped.params,
        "pinned-command"
      );
      if (resp.ok) {
        notifications.show({
          color: "teal",
          title: "Command sent",
          message: `${deviceId}.${mapped.action}`,
        });
      } else {
        notifications.show({
          color: "red",
          title: "Command failed",
          message: formatApiErrorToastMessage(resp.error, {
            targetKind: "device",
            targetId: deviceId,
            action: mapped.action,
          }),
          autoClose: 15000,
        });
      }
    } finally {
      setPinnedBusyByKey((prev) => ({ ...prev, [key]: false }));
    }
  };

  return {
    commandOpen,
    setCommandOpen,
    commandDevice,
    commandAction,
    commandParams,
    setCommandParams,
    commandLabel,
    commandParamValues,
    setCommandParamValues,
    showAdvancedParams,
    setShowAdvancedParams,
    capabilitiesForActive,
    activeMember,
    activeParams,
    isPinned,
    openCommand,
    handleActionChange,
    handleLabelChange,
    handlePinClick,
    executeCommand,
    handlePinnedParamChange,
    handlePinnedCommandSend,
  };
}
