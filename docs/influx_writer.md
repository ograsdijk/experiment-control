# Influx Writer (Wide Mode)

## Overview

`influx_writer` subscribes to `manager.telemetry_update` and writes to InfluxDB v2 via line protocol.

Current mode is **wide**:
- one Influx row per telemetry update (per device)
- one field per telemetry signal in that update
- optional side fields per signal for quality/unit

It does **not** write federated mirror telemetry. Only the owning instance should write those devices.

## Row shape

For each telemetry bundle:

- measurement: resolved by priority (see below)
- tags:
  - always: `instance_id`, `device_id`
  - optional: `device_type` (from metadata), selected metadata tags (for example `location`)
  - plus destination static tags and optional per-route tags
- fields:
  - signal fields: `<signal_name> = <value>`
  - optional quality fields: `<signal_name>__quality = "OK|BAD|MISSING|..."`
  - optional unit fields: `<signal_name>__unit = "<unit>"`

Supported signal value types:
- `bool` -> boolean field
- `int` -> i64 field
- `float` finite only
- `str` -> string field

Unsupported/non-finite values are skipped per signal. If no signal fields remain, the row is skipped.

Timestamp source:
- bundle `ts.t_wall` first
- fallback to first signal timestamp
- fallback to current wall clock

## Measurement resolution

Measurement is resolved per device in this order:

1. `routes.<device_id>.measurement`
2. `routes.<device_id>.device_type`
3. `device_metadata[device_type_key]` from `manager.device_config`
4. destination fallback `measurement`

Recommended: provide `device_metadata.device_type` in device YAML and keep destination fallback as `unknown_device`.

## Config schema (`init_kwargs`)

```yaml
init_kwargs:
  instance_id: "lab_a"
  destinations:
    default:
      url: "http://127.0.0.1:8086"
      token: "${INFLUX_TOKEN}"
      org: "centrex"
      bucket: "telemetry"
      measurement: "unknown_device"
      precision: "ns"          # ns | us | ms | s
      request_timeout_s: 5.0
      static_tags:
        lab: "centrex"
  default_destination: "default"
  routes:
    trace1:
      destination: "default"
      measurement: "dummy_resonance_trace"   # optional
      device_type: "dummy_resonance_trace"   # optional
      tags:                                  # optional
        location: "bench_a"
  disabled_devices: []
  enabled: true
  write_batch_size: 500
  write_flush_interval_ms: 1000
  max_queue_points: 100000
  overflow_policy: "drop_oldest"             # drop_oldest | drop_newest
  include_device_type_tag: true
  include_quality_fields: true
  include_unit_fields: false
  device_type_key: "device_type"
  device_tag_keys: ["location"]
```

## Device metadata contract

Device config payload (`manager.device_config`) should include:

```yaml
device_metadata:
  device_type: hipace700
  location: rack_a
```

`device_type_key` selects which metadata key is used for measurement/type resolution.
`device_tag_keys` selects which metadata keys are copied into tags.

## Federated behavior

`influx_writer` marks devices as remote if:
- `source_kind == federated`, or
- `is_remote == true`

Telemetry updates for those device IDs are skipped (`points_skipped_remote` counter).

## Process RPC

Supported process RPC methods:
- `influx.status`
- `influx.enable` / `influx.disable`
- `influx.flush`
- `influx.devices.get`
- `influx.devices.enable` / `influx.devices.disable`

`influx.status` includes counters such as:
- `points_received`
- `points_written`
- `points_skipped_invalid`
- `points_skipped_remote`
- `write_errors`

## Example line protocol

Example wide row:

```text
hipace700,instance_id=lab_a,device_id=pump1,location=rack_a rot_speed_hz=233.5,rot_speed_hz__quality="OK",is_running=true,is_running__quality="OK" 1731112345000000000
```

