import {
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
} from "@mantine/core";
import { ParamInput } from "./ParamInput";
import type { CapabilityMember } from "../types";

type CapabilityParam = NonNullable<CapabilityMember["params"]>[number];

type Props = {
  opened: boolean;
  onClose: () => void;
  title: string;
  capabilities: ReadonlyArray<CapabilityMember>;
  commandAction: string;
  onActionChange: (value: string | null) => void;
  showAdvancedParams: boolean;
  onShowAdvancedParamsChange: (value: boolean) => void;
  activeParams: ReadonlyArray<CapabilityParam>;
  commandParamValues: Record<string, string>;
  onParamValueChange: (name: string, value: string) => void;
  commandParams: string;
  onCommandParamsChange: (value: string) => void;
  onExecute: () => void;
};

export function ProcessCommandModal({
  opened,
  onClose,
  title,
  capabilities,
  commandAction,
  onActionChange,
  showAdvancedParams,
  onShowAdvancedParamsChange,
  activeParams,
  commandParamValues,
  onParamValueChange,
  commandParams,
  onCommandParamsChange,
  onExecute,
}: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg" centered zIndex={450}>
      <Stack gap="md">
        <Group align="flex-end" justify="space-between">
          <Select
            label="Action"
            placeholder="Select command"
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
          <Button variant="light" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={onExecute}>Execute</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
