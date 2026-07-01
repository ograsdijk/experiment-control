import {
  Badge,
  Button,
  Card,
  Group,
  Modal,
  MultiSelect,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { processStateColor } from "../features/runtime/helpers";
import type {
  HdfWriterStatus,
  MeasurementFieldSchema,
  MeasurementProfileSchema,
  MeasurementSchema,
} from "../features/hdf/types";

type SelectOption = { value: string; label: string };

type Props = {
  opened: boolean;
  onClose: () => void;
  title: string;
  hdfWriterState: string;
  hdfWriterProcessId: string | null;
  hdfWriterStatus: HdfWriterStatus | null;
  hdfWriterLoading: boolean;
  hdfStatusBusy: boolean;
  hdfCommandsBlocked: boolean;
  hdfSupportsStatus: boolean;
  hdfSupportsWritingStart: boolean;
  hdfSupportsWritingStop: boolean;
  hdfAnyCommandBusy: boolean;
  onRefreshStatus: () => Promise<unknown> | void;
  onExecuteWritingStart: () => Promise<unknown> | void;
  onExecuteWritingStop: () => Promise<unknown> | void;
  hdfProcessCapabilitiesError: string | null;
  hdfMeasurementSchemaConfigured: boolean;
  hdfMeasurementSchemaAvailable: boolean;
  hdfSelectableDeviceIds: string[];
  hdfMeasurementSchemaDisplayPath: string | null;
  hdfMeasurementSchemaDisplayError: string | null;
  hdfSupportsMeasurementSchemaGet: boolean;
  hdfMeasurementSchemaLoading: boolean;
  onRefreshSchema: () => Promise<unknown> | void;
  hdfRotateFilenameDraft: string;
  onRotateFilenameChange: (value: string) => void;
  hdfRotateDisabledDevicesDraft: string[];
  onRotateDisabledDevicesChange: (value: string[]) => void;
  hdfSelectableDeviceOptions: SelectOption[];
  hdfShowMeasurementUi: boolean;
  hdfRotateMeasurementProfileDraft: string | null;
  hdfRotateProfileOptions: SelectOption[];
  onSelectRotateMeasurementProfile: (value: string | null) => void;
  hdfRotateSelectedProfile: MeasurementProfileSchema | null;
  renderMeasurementFieldInput: (
    field: MeasurementFieldSchema,
    value: string,
    useCustom: boolean,
    onValueChange: (value: string) => void,
    onUseCustomChange: (value: boolean) => void
  ) => ReactNode;
  hdfRotateMeasurementValuesDraft: Record<string, string>;
  hdfRotateMeasurementCustomByField: Record<string, boolean>;
  onSetRotateFieldValue: (fieldKey: string, value: string) => void;
  onSetRotateFieldUseCustom: (fieldKey: string, useCustom: boolean) => void;
  hdfRotateBusy: boolean;
  hdfWritingStartBusy: boolean;
  hdfWritingStopBusy: boolean;
  hdfSupportsRotate: boolean;
  onExecuteRotate: () => Promise<unknown> | void;
  hdfSupportsMeasurementNote: boolean;
  hdfMeasurementSchema: MeasurementSchema | null;
  hdfNoteValuesDraft: Record<string, string>;
  hdfNoteCustomByField: Record<string, boolean>;
  onSetNoteFieldValue: (fieldKey: string, value: string) => void;
  onSetNoteFieldUseCustom: (fieldKey: string, useCustom: boolean) => void;
  hdfMeasurementNoteBusy: boolean;
  onExecuteMeasurementNote: () => Promise<unknown> | void;
  hdfDevicesGetBusy: boolean;
  hdfSupportsDevicesGet: boolean;
  onExecuteDevicesGet: () => Promise<unknown> | void;
  hdfEnableDevicesDraft: string[];
  onEnableDevicesDraftChange: (value: string[]) => void;
  hdfDevicesEnableBusy: boolean;
  hdfSupportsDevicesEnable: boolean;
  onExecuteDevicesEnable: () => Promise<unknown> | void;
  hdfDisableDevicesDraft: string[];
  onDisableDevicesDraftChange: (value: string[]) => void;
  hdfDevicesDisableBusy: boolean;
  hdfSupportsDevicesDisable: boolean;
  onExecuteDevicesDisable: () => Promise<unknown> | void;
  hdfSelectableProcessOptions: SelectOption[];
  hdfProcessesGetBusy: boolean;
  hdfSupportsProcessesGet: boolean;
  onExecuteProcessesGet: () => Promise<unknown> | void;
  hdfEnableProcessesDraft: string[];
  onEnableProcessesDraftChange: (value: string[]) => void;
  hdfProcessesEnableBusy: boolean;
  hdfSupportsProcessesEnable: boolean;
  onExecuteProcessesEnable: () => Promise<unknown> | void;
  hdfDisableProcessesDraft: string[];
  onDisableProcessesDraftChange: (value: string[]) => void;
  hdfProcessesDisableBusy: boolean;
  hdfSupportsProcessesDisable: boolean;
  onExecuteProcessesDisable: () => Promise<unknown> | void;
};

export function HdfWriterModal({
  opened,
  onClose,
  title,
  hdfWriterState,
  hdfWriterProcessId,
  hdfWriterStatus,
  hdfWriterLoading,
  hdfStatusBusy,
  hdfCommandsBlocked,
  hdfSupportsStatus,
  hdfSupportsWritingStart,
  hdfSupportsWritingStop,
  hdfAnyCommandBusy,
  onRefreshStatus,
  onExecuteWritingStart,
  onExecuteWritingStop,
  hdfProcessCapabilitiesError,
  hdfMeasurementSchemaConfigured,
  hdfMeasurementSchemaAvailable,
  hdfSelectableDeviceIds,
  hdfMeasurementSchemaDisplayPath,
  hdfMeasurementSchemaDisplayError,
  hdfSupportsMeasurementSchemaGet,
  hdfMeasurementSchemaLoading,
  onRefreshSchema,
  hdfRotateFilenameDraft,
  onRotateFilenameChange,
  hdfRotateDisabledDevicesDraft,
  onRotateDisabledDevicesChange,
  hdfSelectableDeviceOptions,
  hdfShowMeasurementUi,
  hdfRotateMeasurementProfileDraft,
  hdfRotateProfileOptions,
  onSelectRotateMeasurementProfile,
  hdfRotateSelectedProfile,
  renderMeasurementFieldInput,
  hdfRotateMeasurementValuesDraft,
  hdfRotateMeasurementCustomByField,
  onSetRotateFieldValue,
  onSetRotateFieldUseCustom,
  hdfRotateBusy,
  hdfWritingStartBusy,
  hdfWritingStopBusy,
  hdfSupportsRotate,
  onExecuteRotate,
  hdfSupportsMeasurementNote,
  hdfMeasurementSchema,
  hdfNoteValuesDraft,
  hdfNoteCustomByField,
  onSetNoteFieldValue,
  onSetNoteFieldUseCustom,
  hdfMeasurementNoteBusy,
  onExecuteMeasurementNote,
  hdfDevicesGetBusy,
  hdfSupportsDevicesGet,
  onExecuteDevicesGet,
  hdfEnableDevicesDraft,
  onEnableDevicesDraftChange,
  hdfDevicesEnableBusy,
  hdfSupportsDevicesEnable,
  onExecuteDevicesEnable,
  hdfDisableDevicesDraft,
  onDisableDevicesDraftChange,
  hdfDevicesDisableBusy,
  hdfSupportsDevicesDisable,
  onExecuteDevicesDisable,
  hdfSelectableProcessOptions,
  hdfProcessesGetBusy,
  hdfSupportsProcessesGet,
  onExecuteProcessesGet,
  hdfEnableProcessesDraft,
  onEnableProcessesDraftChange,
  hdfProcessesEnableBusy,
  hdfSupportsProcessesEnable,
  onExecuteProcessesEnable,
  hdfDisableProcessesDraft,
  onDisableProcessesDraftChange,
  hdfProcessesDisableBusy,
  hdfSupportsProcessesDisable,
  onExecuteProcessesDisable,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="clamp(56rem, 92vw, 96rem)" centered zIndex={450}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <Stack gap={2}>
            <Group gap="xs">
              <Badge variant="light" color={processStateColor(hdfWriterState)}>
                {hdfWriterState}
              </Badge>
              <Text size="xs" c="dimmed">
                {hdfWriterProcessId ?? "No HDF writer process"}
              </Text>
            </Group>
            <Text
              size="xs"
              c="dimmed"
              style={{ wordBreak: "break-all", whiteSpace: "normal" }}
            >
              {hdfWriterStatus?.filePath ?? "No active file"}
            </Text>
          </Stack>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              color="teal"
              loading={hdfWritingStartBusy}
              disabled={
                hdfCommandsBlocked ||
                !hdfSupportsWritingStart ||
                hdfAnyCommandBusy ||
                hdfWriterStatus?.writingActive === true
              }
              onClick={() => {
                void onExecuteWritingStart();
              }}
            >
              Start writing
            </Button>
            <Button
              size="xs"
              variant="light"
              color="orange"
              loading={hdfWritingStopBusy}
              disabled={
                hdfCommandsBlocked ||
                !hdfSupportsWritingStop ||
                hdfAnyCommandBusy ||
                hdfWriterStatus?.writingActive !== true
              }
              onClick={() => {
                void onExecuteWritingStop();
              }}
            >
              Stop writing
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              loading={hdfWriterLoading || hdfStatusBusy}
              disabled={hdfCommandsBlocked || !hdfSupportsStatus || hdfAnyCommandBusy}
              onClick={() => {
                void onRefreshStatus();
              }}
            >
              Refresh status
            </Button>
          </Group>
        </Group>

        {hdfProcessCapabilitiesError && (
          <Text size="sm" c="red">
            {hdfProcessCapabilitiesError}
          </Text>
        )}
        {hdfWriterStatus?.error && (
          <Text size="sm" c="red">
            {hdfWriterStatus.error}
          </Text>
        )}

        <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
          <Stack gap={8}>
            <Group gap="xs" wrap="wrap">
              <Badge variant="light" color="gray">
                pending {hdfWriterStatus?.pending ?? "n/a"}
              </Badge>
              <Badge
                variant="light"
                color={hdfWriterStatus?.writingActive ? "teal" : "orange"}
              >
                writing {hdfWriterStatus?.writingActive ? "active" : "stopped"}
              </Badge>
              <Badge variant="light" color="gray">
                dropped {hdfWriterStatus?.dropped ?? "n/a"}
              </Badge>
              <Badge variant="light" color="gray">
                dropped events {hdfWriterStatus?.droppedEvents ?? "n/a"}
              </Badge>
              <Badge variant="light" color="gray">
                enabled known {hdfWriterStatus?.enabledKnownDevices.length ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                disabled {hdfWriterStatus?.disabledDevices.length ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                known {hdfWriterStatus?.knownDevices.length ?? 0}
              </Badge>
              <Badge variant="light" color="gray">
                notes {hdfWriterStatus?.measurementNotesRows ?? 0}
              </Badge>
              {hdfWriterStatus?.measurementType && (
                <Badge variant="light" color="indigo">
                  measurement {hdfWriterStatus.measurementType}
                </Badge>
              )}
              {hdfMeasurementSchemaConfigured && (
                <Badge
                  variant="light"
                  color={hdfMeasurementSchemaAvailable ? "teal" : "red"}
                >
                  schema {hdfMeasurementSchemaAvailable ? "ready" : "error"}
                </Badge>
              )}
            </Group>
            <Text size="xs" c="dimmed">
              enabled:{" "}
              {hdfWriterStatus?.enabledKnownDevices.length
                ? hdfWriterStatus.enabledKnownDevices.join(", ")
                : "none"}
            </Text>
            <Text size="xs" c="dimmed">
              disabled:{" "}
              {hdfWriterStatus?.disabledDevices.length
                ? hdfWriterStatus.disabledDevices.join(", ")
                : "none"}
            </Text>
            {hdfSelectableDeviceIds.length === 0 && (
              <Text size="xs" c="yellow">
                No device IDs discovered yet. Keep telemetry running and refresh
                status.
              </Text>
            )}
            {hdfSelectableDeviceIds.length > 0 && (
              <Text size="xs" c="dimmed">
                selectable device IDs: {hdfSelectableDeviceIds.join(", ")}
              </Text>
            )}
            {hdfWriterStatus?.measurementId && (
              <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
                measurement_id: {hdfWriterStatus.measurementId}
              </Text>
            )}
            {hdfMeasurementSchemaDisplayPath && (
              <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
                measurement schema: {hdfMeasurementSchemaDisplayPath}
              </Text>
            )}
            {hdfMeasurementSchemaDisplayError && (
              <Text size="xs" c="red">
                measurement schema error: {hdfMeasurementSchemaDisplayError}
              </Text>
            )}
          </Stack>
        </Card>

        <Stack gap="xs">
          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Stack gap="xs">
              <Group justify="space-between" align="flex-end" wrap="wrap">
                <Stack gap={2} style={{ flex: "0 0 220px" }}>
                  <Text fw={600} size="sm">
                    hdf.rotate
                  </Text>
                  <Text size="xs" c="dimmed">
                    Rotate to a new file. Optional filename and disabled device filter.
                  </Text>
                </Stack>
                {hdfSupportsMeasurementSchemaGet && hdfMeasurementSchemaConfigured && (
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconRefresh size={14} />}
                    loading={hdfMeasurementSchemaLoading}
                    disabled={hdfCommandsBlocked || hdfAnyCommandBusy}
                    onClick={() => {
                      void onRefreshSchema();
                    }}
                  >
                    Refresh schema
                  </Button>
                )}
              </Group>
              <Group gap="xs" align="flex-end" wrap="wrap">
                <TextInput
                  size="xs"
                  label="filename"
                  placeholder="optional (e.g. run_002.h5)"
                  value={hdfRotateFilenameDraft}
                  onChange={(event) => onRotateFilenameChange(event.currentTarget.value)}
                  style={{ flex: "1 1 220px" }}
                />
                <MultiSelect
                  size="xs"
                  label="disabled_devices"
                  placeholder="optional"
                  value={hdfRotateDisabledDevicesDraft}
                  data={hdfSelectableDeviceOptions}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  nothingFoundMessage="No devices discovered"
                  onChange={onRotateDisabledDevicesChange}
                  style={{ flex: "1 1 220px" }}
                />
              </Group>
              {hdfMeasurementSchemaConfigured && !hdfMeasurementSchemaAvailable && (
                <Text size="xs" c="red">
                  Measurement schema is configured but unavailable.
                </Text>
              )}
              {hdfShowMeasurementUi && (
                <Stack gap="xs">
                  <Select
                    size="xs"
                    label="measurement_profile *"
                    value={hdfRotateMeasurementProfileDraft}
                    data={hdfRotateProfileOptions}
                    searchable
                    comboboxProps={{ zIndex: 500 }}
                    onChange={onSelectRotateMeasurementProfile}
                    placeholder="Select profile"
                  />
                  {hdfRotateSelectedProfile?.description && (
                    <Text size="xs" c="dimmed">
                      {hdfRotateSelectedProfile.description}
                    </Text>
                  )}
                  {hdfRotateSelectedProfile?.fields.map((field) => (
                    <Stack key={`rotate-field-${field.key}`} gap={2}>
                      {renderMeasurementFieldInput(
                        field,
                        hdfRotateMeasurementValuesDraft[field.key] ?? "",
                        hdfRotateMeasurementCustomByField[field.key] === true,
                        (next) => {
                          onSetRotateFieldValue(field.key, next);
                        },
                        (next) => {
                          onSetRotateFieldUseCustom(field.key, next);
                        }
                      )}
                      {field.description && (
                        <Text size="xs" c="dimmed">
                          {field.description}
                        </Text>
                      )}
                    </Stack>
                  ))}
                </Stack>
              )}
              <Group justify="flex-end">
                <Button
                  size="xs"
                  loading={hdfRotateBusy}
                  disabled={hdfCommandsBlocked || !hdfSupportsRotate}
                  onClick={() => {
                    void onExecuteRotate();
                  }}
                >
                  Rotate
                </Button>
              </Group>
            </Stack>
          </Card>

          {hdfShowMeasurementUi && hdfSupportsMeasurementNote && (
            <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
              <Stack gap="xs">
                <Text fw={600} size="sm">
                  hdf.measurement.note
                </Text>
                <Text size="xs" c="dimmed">
                  Append timestamped rows to `/measurement/notes`.
                </Text>
                {hdfMeasurementSchema?.notes.fields.map((field) => (
                  <Stack key={`note-field-${field.key}`} gap={2}>
                    {renderMeasurementFieldInput(
                      field,
                      hdfNoteValuesDraft[field.key] ?? "",
                      hdfNoteCustomByField[field.key] === true,
                      (next) => {
                        onSetNoteFieldValue(field.key, next);
                      },
                      (next) => {
                        onSetNoteFieldUseCustom(field.key, next);
                      }
                    )}
                    {field.description && (
                      <Text size="xs" c="dimmed">
                        {field.description}
                      </Text>
                    )}
                  </Stack>
                ))}
                <Group justify="flex-end">
                  <Button
                    size="xs"
                    loading={hdfMeasurementNoteBusy}
                    disabled={hdfCommandsBlocked || !hdfSupportsMeasurementNote}
                    onClick={() => {
                      void onExecuteMeasurementNote();
                    }}
                  >
                    Add note
                  </Button>
                </Group>
              </Stack>
            </Card>
          )}

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="center" wrap="wrap">
              <Stack gap={2}>
                <Text fw={600} size="sm">
                  hdf.status
                </Text>
                <Text size="xs" c="dimmed">
                  Query current writer file and queue counters.
                </Text>
              </Stack>
              <Button
                size="xs"
                loading={hdfStatusBusy}
                disabled={hdfCommandsBlocked || !hdfSupportsStatus}
                onClick={() => {
                  void onRefreshStatus();
                }}
              >
                Run
              </Button>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="center" wrap="wrap">
              <Stack gap={2}>
                <Text fw={600} size="sm">
                  hdf.devices.get
                </Text>
                <Text size="xs" c="dimmed">
                  Query known, enabled, and disabled writer devices.
                </Text>
              </Stack>
              <Button
                size="xs"
                loading={hdfDevicesGetBusy}
                disabled={hdfCommandsBlocked || !hdfSupportsDevicesGet}
                onClick={() => {
                  void onExecuteDevicesGet();
                }}
              >
                Run
              </Button>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="flex-end" wrap="wrap">
              <Stack gap={2} style={{ flex: "0 0 220px" }}>
                <Text fw={600} size="sm">
                  hdf.devices.enable
                </Text>
                <Text size="xs" c="dimmed">
                  Enable writing for selected device IDs.
                </Text>
              </Stack>
              <Group gap="xs" align="flex-end" wrap="wrap" style={{ flex: 1 }}>
                <MultiSelect
                  size="xs"
                  label="device_ids"
                  placeholder="Select devices"
                  value={hdfEnableDevicesDraft}
                  data={hdfSelectableDeviceOptions}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  nothingFoundMessage="No devices discovered"
                  onChange={onEnableDevicesDraftChange}
                  style={{ flex: "1 1 260px" }}
                />
                <Button
                  size="xs"
                  loading={hdfDevicesEnableBusy}
                  disabled={hdfCommandsBlocked || !hdfSupportsDevicesEnable}
                  onClick={() => {
                    void onExecuteDevicesEnable();
                  }}
                >
                  Enable
                </Button>
              </Group>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="flex-end" wrap="wrap">
              <Stack gap={2} style={{ flex: "0 0 220px" }}>
                <Text fw={600} size="sm">
                  hdf.devices.disable
                </Text>
                <Text size="xs" c="dimmed">
                  Disable writing for selected device IDs.
                </Text>
              </Stack>
              <Group gap="xs" align="flex-end" wrap="wrap" style={{ flex: 1 }}>
                <MultiSelect
                  size="xs"
                  label="device_ids"
                  placeholder="Select devices"
                  value={hdfDisableDevicesDraft}
                  data={hdfSelectableDeviceOptions}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  nothingFoundMessage="No devices discovered"
                  onChange={onDisableDevicesDraftChange}
                  style={{ flex: "1 1 260px" }}
                />
                <Button
                  size="xs"
                  loading={hdfDevicesDisableBusy}
                  disabled={hdfCommandsBlocked || !hdfSupportsDevicesDisable}
                  onClick={() => {
                    void onExecuteDevicesDisable();
                  }}
                >
                  Disable
                </Button>
              </Group>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="center" wrap="wrap">
              <Stack gap={2}>
                <Text fw={600} size="sm">
                  hdf.processes.get
                </Text>
                <Text size="xs" c="dimmed">
                  Query known, enabled, and disabled process telemetry writers.
                </Text>
              </Stack>
              <Button
                size="xs"
                loading={hdfProcessesGetBusy}
                disabled={hdfCommandsBlocked || !hdfSupportsProcessesGet}
                onClick={() => {
                  void onExecuteProcessesGet();
                }}
              >
                Run
              </Button>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="flex-end" wrap="wrap">
              <Stack gap={2} style={{ flex: "0 0 220px" }}>
                <Text fw={600} size="sm">
                  hdf.processes.enable
                </Text>
                <Text size="xs" c="dimmed">
                  Enable telemetry writing for selected process IDs.
                </Text>
              </Stack>
              <Group gap="xs" align="flex-end" wrap="wrap" style={{ flex: 1 }}>
                <MultiSelect
                  size="xs"
                  label="process_ids"
                  placeholder="Select processes"
                  value={hdfEnableProcessesDraft}
                  data={hdfSelectableProcessOptions}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  nothingFoundMessage="No processes discovered"
                  onChange={onEnableProcessesDraftChange}
                  style={{ flex: "1 1 260px" }}
                />
                <Button
                  size="xs"
                  loading={hdfProcessesEnableBusy}
                  disabled={hdfCommandsBlocked || !hdfSupportsProcessesEnable}
                  onClick={() => {
                    void onExecuteProcessesEnable();
                  }}
                >
                  Enable
                </Button>
              </Group>
            </Group>
          </Card>

          <Card radius="md" p="sm" style={{ border: "1px solid var(--card-border)" }}>
            <Group justify="space-between" align="flex-end" wrap="wrap">
              <Stack gap={2} style={{ flex: "0 0 220px" }}>
                <Text fw={600} size="sm">
                  hdf.processes.disable
                </Text>
                <Text size="xs" c="dimmed">
                  Disable telemetry writing for selected process IDs.
                </Text>
              </Stack>
              <Group gap="xs" align="flex-end" wrap="wrap" style={{ flex: 1 }}>
                <MultiSelect
                  size="xs"
                  label="process_ids"
                  placeholder="Select processes"
                  value={hdfDisableProcessesDraft}
                  data={hdfSelectableProcessOptions}
                  searchable
                  clearable
                  comboboxProps={{ zIndex: 500 }}
                  nothingFoundMessage="No processes discovered"
                  onChange={onDisableProcessesDraftChange}
                  style={{ flex: "1 1 260px" }}
                />
                <Button
                  size="xs"
                  loading={hdfProcessesDisableBusy}
                  disabled={hdfCommandsBlocked || !hdfSupportsProcessesDisable}
                  onClick={() => {
                    void onExecuteProcessesDisable();
                  }}
                >
                  Disable
                </Button>
              </Group>
            </Group>
          </Card>
        </Stack>
      </Stack>
    </Modal>
  );
}
