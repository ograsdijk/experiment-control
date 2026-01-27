# Experiment Control React UI

Minimal Mantine + uPlot UI wired to the FastAPI gateway.

## Prerequisites

- Node.js 18+
- Gateway running (`experiment_control.fastapi.app`)

## Install

```bash
cd web/react_ui
npm install
```

## Run (dev)

```bash
npm run dev
```

By default it connects to the same host as the UI (Vite dev server).
If your gateway is on a different host/port, set env vars:

```bash
VITE_API_BASE=http://127.0.0.1:8000
VITE_WS_BASE=http://127.0.0.1:8000
npm run dev
```

On Windows PowerShell:

```powershell
$env:VITE_API_BASE="http://127.0.0.1:8000"
$env:VITE_WS_BASE="http://127.0.0.1:8000"
npm run dev
```

## Run (build + preview)

```bash
npm run build
npm run preview
```

## Notes

- REST calls go to `/api/*` on the gateway.
- Telemetry uses the WebSocket `/ws/telemetry`.
- The UI maintains its own ring buffers; it does not persist history.
