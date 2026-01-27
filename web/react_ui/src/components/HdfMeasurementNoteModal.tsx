import {
  Badge,
  Button,
  Group,
  Modal,
  Stack,
  Text,
} from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { processStateColor } from "../features/runtime/helpers";
import type { MeasurementFieldSchema } from "../features/hdf/types";

type Props = {
  opened: boolean;
  onClose: () => void;
  title: string;
  hdfWriterState: string;
  measurementType: string | null;
  measurementNotesRows: number;
  filePath: string | null;
  refreshLoading: boolean;
  refreshDisabled: boolean;
  onRefresh: () => Promise<unknown> | void;
  showMeasurementUi: boolean;
  supportsMeasurementNote: boolean;
  fields: ReadonlyArray<MeasurementFieldSchema>;
  renderMeasurementFieldInput: (
    field: MeasurementFieldSchema,
    value: string,
    useCustom: boolean,
    onValueChange: (value: string) => void,
    onUseCustomChange: (value: boolean) => void
  ) => ReactNode;
  noteValuesDraft: Record<string, string>;
  noteCustomByField: Record<string, boolean>;
  onSetFieldValue: (fieldKey: string, value: string) => void;
  onSetFieldUseCustom: (fieldKey: string, useCustom: boolean) => void;
  measurementNoteBusy: boolean;
  addNoteDisabled: boolean;
  onAddNote: () => Promise<unknown> | void;
};

export function HdfMeasurementNoteModal({
  opened,
  onClose,
  title,
  hdfWriterState,
  measurementType,
  measurementNotesRows,
  filePath,
  refreshLoading,
  refreshDisabled,
  onRefresh,
  showMeasurementUi,
  supportsMeasurementNote,
  fields,
  renderMeasurementFieldInput,
  noteValuesDraft,
  noteCustomByField,
  onSetFieldValue,
  onSetFieldUseCustom,
  measurementNoteBusy,
  addNoteDisabled,
  onAddNote,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg" centered zIndex={455}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <Stack gap={2}>
            <Group gap="xs" wrap="wrap">
              <Badge variant="light" color={processStateColor(hdfWriterState)}>
                {hdfWriterState}
              </Badge>
              {measurementType && (
                <Badge variant="light" color="indigo">
                  {measurementType}
                </Badge>
              )}
              <Badge variant="light" color="gray">
                notes {measurementNotesRows}
              </Badge>
            </Group>
            <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
              {filePath ?? "No active file"}
            </Text>
          </Stack>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            loading={refreshLoading}
            disabled={refreshDisabled}
            onClick={() => {
              void onRefresh();
            }}
          >
            Refresh
          </Button>
        </Group>

        {!showMeasurementUi && (
          <Text size="sm" c="dimmed">
            Measurement note UI is unavailable because no measurement schema is active
            for the HDF writer.
          </Text>
        )}

        {showMeasurementUi && supportsMeasurementNote && (
          <Stack gap="xs">
            {fields.map((field) => (
              <Stack key={`note-modal-field-${field.key}`} gap={2}>
                {renderMeasurementFieldInput(
                  field,
                  noteValuesDraft[field.key] ?? "",
                  noteCustomByField[field.key] === true,
                  (next) => {
                    onSetFieldValue(field.key, next);
                  },
                  (next) => {
                    onSetFieldUseCustom(field.key, next);
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
          <Button variant="light" onClick={onClose}>
            Close
          </Button>
          <Button
            loading={measurementNoteBusy}
            disabled={addNoteDisabled}
            onClick={() => {
              void onAddNote();
            }}
          >
            Add note
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
