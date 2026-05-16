import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "uplot/dist/uPlot.min.css";
import "./styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
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
      <TelemetryProvider>
        <App />
      </TelemetryProvider>
    </MantineProvider>
  </React.StrictMode>
);
