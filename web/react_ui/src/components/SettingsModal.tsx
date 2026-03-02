import {
  Button,
  Group,
  Modal,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconFileText,
  IconRefresh,
  IconSquarePlus,
} from "@tabler/icons-react";
import type { ChangeEvent, RefObject } from "react";
import type { GatewaySettingsInfo } from "../api";

type Props = {
  opened: boolean;
  onClose: () => void;
  settingsFileInputRef: RefObject<HTMLInputElement>;
  onImportUiProfile: (event: ChangeEvent<HTMLInputElement>) => Promise<void>;
  onExportUiProfile: () => void;
  onReload: () => Promise<unknown> | void;
  loading: boolean;
  error: string | null;
  gatewaySettings: GatewaySettingsInfo | null;
  resolvedApiBase: string;
  resolvedWsBase: string;
  telemetryStreamStatus: string;
};

export function SettingsModal({
  opened,
  onClose,
  settingsFileInputRef,
  onImportUiProfile,
  onExportUiProfile,
  onReload,
  loading,
  error,
  gatewaySettings,
  resolvedApiBase,
  resolvedWsBase,
  telemetryStreamStatus,
}: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Settings"
      size="clamp(42rem, 82vw, 64rem)"
      centered
      zIndex={425}
    >
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Text size="sm" c="dimmed">
              Runtime endpoints and gateway settings (read-only).
            </Text>
            <Text size="xs" c="dimmed">
              UI profile export/import includes layout, plot panels, DAG
              workspaces, and pinned commands.
            </Text>
          </Stack>
          <Group gap="xs">
            <input
              ref={settingsFileInputRef}
              type="file"
              accept=".json,application/json"
              style={{ display: "none" }}
              onChange={(event) => {
                void onImportUiProfile(event);
              }}
            />
            <Button
              size="xs"
              variant="light"
              leftSection={<IconFileText size={14} />}
              onClick={onExportUiProfile}
            >
              Export UI profile
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconSquarePlus size={14} />}
              onClick={() => settingsFileInputRef.current?.click()}
            >
              Import UI profile
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              loading={loading}
              onClick={() => {
                void onReload();
              }}
            >
              Reload
            </Button>
          </Group>
        </Group>

        {error && (
          <Text size="sm" c="red">
            {error}
          </Text>
        )}
        {gatewaySettings?.loopback_warning && (
          <Text size="sm" c="yellow">
            {gatewaySettings.loopback_warning_message ||
              "Configured endpoints use loopback addresses."}
          </Text>
        )}

        <TextInput label="API base" value={resolvedApiBase} readOnly />
        <TextInput label="WebSocket base" value={resolvedWsBase} readOnly />
        <TextInput
          label="API origin (server view)"
          value={gatewaySettings?.api_origin ?? "Unavailable"}
          readOnly
        />
        <TextInput
          label="Server host IP candidates"
          value={
            gatewaySettings?.host_ip_candidates &&
            gatewaySettings.host_ip_candidates.length > 0
              ? gatewaySettings.host_ip_candidates.join(", ")
              : "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="Router RPC endpoint"
          value={gatewaySettings?.router_rpc ?? "Unavailable"}
          readOnly
        />
        <TextInput
          label="Manager PUB endpoint"
          value={gatewaySettings?.manager_pub ?? "Unavailable"}
          readOnly
        />
        <TextInput
          label="Suggested router RPC endpoint"
          value={
            gatewaySettings?.router_rpc_hint ??
            gatewaySettings?.router_rpc ??
            "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="Suggested manager PUB endpoint"
          value={
            gatewaySettings?.manager_pub_hint ??
            gatewaySettings?.manager_pub ??
            "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="RPC timeout (ms)"
          value={
            gatewaySettings ? String(gatewaySettings.rpc_timeout_ms) : "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="Telemetry topics"
          value={
            gatewaySettings && gatewaySettings.telemetry_topics.length > 0
              ? gatewaySettings.telemetry_topics.join(", ")
              : "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="Log topics"
          value={
            gatewaySettings && gatewaySettings.log_topics.length > 0
              ? gatewaySettings.log_topics.join(", ")
              : "Unavailable"
          }
          readOnly
        />
        <TextInput
          label="Telemetry stream status"
          value={telemetryStreamStatus}
          readOnly
        />
      </Stack>
    </Modal>
  );
}
