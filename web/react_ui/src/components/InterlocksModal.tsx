import { Badge, Box, Group, Modal, ScrollArea, Stack, Table, Text } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { interlockRuleKey } from "../features/runtime/helpers";
import type {
  CommandInterceptorRoute,
  DeviceStatus,
  FollowerRuleStatus,
  InterlockInterceptorStatus,
  ProcessStatus,
} from "../types";
import { useResourceListView } from "../features/devices/useDeviceListView";
import {
  buildInterlockRuleRows,
  interlockRuleAdapter,
  interlockRuleHealth,
  type InterlockRuleRow,
} from "../features/safety/safetyRows";
import { DeviceNameInline } from "./DeviceNameInline";
import { HealthMark } from "./HealthMark";
import { RefreshIconButton } from "./RefreshIconButton";
import { ResourceListToolbar } from "./ResourceListToolbar";
import { CommandRoutingSection } from "./safety/CommandRoutingSection";
import { ConfirmDisableSwitch } from "./safety/ConfirmDisableSwitch";
import { InterlockRuleDetail } from "./safety/InterlockRuleDetail";
import { SafetyProcessStrip } from "./safety/SafetyProcessStrip";
import { useSortableTable, type SortValue } from "./safety/useSortableTable";

// Kept for callers that imported the condition view from here.
export {
  InterlockConditionView,
  type ConditionTelemetryBinding,
} from "./safety/InterlockConditionView";

type Props = {
  opened: boolean;
  onClose: () => void;
  panelOnly?: boolean;
  onRefresh: () => Promise<unknown> | void;
  devices: ReadonlyArray<DeviceStatus>;
  processes: ReadonlyArray<ProcessStatus>;
  followerRulesByProcessId: Record<string, FollowerRuleStatus[]>;
  interlockStatusByProcessId: Record<string, InterlockInterceptorStatus[]>;
  interlocksLoadingByProcessId: Record<string, boolean>;
  interlocksErrorByProcessId: Record<string, string>;
  interlockRuleBusyByKey: Record<string, boolean>;
  commandInterceptorRoutes: CommandInterceptorRoute[];
  commandInterceptorRoutesLoading: boolean;
  commandInterceptorRoutesError: string | null;
  onRefreshProcess: (processId: string) => Promise<unknown> | void;
  onToggleFollowerRule: (
    processId: string,
    ruleId: string,
    enabled: boolean
  ) => Promise<unknown> | void;
  onToggleInterlockRule: (
    processId: string,
    interceptorId: string,
    ruleId: string,
    enabled: boolean
  ) => Promise<unknown> | void;
  /** Select this rule on mount/change (e.g. from a blocked watchdog action). */
  focusRule?: InterlockFocus | null;
};

export type InterlockFocus = {
  processId: string;
  interceptorId: string;
  /** Rule name or rule_id, as reported by an interceptor rejection. */
  rule: string;
  /** Bump to re-focus the same rule. */
  nonce: number;
};

type Column = "rule" | "ruleset" | "match" | "inputs" | "kind" | "enabled";

const COLUMNS: { key: Column; label: string; detail?: boolean }[] = [
  { key: "rule", label: "Rule" },
  // Hidden while a rule is selected: the detail panel shows them instead.
  { key: "ruleset", label: "Ruleset", detail: true },
  { key: "match", label: "Match" },
  { key: "inputs", label: "Inputs", detail: true },
  { key: "kind", label: "Kind", detail: true },
];

function sortValue(row: InterlockRuleRow, column: Column): SortValue {
  switch (column) {
    case "rule":
      return row.name.toLowerCase();
    case "ruleset":
      return `${row.processId}/${row.rulesetLabel}`.toLowerCase();
    case "match":
      return `${row.matchDeviceId}.${row.matchAction}`.toLowerCase();
    case "inputs":
      return row.inputCount;
    case "kind":
      return row.kindLabel;
    case "enabled":
      return row.enabled ? 0 : 1;
  }
}

