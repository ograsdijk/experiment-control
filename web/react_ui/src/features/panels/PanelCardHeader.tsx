import { memo, type ReactNode } from "react";
import { ActionIcon, Group, Menu, Text, TextInput } from "@mantine/core";
import {
  IconArrowsMaximize,
  IconCheck,
  IconDotsVertical,
  IconX,
} from "@tabler/icons-react";

export type PanelMenuItem = {
  key: string;
  label: string;
  color?: string;
  disabled?: boolean;
  onClick: () => void;
};

type PanelCardHeaderProps = {
  title: string;
  placeholder: string;
  editing: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
  onStartEdit: () => void;
  /**
   * Live link state, shown as a dot before the title. Null for panels
   * with no link of their own (telemetry), which renders no dot.
   */
  connected?: boolean | null;
  linkLabel?: string;
  /** The settings control, target and dropdown — owned by the card. */
  settingsSlot: ReactNode;
  /** Omitted entirely for panel kinds that cannot be expanded. */
  onExpand?: () => void;
  menuItems: PanelMenuItem[];
};

/**
 * Compact monitoring header: semantic title on the left, icon controls on
 * the right.
 *
 * Everything that used to sit here permanently — pencil, Active badge /
 * Set active button, panel-type badge, labelled Plot options and Clear
 * buttons, drag-state badge — is either gone or behind one of these three
 * controls. Renaming is progressive: double-click the title, or use the
 * overflow menu.
 *
 * The link dot reads as a status light on the panel's name and costs no
 * height here, where the row exists regardless. Under the plot it was the
 * only permanent occupant of its own row, so a healthy card paid a full
 * line for it.
 */
function PanelCardHeaderImpl({
  title,
  placeholder,
  editing,
  draft,
  onDraftChange,
  onCommit,
  onCancel,
  onStartEdit,
  connected = null,
  linkLabel = "link",
  settingsSlot,
  onExpand,
  menuItems,
}: PanelCardHeaderProps) {
  const linkDot =
    connected === null ? null : (
      <span
        className="panel-card-status-dot panel-card-header-dot"
        title={`${linkLabel} link ${connected ? "connected" : "disconnected"}`}
        aria-label={`${linkLabel} link ${
          connected ? "connected" : "disconnected"
        }`}
        style={{
          background: connected
            ? "var(--mantine-color-teal-6)"
            : "var(--mantine-color-red-6)",
        }}
      />
    );

  if (editing) {
    return (
      <div className="panel-card-header" data-no-activate="true">
        {linkDot}
        <TextInput
          size="xs"
          style={{ flex: "1 1 auto", minWidth: 0 }}
          value={draft}
          onChange={(event) => onDraftChange(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onCommit();
              return;
            }
            if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
          autoFocus
          placeholder={placeholder}
          aria-label="Panel title"
        />
        <Group gap={2} className="panel-card-header-actions">
          <ActionIcon
            size="sm"
            variant="light"
            color="teal"
            onClick={onCommit}
            aria-label="Save title"
            title="Save title"
          >
            <IconCheck size={14} />
          </ActionIcon>
          <ActionIcon
            size="sm"
            variant="light"
            color="gray"
            onClick={onCancel}
            aria-label="Cancel rename"
            title="Cancel rename"
          >
            <IconX size={14} />
          </ActionIcon>
        </Group>
      </div>
    );
  }

  return (
    <div className="panel-card-header">
      {linkDot}
      <Text
        fw={600}
        size="sm"
        className="panel-card-title"
        onDoubleClick={onStartEdit}
        title={`${title} — double-click to rename`}
      >
        {title}
      </Text>
      <div className="panel-card-header-actions" data-no-activate="true">
        {settingsSlot}
        {onExpand ? (
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            onClick={onExpand}
            aria-label="Enlarge plot"
            title="Enlarge plot"
          >
            <IconArrowsMaximize size={15} />
          </ActionIcon>
        ) : null}
        <Menu shadow="md" width={190} position="bottom-end" withinPortal zIndex={700}>
          <Menu.Target>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              aria-label="More panel actions"
              title="More actions"
            >
              <IconDotsVertical size={15} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            {menuItems.map((item) => (
              <Menu.Item
                key={item.key}
                color={item.color}
                disabled={item.disabled}
                onClick={item.onClick}
              >
                {item.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      </div>
    </div>
  );
}

export const PanelCardHeader = memo(PanelCardHeaderImpl);
