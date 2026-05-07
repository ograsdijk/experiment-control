import {
  Box,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import { IconPlaylistAdd, IconStar, IconStarFilled } from "@tabler/icons-react";
import type { ReactNode } from "react";
import type { ApiResponse } from "../api";
import { JsonPreview } from "./JsonPreview";
import { ParamInput } from "./ParamInput";
import type { CapabilityMember } from "../types";

type CapabilityParam = NonNullable<CapabilityMember["params"]>[number];

type Props = {
  opened: boolean;
  onClose: () => void;
  title: ReactNode;
  capabilities: ReadonlyArray<CapabilityMember>;
  commandAction: string;
  onActionChange: (value: string | null) => void;
  commandLabel: string;
  onLabelChange: (value: string) => void;
  showAdvancedParams: boolean;
  onShowAdvancedParamsChange: (value: boolean) => void;
  activeParams: ReadonlyArray<CapabilityParam>;
  commandParamValues: Record<string, string>;
  onParamValueChange: (name: string, value: string) => void;
  commandParams: string;
  onCommandParamsChange: (value: string) => void;
  commandResponse: ApiResponse<unknown> | null;
  colorScheme: "light" | "dark";
  isPinned: boolean;
  pinDisabled: boolean;
  onTogglePin: () => void;
  deckDisabled?: boolean;
  onAddToDeck?: () => void;
  onExecute: () => void;
};

export function DeviceCommandModal({
  opened,
  onClose,
  title,
  capabilities,
  commandAction,
  onActionChange,
  commandLabel,
  onLabelChange,
  showAdvancedParams,
  onShowAdvancedParamsChange,
  activeParams,
  commandParamValues,
  onParamValueChange,
  commandParams,
  onCommandParamsChange,
  commandResponse,
  colorScheme,
  isPinned,
  pinDisabled,
  onTogglePin,
  deckDisabled,
  onAddToDeck,
  onExecute,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="clamp(42rem, 82vw, 64rem)" centered>
      <Stack gap="md">
        <Group align="flex-end" justify="space-between">
          <Select
            label="Action"
            placeholder="Select or type"
            searchable
            comboboxProps={{ zIndex: 500 }}
            data={capabilities.map((cap) => ({
              value: cap.name,
              label: cap.name,
            }))}
            value={commandAction}
            onChange={onActionChange}
            flex={1}
          />
        </Group>
        <TextInput
          label="Action name"
          value={commandAction}
          onChange={(event) => onActionChange(event.currentTarget.value)}
          placeholder="set_mode"
        />
        <TextInput
          label="Pinned label"
          value={commandLabel}
          onChange={(event) => onLabelChange(event.currentTarget.value)}
          placeholder="Optional label for pinned command"
        />
        {!showAdvancedParams && activeParams.length === 0 && (
          <Text size="sm" c="dimmed">
            No parameters required.
          </Text>
        )}
        {!showAdvancedParams &&
          activeParams.map((param) => (
            <ParamInput
              key={param.name}
              param={param}
              value={commandParamValues[param.name] ?? ""}
              onChange={(nextValue) => onParamValueChange(param.name, nextValue)}
            />
          ))}
        {showAdvancedParams && (
          <Textarea
            label="Params (JSON)"
            minRows={4}
            value={commandParams}
            onChange={(event) => onCommandParamsChange(event.currentTarget.value)}
          />
        )}
        <Switch
          checked={showAdvancedParams}
          onChange={(event) =>
            onShowAdvancedParamsChange(event.currentTarget.checked)
          }
          label="Advanced JSON params"
        />
        <Group justify="flex-end">
          <Button
            variant="light"
            leftSection={<IconPlaylistAdd size={14} />}
            onClick={onAddToDeck}
            disabled={deckDisabled || !onAddToDeck}
          >
            Add to Deck
          </Button>
          <Button
            variant="light"
            leftSection={isPinned ? <IconStarFilled size={14} /> : <IconStar size={14} />}
            onClick={onTogglePin}
            disabled={pinDisabled}
          >
            {isPinned ? "Unpin" : "Pin"}
          </Button>
          <Button variant="light" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={onExecute}>Execute</Button>
        </Group>
        {commandResponse !== null && (
          <Box
            style={{
              border: "1px solid var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-sm)",
              padding: "0.75rem",
              backgroundColor:
                colorScheme === "dark"
                  ? "var(--mantine-color-dark-7)"
                  : "var(--mantine-color-gray-0)",
            }}
          >
            <JsonPreview
              text={JSON.stringify(commandResponse, null, 2)}
              colorScheme={colorScheme}
            />
          </Box>
        )}
      </Stack>
    </Modal>
  );
}
