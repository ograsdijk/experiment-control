import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type ReactNode,
  type SetStateAction,
} from "react";

import type {
  ExtraUiInfo,
  GatewaySettingsInfo,
  InstanceRuntimeStatus,
} from "../../api";

/**
 * Shared state container for the settings modal + instance-runtime
 * status panel.
 *
 * App.tsx historically held the settings modal's open/loading/error
 * flags, the gateway settings + extra-UI list + instance runtime
 * status it shows, plus the load/cleanup busy flags and the file
 * input DOM ref used by the import-profile button. All of those
 * moved here so the settings modal (and any future extraction of it
 * into its own panel module) can subscribe directly via
 * `useSettings()` instead of receiving the state through props.
 *
 * **Scope choices** (mirrors the round-8/9/10 Context shape):
 *
 * - The Provider owns the **state container only**. The network-side
 *   handlers (fetch gateway settings, fetch instance runtime status,
 *   trigger cleanup, import / export profile) stay in App.tsx for now —
 *   they call into other state (panels, devices, profile) that hasn't
 *   been extracted, or into helpers the modal renders directly.
 *
 * **Downstream-compatibility**: no centrex instance UI references any
 * of this state — the settings modal is App-only. The Provider is
 * downstream-safe.
 */

export interface SettingsContextValue {
  // -----------------------------------------------------------------
  // Modal open + loading flags
  // -----------------------------------------------------------------
  settingsOpen: boolean;
  setSettingsOpen: Dispatch<SetStateAction<boolean>>;
  settingsLoading: boolean;
  setSettingsLoading: Dispatch<SetStateAction<boolean>>;
  settingsError: string | null;
  setSettingsError: Dispatch<SetStateAction<string | null>>;

  // -----------------------------------------------------------------
  // Server-fetched payloads displayed by the modal
  // -----------------------------------------------------------------
  gatewaySettings: GatewaySettingsInfo | null;
  setGatewaySettings: Dispatch<SetStateAction<GatewaySettingsInfo | null>>;
  extraUis: ExtraUiInfo[];
  setExtraUis: Dispatch<SetStateAction<ExtraUiInfo[]>>;
  instanceRuntimeStatus: InstanceRuntimeStatus | null;
  setInstanceRuntimeStatus: Dispatch<
    SetStateAction<InstanceRuntimeStatus | null>
  >;
  instanceRuntimeLoading: boolean;
  setInstanceRuntimeLoading: Dispatch<SetStateAction<boolean>>;
  instanceRuntimeError: string | null;
  setInstanceRuntimeError: Dispatch<SetStateAction<string | null>>;
  instanceCleanupBusy: boolean;
  setInstanceCleanupBusy: Dispatch<SetStateAction<boolean>>;

  // -----------------------------------------------------------------
  // DOM ref — hidden <input type="file"> used by the
  // import-profile button. Lives here so the modal can pick it up via
  // context once it's extracted into its own component.
  // -----------------------------------------------------------------
  settingsFileInputRef: MutableRefObject<HTMLInputElement | null>;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [gatewaySettings, setGatewaySettings] =
    useState<GatewaySettingsInfo | null>(null);
  const [extraUis, setExtraUis] = useState<ExtraUiInfo[]>([]);
  const [instanceRuntimeStatus, setInstanceRuntimeStatus] =
    useState<InstanceRuntimeStatus | null>(null);
  const [instanceRuntimeLoading, setInstanceRuntimeLoading] = useState(false);
  const [instanceRuntimeError, setInstanceRuntimeError] = useState<
    string | null
  >(null);
  const [instanceCleanupBusy, setInstanceCleanupBusy] = useState(false);
  const settingsFileInputRef = useRef<HTMLInputElement | null>(null);

  const value = useMemo<SettingsContextValue>(
    () => ({
      settingsOpen,
      setSettingsOpen,
      settingsLoading,
      setSettingsLoading,
      settingsError,
      setSettingsError,
      gatewaySettings,
      setGatewaySettings,
      extraUis,
      setExtraUis,
      instanceRuntimeStatus,
      setInstanceRuntimeStatus,
      instanceRuntimeLoading,
      setInstanceRuntimeLoading,
      instanceRuntimeError,
      setInstanceRuntimeError,
      instanceCleanupBusy,
      setInstanceCleanupBusy,
      settingsFileInputRef,
    }),
    [
      settingsOpen,
      settingsLoading,
      settingsError,
      gatewaySettings,
      extraUis,
      instanceRuntimeStatus,
      instanceRuntimeLoading,
      instanceRuntimeError,
      instanceCleanupBusy,
    ]
  );

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (ctx === null) {
    throw new Error("useSettings must be called inside a <SettingsProvider>");
  }
  return ctx;
}