export function InterlocksModal({
  opened,
  onClose,
  panelOnly = false,
  onRefresh,
  devices,
  processes,
  followerRulesByProcessId,
  interlockStatusByProcessId,
  interlocksLoadingByProcessId,
  interlocksErrorByProcessId,
  interlockRuleBusyByKey,
  commandInterceptorRoutes,
  commandInterceptorRoutesLoading,
  commandInterceptorRoutesError,
  onRefreshProcess,
  onToggleFollowerRule,
  onToggleInterlockRule,
  focusRule = null,
}: Props) {
  const deviceById = useMemo(
    () => new Map(devices.map((device) => [device.device_id, device])),
    [devices]
  );
  const processById = useMemo(
    () => new Map(processes.map((process) => [process.process_id, process])),
    [processes]
  );
  const rows = useMemo(
    () => buildInterlockRuleRows(processes, interlockStatusByProcessId, followerRulesByProcessId),
    [processes, interlockStatusByProcessId, followerRulesByProcessId]
  );
  const view = useResourceListView(rows, interlockRuleAdapter, {
    persistKey: "ecui.safety.interlocks",
  });
  const { sorted, header } = useSortableTable(view.visible, sortValue);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selectedRow = rows.find((row) => row.key === selectedKey) ?? null;
  const compact = selectedRow !== null;
  const showProcessName = processes.length > 1;

  // A rule picked from the routing section may be hidden by the filter.
  useEffect(() => {
    if (selectedKey && !rows.some((row) => row.key === selectedKey)) {
      setSelectedKey(null);
    }
  }, [rows, selectedKey]);

  useEffect(() => {
    if (!focusRule) {
      return;
    }
    const target = rows.find(
      (row) =>
        (!focusRule.processId || row.processId === focusRule.processId) &&
        (!focusRule.interceptorId || row.scopeId === focusRule.interceptorId) &&
        (row.name === focusRule.rule || row.ruleId === focusRule.rule)
    );
    if (target) {
      view.setQuery("");
      view.setIssuesOnly(false);
      setSelectedKey(target.key);
    }
    // Re-run only when a new focus request arrives, not on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRule?.nonce, rows.length]);

  const toggleRule = (row: InterlockRuleRow, enabled: boolean) => {
    if (row.follower) {
      void onToggleFollowerRule(row.processId, row.ruleId, enabled);
    } else {
      void onToggleInterlockRule(row.processId, row.scopeId, row.ruleId, enabled);
    }
  };
  const ruleBusy = (row: InterlockRuleRow) =>
    Boolean(interlockRuleBusyByKey[interlockRuleKey(row.processId, row.scopeId, row.ruleId)]) ||
    Boolean(interlocksLoadingByProcessId[row.processId]);

  const selectFromRouting = (key: string) => {
    if (!view.visible.some((row) => row.key === key)) {
      view.setQuery("");
      view.setIssuesOnly(false);
    }
    setSelectedKey(key);
  };

  const panel = (
    <Stack gap="sm">
      {processes.length === 0 ? (
        <Text size="sm" c="dimmed">
          No interlock-capable processes are available.
        </Text>
      ) : (
        <Stack gap={6}>
          {processes.map((process) => {
            const processRows = rows.filter((row) => row.processId === process.process_id);
            const enabledCount = processRows.filter((row) => row.enabled).length;
            return (
              <SafetyProcessStrip
                key={process.process_id}
                process={process}
                loading={Boolean(interlocksLoadingByProcessId[process.process_id])}
                error={interlocksErrorByProcessId[process.process_id]}
                onRefresh={() => {
                  void onRefreshProcess(process.process_id);
                }}
              >
                <Text size="xs" c="dimmed">
                  {enabledCount}/{processRows.length} rules enabled
                </Text>
              </SafetyProcessStrip>
            );
          })}
        </Stack>
      )}

      <Group gap="sm" align="flex-start" wrap="nowrap">
        <Box style={{ flex: 1, maxWidth: 640 }}>
          <ResourceListToolbar
            view={view}
            noun="rules"
            healthyLabel="active"
            healthyTitle="Enabled and enforced by a running process"
            customSortLabel="Default order"
          />
        </Box>
        <RefreshIconButton
          label="Refresh interlocks"
          onClick={() => {
            void onRefresh();
          }}
        />
      </Group>

      <Group gap="md" align="flex-start" wrap="nowrap">
        <ScrollArea.Autosize
          style={{ flex: 1, minWidth: 0 }}
          mah="min(60vh, 40rem)"
          type="auto"
          offsetScrollbars
        >
          {sorted.length === 0 ? (
            <Text size="sm" c="dimmed" ta="center" py="lg">
              {rows.length === 0
                ? "No interlock rule data available yet."
                : "No rules match the current filter."}
            </Text>
          ) : (
            <Table
              className="resource-overview-table"
              highlightOnHover
              stickyHeader
              verticalSpacing={4}
              fz="xs"
            >
              <Table.Thead>
                <Table.Tr>
                  {COLUMNS.filter((c) => !compact || !c.detail).map((c) => header(c.key, c.label))}
                  {header("enabled", "Enabled")}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sorted.map((row) => (
                  <Table.Tr
                    key={row.key}
                    className={`resource-overview-row${
                      row.key === selectedKey ? " resource-overview-row-selected" : ""
                    }`}
                    onClick={() =>
                      setSelectedKey((prev) => (prev === row.key ? null : row.key))
                    }
                  >
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <HealthMark health={interlockRuleHealth(row)} />
                        <Text size="xs" fw={600}>
                          {row.name}
                        </Text>
                      </Group>
                    </Table.Td>
                    {compact ? null : (
                      <Table.Td>
                        <Text size="xs" c="dimmed">
                          {showProcessName ? `${row.processId} / ` : ""}
                          {row.rulesetLabel}
                          {row.rulesetEnabled ? "" : " (off)"}
                        </Text>
                      </Table.Td>
                    )}
                    <Table.Td>
                      <DeviceNameInline
                        deviceId={row.matchDeviceId}
                        device={deviceById.get(row.matchDeviceId) ?? null}
                        size="xs"
                        suffix={`.${row.matchAction}`}
                      />
                    </Table.Td>
                    {compact ? null : (
                      <>
                        <Table.Td>{row.inputCount || "—"}</Table.Td>
                        <Table.Td>
                          <Badge size="xs" variant="outline" color="gray">
                            {row.kindLabel}
                          </Badge>
                        </Table.Td>
                      </>
                    )}
                    <Table.Td>
                      <ConfirmDisableSwitch
                        size="xs"
                        checked={row.enabled}
                        disabled={ruleBusy(row) || !row.ruleId}
                        subject={`rule "${row.name}"`}
                        onChange={(enabled) => toggleRule(row, enabled)}
                      />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </ScrollArea.Autosize>
        {selectedRow ? (
          <Box className="resource-overview-detail">
            <ScrollArea.Autosize mah="min(60vh, 40rem)" type="auto">
              <InterlockRuleDetail
                row={selectedRow}
                rows={rows}
                deviceById={deviceById}
                busy={ruleBusy(selectedRow)}
                onToggle={toggleRule}
                onClose={() => setSelectedKey(null)}
                onSelectRule={selectFromRouting}
              />
            </ScrollArea.Autosize>
          </Box>
        ) : null}
      </Group>

      <CommandRoutingSection
        routes={commandInterceptorRoutes}
        loading={commandInterceptorRoutesLoading}
        error={commandInterceptorRoutesError}
        rows={rows}
        processById={processById}
        deviceById={deviceById}
        onSelectRule={selectFromRouting}
      />
    </Stack>
  );

  if (panelOnly) {
    return panel;
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Interlocks"
      size="clamp(56rem, 92vw, 96rem)"
      centered
    >
      {panel}
    </Modal>
  );
}
