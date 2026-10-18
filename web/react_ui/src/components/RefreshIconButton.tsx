import { ActionIcon, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";

type Props = {
  label: string;
  onClick: () => void;
  loading?: boolean;
  size?: "sm" | "md";
};

/** The one refresh control used across modals: an icon button with a tooltip. */
export function RefreshIconButton({ label, onClick, loading = false, size = "md" }: Props) {
  return (
    <Tooltip label={label} withArrow>
      <ActionIcon
        size={size === "md" ? 30 : 26}
        variant="light"
        color="gray"
        aria-label={label}
        loading={loading}
        onClick={onClick}
      >
        <IconRefresh size={size === "md" ? 16 : 14} />
      </ActionIcon>
    </Tooltip>
  );
}
