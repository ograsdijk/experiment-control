import {
  ActionIcon,
  Autocomplete,
  Badge,
  Button,
  Card,
  Group,
  Menu,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { SortableContext, rectSortingStrategy } from "@dnd-kit/sortable";
import {
  IconChevronDown,
  IconChevronRight,
  IconChevronUp,
  IconDotsVertical,
  IconGripVertical,
  IconPlayerPlay,
  IconPlus,
  IconSettings,
  IconTrash,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { effectiveDeviceMemberParams } from "../features/devices/command_schema";
import { SortableItem } from "../features/layout/SortableItem";
import type {
  CapabilityMember,
  CommandDeckCommandEntry,
  CommandDeckEntry,
  CommandDeckTargetKind,
  CommandDeckTelemetryEntry,
  DeviceStatus,
  ProcessStatus,
  TelemetrySignal,
} from "../types";

function normalizeGroupName(raw: string | null | undefined): string {
  const text = String(raw ?? "").trim();
  return text.length > 0 ? text : "Ungrouped";
}

function commandDeckSortableId(entryId: string): string {
  return `deck:${entryId}`;
}

function isCommandEntry(entry: CommandDeckEntry): entry is CommandDeckCommandEntry {
  return entry.kind !== "telemetry";
}

function isTelemetryEntry(
  entry: CommandDeckEntry
): entry is CommandDeckTelemetryEntry {
  return entry.kind === "telemetry";
}

function formatTelemetryValue(
  signal: TelemetrySignal | undefined,
  opts?: { format?: string | null; decimals?: number | null }
): {
  display: string;
  units: string | null;
  quality: string | null;
} {
  if (!signal || signal.value == null) {
    return { display: "n/a", units: null, quality: null };
  }
  const units =
    typeof signal.units === "string" && signal.units.trim().length > 0
      ? signal.units
      : null;
  const quality =
    typeof signal.quality === "string" && signal.quality.trim().length > 0
      ? signal.quality
      : null;
  const formatRaw = String(opts?.format ?? "auto").trim().toLowerCase();
  const format =
    formatRaw === "fixed" || formatRaw === "scientific" ? formatRaw : "auto";
  const decimals =
    typeof opts?.decimals === "number" && Number.isFinite(opts.decimals)
      ? Math.max(0, Math.min(12, Math.trunc(opts.decimals)))
      : 3;
  const value = signal.value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      return { display: "n/a", units, quality };
    }
    const abs = Math.abs(value);
    const display =
      format === "fixed"
        ? value.toFixed(decimals)
        : format === "scientific"
        ? value.toExponential(decimals)
        : abs > 0 && (abs >= 1e4 || abs < 1e-3)
        ? value.toExponential(3)
        : value.toFixed(3).replace(/\.?0+$/, "");
    return { display, units, quality };
  }
  if (typeof value === "boolean") {
    return { display: value ? "true" : "false", units, quality };
  }
  return { display: String(value), units, quality };
}

type Props = {
  entries: CommandDeckEntry[];
  devices: DeviceStatus[];
  processes: ProcessStatus[];
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal> | undefined>;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  capabilitiesByProcess: Record<string, CapabilityMember[]>;
  busyById: Record<string, boolean>;
  onAddCommandEntry: () => CommandDeckEntry | null;
  onAddTelemetryEntry: () => CommandDeckEntry | null;
  onRunEntry: (entryId: string) => void;
  onRemoveEntry: (entryId: string) => void;
  onMoveEntryUp: (entryId: string) => void;
  onMoveEntryDown: (entryId: string) => void;
  onUpdateCommandEntryTargetKind: (
    entryId: string,
    targetKind: CommandDeckTargetKind
  ) => void;
  onUpdateCommandEntryTarget: (entryId: string, targetId: string) => void;
  onUpdateCommandEntryAction: (entryId: string, action: string) => void;
  onUpdateEntryLabel: (entryId: string, label: string) => void;
  onUpdateEntryGroup: (entryId: string, group: string) => void;
  onUpdateGroupEntries: (fromGroup: string, toGroupRaw: string) => void;
  onUpdateCommandEntryParam: (
    entryId: string,
    paramName: string,
    value: string
  ) => void;
  onUpdateTelemetryEntryDevice: (entryId: string, deviceId: string) => void;
  onUpdateTelemetryEntrySignal: (entryId: string, signal: string) => void;
  onUpdateTelemetryEntryFormat: (
    entryId: string,
    format: "auto" | "fixed" | "scientific"
  ) => void;
  onUpdateTelemetryEntryDecimals: (entryId: string, decimals: number | null) => void;
};

