import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "uplot/dist/uPlot.min.css";
import "./styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { CommandsProvider } from "./features/commands/CommandsContext";
import { DevicesProvider } from "./features/devices/DevicesContext";
import { LayoutProvider } from "./features/layout/LayoutContext";
import { LogsProvider } from "./features/logs/LogsContext";
import { PanelsProvider } from "./features/panels/PanelsContext";
import { SettingsProvider } from "./features/runtime/SettingsContext";
import { StreamAnalysisProvider } from "./features/stream_analysis/StreamAnalysisContext";
import { TelemetryProvider } from "./features/telemetry/TelemetryContext";

const theme = createTheme({
  fontFamily: "IBM Plex Sans, Segoe UI, sans-serif",
  headings: { fontFamily: "Space Grotesk, Trebuchet MS, sans-serif" },
  primaryColor: "teal",
  defaultRadius: "md",
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Notifications position="top-right" />
      <LayoutProvider>
        <TelemetryProvider>
          <StreamAnalysisProvider>
            <DevicesProvider>
              <CommandsProvider>
                <LogsProvider>
                  <SettingsProvider>
                    <PanelsProvider>
                      <App />
                    </PanelsProvider>
                  </SettingsProvider>
                </LogsProvider>
              </CommandsProvider>
            </DevicesProvider>
          </StreamAnalysisProvider>
        </TelemetryProvider>
      </LayoutProvider>
    </MantineProvider>
  </React.StrictMode>
);
