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
  settingsSlot,
  onExpand,
  menuItems,
}: PanelCardHeaderProps) {
  if (editing) {
    return (
      <div className="panel-card-header" data-no-activate="true">
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
