# Pydantic Config Validation (Implementation Sketch)

## Goal
Use Pydantic for higher-level YAML validation (stack YAML + device/process wrapper YAMLs),
while keeping the existing specialized parsers for:
- telemetry_calls
- stream_calls
- run_meta_calls

This keeps error reporting clean and typed for the top-level structures, without
replacing the existing schema logic where it is already established.

## Why a hybrid approach
- Pydantic excels at validating the *shape* and required fields of top-level configs.
- The telemetry/stream/run_meta parsers already handle detailed validation and
  path-based errors; keep those for fidelity and compatibility.

## Proposed model layout (pydantic v2)

```python
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator

class ManagerCfg(BaseModel):
    registry_bind: str = "tcp://127.0.0.1:5555"
    internal_rpc_bind: str = "tcp://127.0.0.1:6002"
    external_rpc_bind: str = "tcp://127.0.0.1:6000"
    external_pub_bind: str = "tcp://127.0.0.1:6001"
    process_hb_bind_base: str = "tcp://127.0.0.1:6100"
    heartbeat_timeout_s: float = 3.0
    telemetry_stale_s: float = 10.0
    device_rpc_timeout_ms: int = 1500
    interceptor_rpc_timeout_ms: int = 500
    auto_connect_on_register: bool = True

class ConfigSources(BaseModel):
    dirs: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    glob: str = "*.yaml"

class StartupCfg(BaseModel):
    start_devices: bool = True
    start_processes: bool = True
    process_order: Optional[list[str]] = None
    wait_processes_running: Optional[bool] = None
    connect: Optional[bool] = None
    wait_for_registered: bool = True
    wait_for_online: bool = True
    timeout_s: float = 10.0
    poll_ms: int = 50

class StackCfg(BaseModel):
    version: int = 1
    manager: ManagerCfg = Field(default_factory=ManagerCfg)
    devices: ConfigSources = Field(default_factory=ConfigSources)
    processes: ConfigSources = Field(default_factory=ConfigSources)
    startup: StartupCfg = Field(default_factory=StartupCfg)

# --- Device YAML wrapper (device_spec_from_yaml style) ---
class DeviceDriverCfg(BaseModel):
    file: str
    class_name: str

class DeviceYaml(BaseModel):
    device_id: str
    driver: DeviceDriverCfg
    init_kwargs: dict[str, Any] = Field(default_factory=dict)
    telemetry_calls: Any = None
    stream_calls: Any = None
    run_meta_calls: Any = None
    device_metadata: dict[str, Any] = Field(default_factory=dict)
    stream_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("device_id")
    def device_id_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("device_id must be non-empty")
        return v

# --- Process YAML wrapper (process_spec_from_yaml style) ---
class ProcessRunnerCfg(BaseModel):
    file: str
    class_name: str

class ProcessYaml(BaseModel):
    process_id: str
    process: Optional[ProcessRunnerCfg] = None
    argv: Optional[list[str]] = None
    init_kwargs: dict[str, Any] = Field(default_factory=dict)

    heartbeat_timeout_s: float = 3.0
    shutdown_timeout_s: float = 3.0
    restart_policy: str = "NEVER"
    restart_backoff_s: float = 0.5
    max_restarts: Optional[int] = None
    heartbeat_endpoint: Optional[str] = None
    heartbeat_period_s: Optional[float] = None

    @field_validator("process_id")
    def process_id_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("process_id must be non-empty")
        return v

    @field_validator("argv")
    def argv_list_str(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is not None and not all(isinstance(x, str) for x in v):
            raise ValueError("argv must be list[str]")
        return v

    @field_validator("init_kwargs")
    def forbid_reserved_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        reserved = {"process_id", "manager_rpc", "manager_pub", "heartbeat_endpoint"}
        bad = reserved & v.keys()
        if bad:
            raise ValueError(f"init_kwargs contains reserved keys: {sorted(bad)}")
        return v

    @field_validator("process")
    def process_or_argv(cls, v, info):
        # ensure either process or argv is set, not both
        data = info.data
        if v is None and data.get("argv") is None:
            raise ValueError("either process or argv must be provided")
        if v is not None and data.get("argv") is not None:
            raise ValueError("process and argv are mutually exclusive")
        return v
```

## Integration plan (minimal changes)
1. Add `pydantic>=2` as a dependency (optional at first).
2. Load YAMLs and validate with Pydantic models:
   - `StackCfg` for the stack YAML
   - `DeviceYaml` / `ProcessYaml` for device and process YAML files
3. After Pydantic validation, pass `telemetry_calls`, `stream_calls`, `run_meta_calls`
   to existing `*_calls_from_json(...)` for current behavior and errors.
4. Convert validated models into existing `DeviceSpec` / `ProcessSpec`.

## Notes
- Keep `process_id` top-level; do not allow it in `init_kwargs`.
- Keep `manager_rpc`, `manager_pub`, `heartbeat_endpoint` manager-injected only.
- Permit `heartbeat_period_s` in YAML (process-specific).
- Maintain compatibility with argv-based process configs.