export function CommandDeckPanel({
  entries,
  devices,
  processes,
  latestSignalsByDevice,
  capabilitiesByDevice,
  capabilitiesByProcess,
  busyById,
  onAddCommandEntry,
  onAddTelemetryEntry,
  onRunEntry,
  onRemoveEntry,
  onMoveEntryUp,
  onMoveEntryDown,
  onUpdateCommandEntryTargetKind,
  onUpdateCommandEntryTarget,
  onUpdateCommandEntryAction,
  onUpdateEntryLabel,
  onUpdateEntryGroup,
  onUpdateGroupEntries,
  onUpdateCommandEntryParam,
  onUpdateTelemetryEntryDevice,
  onUpdateTelemetryEntrySignal,
  onUpdateTelemetryEntryFormat,
  onUpdateTelemetryEntryDecimals,
}: Props) {
  const [searchText, setSearchText] = useState("");
  const [filterKind, setFilterKind] = useState<"all" | "command" | "telemetry">(
    "all"
  );
  const [collapsedByGroup, setCollapsedByGroup] = useState<Record<string, boolean>>(
    {}
  );
  const [expandedByEntryId, setExpandedByEntryId] = useState<Record<string, boolean>>(
    {}
  );
  const [groupDraftByEntryId, setGroupDraftByEntryId] = useState<
    Record<string, string>
  >({});
  const [groupRenameOpenByName, setGroupRenameOpenByName] = useState<
    Record<string, boolean>
  >({});
  const [groupRenameDraftByName, setGroupRenameDraftByName] = useState<
    Record<string, string>
  >({});

  const deviceOptions = useMemo(
    () =>
      devices
        .map((device) => ({
          value: device.device_id,
          label: `${device.device_id}${device.is_remote ? " (remote)" : ""}`,
        }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [devices]
  );
  const processOptions = useMemo(
    () =>
      processes
        .map((process) => ({ value: process.process_id, label: process.process_id }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [processes]
  );

  const knownGroupNames = useMemo(() => {
    const values = new Set<string>();
    for (const entry of entries) {
      const group = String(entry.group ?? "").trim();
      if (group) {
        values.add(group);
      }
    }
    return [...values].sort((a, b) => a.localeCompare(b));
  }, [entries]);

  useEffect(() => {
    const knownIds = new Set(entries.map((entry) => entry.id));
    setGroupDraftByEntryId((prev) => {
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
    const knownGroupNames = new Set(
      entries.map((entry) => normalizeGroupName(entry.group))
    );
    setGroupRenameOpenByName((prev) => {
      let changed = false;
      const next: Record<string, boolean> = {};
      for (const [groupName, open] of Object.entries(prev)) {
        if (knownGroupNames.has(groupName)) {
          next[groupName] = open;
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setGroupRenameDraftByName((prev) => {
      let changed = false;
      const next: Record<string, string> = {};
      for (const [groupName, draft] of Object.entries(prev)) {
        if (knownGroupNames.has(groupName)) {
          next[groupName] = draft;
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [entries]);

  const filteredEntries = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    return entries.filter((entry) => {
      if (filterKind === "command" && !isCommandEntry(entry)) {
        return false;
      }
      if (filterKind === "telemetry" && !isTelemetryEntry(entry)) {
        return false;
      }
      if (!needle) {
        return true;
      }
      const group = normalizeGroupName(entry.group).toLowerCase();
      const label = String(entry.label ?? "").toLowerCase();
      if (isTelemetryEntry(entry)) {
        return (
          entry.deviceId.toLowerCase().includes(needle) ||
          entry.signal.toLowerCase().includes(needle) ||
          group.includes(needle) ||
          label.includes(needle)
        );
      }
      return (
        entry.targetId.toLowerCase().includes(needle) ||
        entry.action.toLowerCase().includes(needle) ||
        group.includes(needle) ||
        label.includes(needle)
      );
    });
  }, [entries, filterKind, searchText]);

  const groups = useMemo(() => {
    const byGroup = new Map<string, CommandDeckEntry[]>();
    for (const entry of filteredEntries) {
      const group = normalizeGroupName(entry.group);
      const current = byGroup.get(group) ?? [];
      current.push(entry);
      byGroup.set(group, current);
    }
    return [...byGroup.entries()].sort(([a], [b]) => {
      if (a === "Ungrouped" && b !== "Ungrouped") {
        return -1;
      }
      if (b === "Ungrouped" && a !== "Ungrouped") {
        return 1;
      }
      return a.localeCompare(b);
    });
  }, [filteredEntries]);

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="center">
        <Text fw={600}>Command Deck</Text>
        <Menu shadow="md" width={220} position="bottom-end" withArrow withinPortal>
          <Menu.Target>
            <Button size="compact-xs" variant="light" leftSection={<IconPlus size={14} />}>
              Add
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onAddCommandEntry()}>Add command</Menu.Item>
            <Menu.Item onClick={() => onAddTelemetryEntry()}>Add telemetry</Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
      <Group grow>
        <TextInput
          size="xs"
          placeholder="Search deck..."
          value={searchText}
          onChange={(event) => setSearchText(event.currentTarget.value)}
        />
        <SegmentedControl
          size="xs"
          value={filterKind}
          onChange={(value) =>
            setFilterKind(value === "command" || value === "telemetry" ? value : "all")
          }
          data={[
            { value: "all", label: "All" },
            { value: "command", label: "Commands" },
            { value: "telemetry", label: "Telemetry" },
          ]}
        />
      </Group>
      {groups.length === 0 ? (
        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Text size="sm" c="dimmed">
            No deck entries yet.
          </Text>
        </Card>
      ) : (
        <Stack gap="xs">
          {groups.map(([groupName, groupEntries]) => {
            const collapsed = collapsedByGroup[groupName] === true;
            const groupRenameOpen = groupRenameOpenByName[groupName] === true;
            const groupRenameDraftValue = Object.prototype.hasOwnProperty.call(
              groupRenameDraftByName,
              groupName
            )
              ? groupRenameDraftByName[groupName]
              : groupName === "Ungrouped"
              ? ""
              : groupName;
            const commitGroupRename = () => {
              onUpdateGroupEntries(groupName, groupRenameDraftValue);
              setGroupRenameOpenByName((prev) => ({
                ...prev,
                [groupName]: false,
              }));
            };
            return (
              <Card
                key={groupName}
                className="device-card command-deck-group-card"
                radius="lg"
                p="md"
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
                    <Group gap={6}>
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
                            onClick={() => {
                              setGroupRenameOpenByName((prev) => ({
                                ...prev,
                                [groupName]: !groupRenameOpen,
                              }));
                              setGroupRenameDraftByName((prev) => ({
                                ...prev,
                                [groupName]:
                                  Object.prototype.hasOwnProperty.call(
                                    prev,
                                    groupName
                                  )
                                    ? prev[groupName]
                                    : groupName === "Ungrouped"
                                    ? ""
                                    : groupName,
                              }));
                            }}
                          >
                            {groupRenameOpen ? "Hide rename" : "Rename group"}
                          </Menu.Item>
                        </Menu.Dropdown>
                      </Menu>
                      <Badge size="xs" variant="light" color="gray">
                        {groupEntries.length}
                      </Badge>
                    </Group>
                  </Group>
                  {groupRenameOpen ? (
                    <Group align="end" wrap="nowrap">
                      <TextInput
                        size="xs"
                        flex={1}
                        label={`Move all "${groupName}" entries to`}
                        value={groupRenameDraftValue}
                        onChange={(event) =>
                          setGroupRenameDraftByName((prev) => ({
                            ...prev,
                            [groupName]: event.currentTarget.value,
                          }))
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitGroupRename();
                          } else if (event.key === "Escape") {
                            event.preventDefault();
                            setGroupRenameOpenByName((prev) => ({
                              ...prev,
                              [groupName]: false,
                            }));
                          }
                        }}
                        placeholder="Ungrouped"
                      />
                      <Button
                        size="xs"
                        variant="light"
                        color="gray"
                        onClick={() =>
                          setGroupRenameOpenByName((prev) => ({
                            ...prev,
                            [groupName]: false,
                          }))
                        }
                      >
                        Cancel
                      </Button>
                      <Button size="xs" variant="filled" onClick={commitGroupRename}>
                        Apply
                      </Button>
                    </Group>
                  ) : null}
                  {!collapsed && (
                    <SortableContext
                      items={groupEntries.map((entry) => commandDeckSortableId(entry.id))}
                      strategy={rectSortingStrategy}
                    >
                    {groupEntries.map((entry) => {
                      const optionsOpen = expandedByEntryId[entry.id] === true;
                      const groupDraftValue = Object.prototype.hasOwnProperty.call(
                        groupDraftByEntryId,
                        entry.id
                      )
                        ? groupDraftByEntryId[entry.id]
                        : String(entry.group ?? "");
                      const commitGroupDraft = () => {
                        onUpdateEntryGroup(entry.id, groupDraftValue);
                        setGroupDraftByEntryId((prev) => {
                          if (!Object.prototype.hasOwnProperty.call(prev, entry.id)) {
                            return prev;
                          }
                          const next = { ...prev };
                          delete next[entry.id];
                          return next;
                        });
                      };
                      return (
                        <SortableItem
                          key={entry.id}
                          id={commandDeckSortableId(entry.id)}
                          data={{
                            kind: "command-deck-entry",
                            entryId: entry.id,
                            groupName,
                          }}
                        >
                          {({
                            setNodeRef,
                            attributes,
                            listeners,
                            style: sortableStyle,
                            isDragging,
                          }) => (
                            <Stack
                              ref={setNodeRef}
                              gap={6}
                              style={sortableStyle}
                            >
                          {isCommandEntry(entry) ? (
                            <div
                              className={`pinned-command-chip command-deck-chip${
                                isDragging ? " command-deck-chip-dragging" : ""
                              }`}
                            >
                              <div className="pinned-command-segment pinned-command-name">
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
                                  {String(entry.label ?? "").trim() || entry.action || "Select"}
                                </Button>
                              </div>
                              <div className="pinned-command-segment pinned-command-more">
                                <Menu shadow="md" width={220} position="bottom-end" withArrow withinPortal>
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
                                    <Menu.Item leftSection={<IconChevronUp size={14} />} onClick={() => onMoveEntryUp(entry.id)}>
                                      Move up
                                    </Menu.Item>
                                    <Menu.Item leftSection={<IconChevronDown size={14} />} onClick={() => onMoveEntryDown(entry.id)}>
                                      Move down
                                    </Menu.Item>
                                    <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => onRemoveEntry(entry.id)}>
                                      Remove
                                    </Menu.Item>
                                  </Menu.Dropdown>
                                </Menu>
                                <ActionIcon
                                  size="sm"
                                  variant="subtle"
                                  color="gray"
                                  className="command-deck-drag-handle"
                                  title="Drag to reorder in group"
                                  {...attributes}
                                  {...listeners}
                                >
                                  <IconGripVertical size={14} />
                                </ActionIcon>
                              </div>
                              <div className="pinned-command-segment pinned-command-inputs">
                                {(() => {
                                  const capabilities =
                                    entry.targetKind === "process"
                                      ? capabilitiesByProcess[entry.targetId] ?? []
                                      : capabilitiesByDevice[entry.targetId] ?? [];
                                  const member = capabilities.find(
                                    (candidate) => candidate.name === entry.action
                                  );
                                  const params =
                                    entry.targetKind === "process"
                                      ? (member?.params ?? [])
                                      : effectiveDeviceMemberParams(member);
                                  return params.length > 0 ? (
                                    params.map((param) => (
                                      <TextInput
                                        key={`${entry.id}:${param.name}`}
                                        size="xs"
                                        w={120}
                                        value={(entry.paramsDraft ?? {})[param.name] ?? ""}
                                        onChange={(event) =>
                                          onUpdateCommandEntryParam(
                                            entry.id,
                                            param.name,
                                            event.currentTarget.value
                                          )
                                        }
                                        onKeyDown={(event) => {
                                          if (event.key === "Enter" && !event.shiftKey) {
                                            event.preventDefault();
                                            if (!busyById[entry.id]) {
                                              onRunEntry(entry.id);
                                            }
                                          }
                                        }}
                                        placeholder={param.required ? `${param.name} *` : param.name}
                                      />
                                    ))
                                  ) : null;
                                })()}
                              </div>
                              <div className="pinned-command-segment pinned-command-send">
                                <ActionIcon
                                  size="sm"
                                  variant="light"
                                  color="teal"
                                  loading={Boolean(busyById[entry.id])}
                                  onClick={() => onRunEntry(entry.id)}
                                >
                                  <IconPlayerPlay size={14} />
                                </ActionIcon>
                              </div>
                            </div>
                          ) : (
                            <div
                              className={`command-deck-telemetry-row command-deck-chip${
                                isDragging ? " command-deck-chip-dragging" : ""
                              }`}
                            >
                              {(() => {
                                const value = formatTelemetryValue(
                                  latestSignalsByDevice[entry.deviceId]?.[entry.signal],
                                  {
                                    format: entry.format,
                                    decimals: entry.decimals,
                                  }
                                );
                                const label = String(entry.label ?? "").trim();
                                return (
                                  <>
                                    <div className="command-deck-telemetry-left">
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
                                        {label || entry.signal}
                                      </Button>
                                      {label ? (
                                        <Text size="xs" c="dimmed">
                                          {entry.signal}
                                        </Text>
                                      ) : null}
                                    </div>
                                    <div className="command-deck-telemetry-value">
                                      <Text size="sm" fw={500}>
                                        {value.display}
                                      </Text>
                                      {value.units ? (
                                        <Text size="xs" c="dimmed">
                                          {value.units}
                                        </Text>
                                      ) : null}
                                      {value.quality ? (
                                        <Badge size="xs" variant="light" color="gray">
                                          {value.quality}
                                        </Badge>
                                      ) : null}
                                    </div>
                                  </>
                                );
                              })()}
                              <div className="command-deck-telemetry-actions">
                                <Menu shadow="md" width={220} position="bottom-end" withArrow withinPortal>
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
                                    <Menu.Item leftSection={<IconChevronUp size={14} />} onClick={() => onMoveEntryUp(entry.id)}>
                                      Move up
                                    </Menu.Item>
                                    <Menu.Item leftSection={<IconChevronDown size={14} />} onClick={() => onMoveEntryDown(entry.id)}>
                                      Move down
                                    </Menu.Item>
                                    <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => onRemoveEntry(entry.id)}>
                                      Remove
                                    </Menu.Item>
                                  </Menu.Dropdown>
                                </Menu>
                                <ActionIcon
                                  size="sm"
                                  variant="subtle"
                                  color="gray"
                                  className="command-deck-drag-handle"
                                  title="Drag to reorder in group"
                                  {...attributes}
                                  {...listeners}
                                >
                                  <IconGripVertical size={14} />
                                </ActionIcon>
                              </div>
                            </div>
                          )}
                          {optionsOpen ? (
                            <Card
                              radius="sm"
                              p="xs"
                              className="command-deck-options-card"
                              style={{ border: "1px solid var(--card-border)" }}
                            >
                              <Stack gap={6}>
                                {isCommandEntry(entry) ? (
                                  <Group grow>
                                    <Select
                                      size="xs"
                                      label="Target kind"
                                      data={[
                                        { value: "device", label: "Device" },
                                        { value: "process", label: "Process" },
                                      ]}
                                      value={entry.targetKind === "process" ? "process" : "device"}
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (value === "device" || value === "process") {
                                          onUpdateCommandEntryTargetKind(entry.id, value);
                                        }
                                      }}
                                    />
                                    <Select
                                      size="xs"
                                      label={entry.targetKind === "process" ? "Process" : "Device"}
                                      data={entry.targetKind === "process" ? processOptions : deviceOptions}
                                      value={entry.targetId || null}
                                      searchable
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateCommandEntryTarget(entry.id, value);
                                        }
                                      }}
                                    />
                                    <Select
                                      size="xs"
                                      label="Action"
                                      data={(
                                        entry.targetKind === "process"
                                          ? capabilitiesByProcess[entry.targetId] ?? []
                                          : capabilitiesByDevice[entry.targetId] ?? []
                                      ).map((member) => ({ value: member.name, label: member.name }))}
                                      value={entry.action || null}
                                      searchable
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateCommandEntryAction(entry.id, value);
                                        }
                                      }}
                                    />
                                  </Group>
                                ) : (
                                  <Group grow>
                                    <Select
                                      size="xs"
                                      label="Device"
                                      data={deviceOptions}
                                      value={entry.deviceId || null}
                                      searchable
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateTelemetryEntryDevice(entry.id, value);
                                        }
                                      }}
                                    />
                                    <Select
                                      size="xs"
                                      label="Signal"
                                      data={Object.keys(
                                        latestSignalsByDevice[entry.deviceId] ?? {}
                                      ).map((signal) => ({ value: signal, label: signal }))}
                                      value={entry.signal || null}
                                      searchable
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (typeof value === "string") {
                                          onUpdateTelemetryEntrySignal(entry.id, value);
                                        }
                                      }}
                                    />
                                  </Group>
                                )}
                                {isTelemetryEntry(entry) ? (
                                  <Group grow>
                                    <Select
                                      size="xs"
                                      label="Notation"
                                      data={[
                                        { value: "auto", label: "Auto" },
                                        { value: "fixed", label: "Fixed" },
                                        { value: "scientific", label: "Scientific" },
                                      ]}
                                      value={
                                        entry.format === "fixed" ||
                                        entry.format === "scientific"
                                          ? entry.format
                                          : "auto"
                                      }
                                      allowDeselect={false}
                                      onChange={(value) => {
                                        if (
                                          value === "auto" ||
                                          value === "fixed" ||
                                          value === "scientific"
                                        ) {
                                          onUpdateTelemetryEntryFormat(entry.id, value);
                                        }
                                      }}
                                    />
                                    <NumberInput
                                      size="xs"
                                      label="Decimals"
                                      value={
                                        typeof entry.decimals === "number"
                                          ? entry.decimals
                                          : 3
                                      }
                                      min={0}
                                      max={12}
                                      step={1}
                                      clampBehavior="strict"
                                      disabled={
                                        (entry.format ?? "auto") === "auto"
                                      }
                                      onChange={(value) => {
                                        const next =
                                          typeof value === "number" && Number.isFinite(value)
                                            ? Math.max(0, Math.min(12, Math.trunc(value)))
                                            : null;
                                        onUpdateTelemetryEntryDecimals(entry.id, next);
                                      }}
                                    />
                                  </Group>
                                ) : null}
                                <Group grow>
                                  <TextInput
                                    size="xs"
                                    label="Label"
                                    value={entry.label ?? ""}
                                    onChange={(event) =>
                                      onUpdateEntryLabel(entry.id, event.currentTarget.value)
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
                                      }
                                    }}
                                    placeholder="Ungrouped"
                                  />
                                </Group>
                              </Stack>
                            </Card>
                          ) : null}
                            </Stack>
                          )}
                        </SortableItem>
                      );
                    })}
                    </SortableContext>
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
