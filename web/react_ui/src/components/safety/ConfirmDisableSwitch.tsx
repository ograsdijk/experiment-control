import { Button, Group, Popover, Stack, Switch, Text } from "@mantine/core";
import { useState } from "react";

type Props = {
  checked: boolean;
  disabled?: boolean;
  /** What is being switched off, e.g. `rule "limit_temperature"`. */
  subject: string;
  onChange: (enabled: boolean) => void;
  size?: "xs" | "sm";
  ariaLabel?: string;
};

/**
 * Enable switch for safety rules: enabling is immediate, disabling asks for
 * confirmation first, since a disabled rule stops protecting until someone
 * turns it back on.
 */
export function ConfirmDisableSwitch({
  checked,
  disabled = false,
  subject,
  onChange,
  size = "sm",
  ariaLabel,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  return (
    <Popover
      opened={confirmOpen}
      onChange={setConfirmOpen}
      position="left"
      withArrow
      shadow="md"
      trapFocus
    >
      <Popover.Target>
        <span
          // Keep row clicks (which select the rule) from firing on the switch.
          onClick={(event) => event.stopPropagation()}
          style={{ display: "inline-flex" }}
        >
          <Switch
            size={size}
            checked={checked}
            disabled={disabled}
            aria-label={ariaLabel ?? `Enable ${subject}`}
            onChange={(event) => {
              const next = event.currentTarget.checked;
              if (next) {
                onChange(true);
              } else {
                setConfirmOpen(true);
              }
            }}
          />
        </span>
      </Popover.Target>
      <Popover.Dropdown onClick={(event) => event.stopPropagation()}>
        <Stack gap="xs" maw={260}>
          <Text size="sm">Disable {subject}?</Text>
          <Text size="xs" c="dimmed">
            It stops protecting until it is enabled again.
          </Text>
          <Group gap="xs" justify="flex-end">
            <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              size="compact-xs"
              color="red"
              onClick={() => {
                setConfirmOpen(false);
                onChange(false);
              }}
            >
              Disable
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
