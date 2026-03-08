import {
  ActionIcon,
  Autocomplete,
  Badge,
  Button,
  Card,
  Group,
  Menu,
  Select,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import {
  IconChevronDown,
  IconChevronRight,
  IconChevronUp,
  IconDotsVertical,
  IconGripVertical,
  IconPlayerPlay,
  IconSettings,
  IconTrash,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState, type DragEvent } from "react";
import { effectiveDeviceMemberParams } from "../features/devices/command_schema";
import { computeVerticalReorderMode } from "../features/layout/reorder";
import type {
  CapabilityMember,
  CommandDeckEntry,
  CommandDeckTargetKind,
  DeviceStatus,
  ProcessStatus,
} from "../types";
import { DeviceNameInline } from "./DeviceNameInline";

function normalizeGroupName(raw: string | null | undefined): string {
  const text = String(raw ?? "").trim();
  return text.length > 0 ? text : "Ungrouped";
}

type Props = {
  entries: CommandDeckEntry[];
  devices: DeviceStatus[];
  processes: ProcessStatus[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  busyById: Record<string, boolean>;
  onAddEntry: () => void;
  onRunEntry: (entryId: string) => void;
  onRemoveEntry: (entryId: string) => void;
  onMoveEntryUp: (entryId: string) => void;
  onMoveEntryDown: (entryId: string) => void;
  onReorderEntry: (
    entryId: string,
    targetEntryId: string,
    mode: "before" | "after" | "swap"
  ) => void;
  onUpdateEntryTargetKind: (entryId: string, targetKind: CommandDeckTargetKind) => void;
  onUpdateEntryTarget: (entryId: string, targetId: string) => void;
  onUpdateEntryAction: (entryId: string, action: string) => void;
  onUpdateEntryLabel: (entryId: string, label: string) => void;
  onUpdateEntryGroup: (entryId: string, group: string) => void;
  onUpdateEntryParam: (entryId: string, paramName: string, value: string) => void;
};

export function CommandDeckPanel({
  entries,
  devices,
  processes,
  capabilitiesByDevice,
  capabilitiesByProcess,
  busyById,
  onAddEntry,
  onRunEntry,
  onRemoveEntry,
  onMoveEntryUp,
  onMoveEntryDown,
  onReorderEntry,
  onUpdateEntryTargetKind,
  onUpdateEntryTarget,
  onUpdateEntryAction,
  onUpdateEntryLabel,
  onUpdateEntryGroup,
  onUpdateEntryParam,
}: Props) {
  const [searchText, setSearchText] = useState("");
  const [collapsedByGroup, setCollapsedByGroup] = useState<Record<string, boolean>>({});
  const [expandedByEntryId, setExpandedByEntryId] = useState<Record<string, boolean>>({});
  const [groupDraftByEntryId, setGroupDraftByEntryId] = useState<
    Record<string, string>
  >({});
  const [dragEntryId, setDragEntryId] = useState<string | null>(null);
  const [dragOverEntryTarget, setDragOverEntryTarget] = useState<{
    entryId: string;
    mode: "before" | "after" | "swap";
  } | null>(null);
  const deviceById = useMemo(
    () => new Map(devices.map((device) => [device.device_id, device])),
    [devices]
  );
  const processById = useMemo(
    () => new Map(processes.map((process) => [process.process_id, process])),
    [processes]
  );
  const deviceOptions = useMemo(() => {
    const items = devices.map((device) => {
      const suffix = device.is_remote ? " (remote)" : "";
      return {
        value: device.device_id,
        label: `${device.device_id}${suffix}`,
      };
    });
    items.sort((a, b) => a.label.localeCompare(b.label));
    return items;
  }, [devices]);
  const processOptions = useMemo(() => {
    const items = processes.map((process) => ({
      value: process.process_id,
      label: process.process_id,
    }));
    items.sort((a, b) => a.label.localeCompare(b.label));
    return items;
  }, [processes]);
  const knownGroupNames = useMemo(() => {
    const values = new Set<string>();
    for (const entry of entries) {
      const group = String(entry.group ?? "").trim();
      if (group.length > 0) {
        values.add(group);
      }
    }
    return [...values].sort((a, b) => a.localeCompare(b));
  }, [entries]);

  useEffect(() => {
    setGroupDraftByEntryId((prev) => {
      const knownIds = new Set(entries.map((entry) => entry.id));
      let changed = false;
      const next: Record<string, string> = {};
      for (const [entryId, draft] of Object.entries(prev)) {
        if (knownIds.has(entryId)) {
          next[entryId] = draft;
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setExpandedByEntryId((prev) => {
      const knownIds = new Set(entries.map((entry) => entry.id));
      let changed = false;
      const next: Record<string, boolean> = {};
      for (const [entryId, expanded] of Object.entries(prev)) {
        if (knownIds.has(entryId)) {
          next[entryId] = expanded;
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [entries]);

  const handleEntryDragStart = (
    entryId: string,
    event: DragEvent<HTMLElement>
  ) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-command-deck-entry", entryId);
    setDragEntryId(entryId);
    setDragOverEntryTarget(null);
  };

  const handleEntryDragEnd = () => {
    setDragEntryId(null);
    setDragOverEntryTarget(null);
  };

  const handleEntryDragOver = (
    targetEntryId: string,
    event: DragEvent<HTMLElement>
  ) => {
    if (!dragEntryId || dragEntryId === targetEntryId) {
      if (dragOverEntryTarget?.entryId === targetEntryId) {
        setDragOverEntryTarget(null);
      }
      return;
    }
    const mode = computeVerticalReorderMode(event) as "before" | "after" | "swap";
    event.preventDefault();
    setDragOverEntryTarget((prev) =>
      prev && prev.entryId === targetEntryId && prev.mode === mode
        ? prev
        : { entryId: targetEntryId, mode }
    );
  };

  const handleEntryDrop = (
    targetEntryId: string,
    event: DragEvent<HTMLElement>
  ) => {
    if (!dragEntryId || dragEntryId === targetEntryId) {
      return;
    }
    const mode = computeVerticalReorderMode(event) as "before" | "after" | "swap";
    event.preventDefault();
    onReorderEntry(dragEntryId, targetEntryId, mode);
    setDragEntryId(null);
    setDragOverEntryTarget(null);
  };

  const filteredEntries = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    if (!needle) {
      return entries;
    }
    return entries.filter((entry) => {
      const groupName = normalizeGroupName(entry.group);
      const label = String(entry.label ?? "").trim();
      return (
        entry.targetId.toLowerCase().includes(needle) ||
        entry.action.toLowerCase().includes(needle) ||
        groupName.toLowerCase().includes(needle) ||
        label.toLowerCase().includes(needle)
      );
    });
  }, [entries, searchText]);

  const groups = useMemo(() => {
    const byGroup = new Map<string, CommandDeckEntry[]>();
    for (const entry of filteredEntries) {
      const groupName = normalizeGroupName(entry.group);
      const current = byGroup.get(groupName) ?? [];
      current.push(entry);
      byGroup.set(groupName, current);
    }
    const ordered = [...byGroup.entries()];
    ordered.sort(([a], [b]) => {
      if (a === "Ungrouped" && b !== "Ungrouped") {
        return -1;
      }
      if (b === "Ungrouped" && a !== "Ungrouped") {
        return 1;
      }
      return a.localeCompare(b);
    });
    return ordered;
  }, [filteredEntries]);

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="center">
        <Text fw={600}>Command Deck</Text>
        <Button size="compact-xs" variant="light" onClick={onAddEntry}>
          Add
        </Button>
      </Group>
      <TextInput
        size="xs"
        placeholder="Search command deck..."
        value={searchText}
        onChange={(event) => setSearchText(event.currentTarget.value)}
      />
      {groups.length === 0 ? (
        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Text size="sm" c="dimmed">
            No command deck entries yet.
          </Text>
        </Card>
      ) : (
        <Stack gap="xs">
          {groups.map(([groupName, groupEntries]) => {
            const collapsed = collapsedByGroup[groupName] === true;
            return (
              <Card
                key={groupName}
                radius="md"
                p="sm"
                style={{ border: "1px solid var(--card-border)" }}
              >
                <Stack gap="xs">
                  <Group justify="space-between" align="center">
                    <Group gap="xs">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        color="gray"
                        onClick={() =>
                          setCollapsedByGroup((prev) => ({
                            ...prev,
                            [groupName]: !collapsed,
                          }))
                        }
                        aria-label={collapsed ? "Expand group" : "Collapse group"}
                      >
                        {collapsed ? (
                          <IconChevronRight size={14} />
                        ) : (
                          <IconChevronDown size={14} />
                        )}
                      </ActionIcon>
                      <Text size="sm" fw={600}>
                        {groupName}
                      </Text>
                    </Group>
                    <Badge size="xs" variant="light" color="gray">
                      {groupEntries.length}
                    </Badge>
                  </Group>
                  {!collapsed && (
                    <Stack gap="xs">
                      {groupEntries.map((entry) => {
                        const targetKind: CommandDeckTargetKind =
                          entry.targetKind === "process" ? "process" : "device";
                        const capabilities =
                          targetKind === "process"
                            ? capabilitiesByProcess[entry.targetId] ?? []
                            : capabilitiesByDevice[entry.targetId] ?? [];
                        const optionsOpen = expandedByEntryId[entry.id] === true;
                        const actionOptions = capabilities
                          .map((candidate) => ({
                            value: candidate.name,
                            label: candidate.name,
                          }))
                          .sort((a, b) => a.label.localeCompare(b.label));
                        if (
                          entry.action &&
                          !actionOptions.some((option) => option.value === entry.action)
                        ) {
                          actionOptions.unshift({
                            value: entry.action,
                            label: `${entry.action} (current)`,
                          });
                        }
                        const entryTargetOptions =
                          targetKind === "process"
                            ? [...processOptions]
                            : [...deviceOptions];
                        if (
                          entry.targetId &&
                          !entryTargetOptions.some(
                            (option) => option.value === entry.targetId
                          )
                        ) {
                          entryTargetOptions.unshift({
                            value: entry.targetId,
                            label: `${entry.targetId} (current)`,
                          });
                        }
                        const groupDraftValue = Object.prototype.hasOwnProperty.call(
                          groupDraftByEntryId,
                          entry.id
                        )
                          ? groupDraftByEntryId[entry.id]
                          : String(entry.group ?? "");
                        const commitGroupDraft = () => {
                          const raw = Object.prototype.hasOwnProperty.call(
                            groupDraftByEntryId,
                            entry.id
                          )
                            ? groupDraftByEntryId[entry.id]
                            : String(entry.group ?? "");
                          onUpdateEntryGroup(entry.id, raw);
                          setGroupDraftByEntryId((prev) => {
                            if (!Object.prototype.hasOwnProperty.call(prev, entry.id)) {
                              return prev;
                            }
                            const next = { ...prev };
                            delete next[entry.id];
                            return next;
                          });
                        };
                        const member = capabilities.find(
                          (candidate) => candidate.name === entry.action
                        );
                        const params =
                          targetKind === "process"
                            ? (member?.params ?? [])
                            : effectiveDeviceMemberParams(member);
                        const draft = entry.paramsDraft ?? {};
                        const buttonText = String(entry.label ?? "").trim() || entry.action;
                        const buttonTooltip =
                          entry.targetId && entry.action
                            ? `${entry.targetId}.${entry.action}`
                            : entry.targetId || entry.action || null;
                        const dragClass =
                          dragEntryId === entry.id
                            ? " command-deck-chip-dragging"
                            : dragOverEntryTarget?.entryId === entry.id
                            ? dragOverEntryTarget.mode === "before"
                              ? " command-deck-chip-drop-before"
                              : dragOverEntryTarget.mode === "after"
                              ? " command-deck-chip-drop-after"
                              : " command-deck-chip-drop-swap"
                            : "";
                        const commandNameButton = (
                          <Button
                            size="xs"
                            variant="subtle"
                            color="gray"
                            className="pinned-command-name-button"
                            onClick={() =>
                              setExpandedByEntryId((prev) => ({
                                ...prev,
                                [entry.id]: !optionsOpen,
                              }))
                            }
                          >
                            {buttonText || "Select command"}
                          </Button>
                        );
                        return (
                          <Stack
                            key={entry.id}
                            gap={6}
                            onDragOver={(event) => handleEntryDragOver(entry.id, event)}
                            onDragLeave={() => {
                              if (dragOverEntryTarget?.entryId === entry.id) {
                                setDragOverEntryTarget(null);
                              }
                            }}
                            onDrop={(event) => handleEntryDrop(entry.id, event)}
                          >
                            <div
                              className={`pinned-command-chip command-deck-chip${dragClass}`}
                            >
                              <div className="pinned-command-segment pinned-command-name">
                                {buttonTooltip ? (
                                  <Tooltip label={buttonTooltip} withArrow>
                                    {commandNameButton}
                                  </Tooltip>
                                ) : (
                                  commandNameButton
                                )}
                              </div>
                              <div className="pinned-command-segment pinned-command-more">
                                <Menu
                                  shadow="md"
                                  width={220}
                                  position="bottom-end"
                                  withArrow
                                  withinPortal
                                >
                                  <Menu.Target>
                                    <ActionIcon size="sm" variant="subtle" color="gray">
                                      <IconDotsVertical size={14} />
                                    </ActionIcon>
                                  </Menu.Target>
                                  <Menu.Dropdown>
                                    <Menu.Item
                                      leftSection={<IconSettings size={14} />}
                                      onClick={() =>
                                        setExpandedByEntryId((prev) => ({
                                          ...prev,
                                          [entry.id]: !optionsOpen,
                                        }))
                                      }
                                    >
                                      {optionsOpen ? "Hide options" : "Edit options"}
                                    </Menu.Item>
                                    <Menu.Item
                                      leftSection={<IconChevronUp size={14} />}
                                      onClick={() => onMoveEntryUp(entry.id)}
                                    >
                                      Move up
                                    </Menu.Item>
                                    <Menu.Item
                                      leftSection={<IconChevronDown size={14} />}
                                      onClick={() => onMoveEntryDown(entry.id)}
                                    >
                                      Move down
                                    </Menu.Item>
                                    <Menu.Item
                                      color="red"
                                      leftSection={<IconTrash size={14} />}
                                      onClick={() => onRemoveEntry(entry.id)}
                                    >
                                      Remove
                                    </Menu.Item>
                                  </Menu.Dropdown>
                                </Menu>
                                <ActionIcon
                                  size="sm"
                                  variant="subtle"
                                  color="gray"
                                  className="command-deck-drag-handle"
                                  draggable
                                  onDragStart={(event) =>
                                    handleEntryDragStart(entry.id, event)
                                  }
                                  onDragEnd={handleEntryDragEnd}
                                  title="Drag to reorder in group"
                                  aria-label="Drag to reorder command"
                                >
                                  <IconGripVertical size={14} />
                                </ActionIcon>
                              </div>
                              <div className="pinned-command-segment pinned-command-inputs">
                                {params.length > 0 ? (
                                  params.map((param) => (
                                    <TextInput
                                      key={`${entry.id}:${param.name}`}
                                      size="xs"
                                      w={120}
                                      value={draft[param.name] ?? ""}
                                      onChange={(event) =>
                                        onUpdateEntryParam(
                                          entry.id,
                                          param.name,
                                          event.currentTarget.value
                                        )
                                      }
                                      placeholder={
                                        param.required
                                          ? `${param.name} *`
                                          : param.name
                                      }
                                    />
                                  ))
                                ) : (
                                  <Text size="xs" c="dimmed" px={4}>
                                    No parameters
                                  </Text>
                                )}
                              </div>
                              <div className="pinned-command-segment pinned-command-send">
                                <ActionIcon
                                  size="sm"
                                  variant="light"
                                  color="teal"
                                  loading={Boolean(busyById[entry.id])}
                                  onClick={() => onRunEntry(entry.id)}
                                  aria-label="Run command"
                                >
                                  <IconPlayerPlay size={14} />
                                </ActionIcon>
                              </div>
                            </div>
                            {optionsOpen ? (
                              <Card
                                radius="sm"
                                p="xs"
                                className="command-deck-options-card"
                                style={{ border: "1px solid var(--card-border)" }}
                              >
                                <Stack gap={6}>
                                  <Text size="xs" c="dimmed">
                                    {targetKind === "process" ? (
                                      <>
                                        process:
                                        {processById.get(entry.targetId)?.process_id ??
                                          entry.targetId}{" "}
                                        .{entry.action || "(select action)"}
                                      </>
                                    ) : (
                                      <>
                                        <DeviceNameInline
                                          deviceId={entry.targetId}
                                          device={deviceById.get(entry.targetId) ?? null}
                                        />{" "}
                                        .{entry.action || "(select action)"}
                                      </>
                                    )}
                                  </Text>
                                  <Group grow>
                                    <Select
                                      size="xs"
                                      label="Target kind"
                                      data={[
                                        { value: "device", label: "Device" },
                                        { value: "process", label: "Process" },
                                      ]}
                                      value={targetKind}
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (value === "device" || value === "process") {
                                          onUpdateEntryTargetKind(entry.id, value);
                                        }
                                      }}
                                    />
                                  </Group>
                                  <Group grow>
                                    <Select
                                      size="xs"
                                      label={targetKind === "process" ? "Process" : "Device"}
                                      data={entryTargetOptions}
                                      value={entry.targetId || null}
                                      searchable
                                      nothingFoundMessage={
                                        targetKind === "process"
                                          ? "No processes"
                                          : "No devices"
                                      }
                                      placeholder={
                                        targetKind === "process"
                                          ? "Select process"
                                          : "Select device"
                                      }
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateEntryTarget(entry.id, value);
                                        }
                                      }}
                                      allowDeselect={false}
                                    />
                                    <Select
                                      size="xs"
                                      label="Action"
                                      data={actionOptions}
                                      value={entry.action || null}
                                      searchable
                                      nothingFoundMessage={
                                        entry.targetId
                                          ? "No command capabilities"
                                          : targetKind === "process"
                                          ? "Select a process first"
                                          : "Select a device first"
                                      }
                                      placeholder={
                                        entry.targetId
                                          ? "Select command"
                                          : targetKind === "process"
                                          ? "Select a process first"
                                          : "Select a device first"
                                      }
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateEntryAction(entry.id, value);
                                        }
                                      }}
                                      allowDeselect={false}
                                      disabled={!entry.targetId}
                                    />
                                  </Group>
                                  <Group grow>
                                    <TextInput
                                      size="xs"
                                      label="Label"
                                      value={entry.label ?? ""}
                                      onChange={(event) =>
                                        onUpdateEntryLabel(
                                          entry.id,
                                          event.currentTarget.value
                                        )
                                      }
                                      placeholder="Optional label"
                                    />
                                    <Autocomplete
                                      size="xs"
                                      label="Group"
                                      value={groupDraftValue}
                                      data={knownGroupNames}
                                      onChange={(value) =>
                                        setGroupDraftByEntryId((prev) => ({
                                          ...prev,
                                          [entry.id]: value,
                                        }))
                                      }
                                      onBlur={commitGroupDraft}
                                      onKeyDown={(event) => {
                                        if (event.key === "Enter") {
                                          event.preventDefault();
                                          commitGroupDraft();
                                          return;
                                        }
                                        if (event.key === "Escape") {
                                          event.preventDefault();
                                          setGroupDraftByEntryId((prev) => {
                                            if (
                                              !Object.prototype.hasOwnProperty.call(
                                                prev,
                                                entry.id
                                              )
                                            ) {
                                              return prev;
                                            }
                                            const next = { ...prev };
                                            delete next[entry.id];
                                            return next;
                                          });
                                        }
                                      }}
                                      placeholder="Ungrouped"
                                    />
                                  </Group>
                                </Stack>
                              </Card>
                            ) : null}
                          </Stack>
                        );
                      })}
                    </Stack>
                  )}
                </Stack>
              </Card>
            );
          })}
        </Stack>
      )}
    </Stack>
  );
}
