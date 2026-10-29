# Sequencer run form (`form:`)

A sequence can describe a form that the Sequencer UI shows as **Run with
form…**, next to Start. Each field sets one of the sequence's declared `vars`
for the next start, sent as `vars_override`. Nothing is written to the YAML.
The engine never reads `form:` while running. It is checked at load
(`sequencer/run_form.py`), so a field that names an undeclared var fails
then, rather than becoming a field that silently sets nothing.

The start event records `vars_override` and the resolved vars
(`docs/protocol.md`, `sequencer.lifecycle`), so the HDF file keeps what the
form chose.

The button needs the loaded sequence. With unloaded edits in the editor it is
disabled, because the form would describe text the sequencer has not loaded.

## Schema

```yaml
form:
  title: Quick parameter scan          # optional
  presets:                              # optional: buttons that set several vars
    - label: Laser only
      set: {arm_yag: false, neon_enabled: false}
  summary: {points: range_points, passes: passes, bursts: bursts_per_value, shots: n_traces}
  sections:                             # required, non-empty
    - title: Wavemeter
      toggle: wm_enabled                # optional: a bool var shown as the section's switch
      fields:                           # required, non-empty
        - {type: chips, var: wm_channels, options: wm_channel_options, label: Channels}
```

`summary` is optional. The form shows its `points` as the number of values,
and the product of all four as the total shots. A summary var the form doesn't
set uses the sequence's own value.

### Field types

Every field takes an optional `label` and `enabled_by` (a bool var: the field
is disabled while that var is false).

| type | keys | sets |
|---|---|---|
| `checkbox` | `var` | a bool |
| `number` | `var`, `min`, `max`, `step`, `unit` | a float |
| `integer` | `var`, `min`, `max`, `unit` | an int |
| `text` | `var` | a string |
| `select` | `var`, and `options` or `options_call`, `empty_label` | one option's value |
| `chips` | `var`, `options` | a list of option values, in the options' order |
| `target` | `var`, `catalog`, `other_exclude` | the chosen catalog entry (a dict) |
| `range` | `target`, `relative`, `from`, `to`, `points` | those four vars |

`options` names a var holding a list of `{value, label}` (`label` defaults
to the value). `options_call` fills a select from a device call instead:

```yaml
options_call: {device: pulseblaster, action: list_profiles, list: profiles, value: name}
```

`list` is the result key holding the list (the result itself when absent), and
`value` and `label` are the item keys (the item itself when absent).
`empty_label` adds a first option whose value is `""`.

### `target` and `range`

`catalog` names a var holding a list of targets. The form uses these keys of
an entry, and the sequence may use the rest:

- `id`, `device_label` (the Device dropdown), `param_label` (the Parameter
  dropdown), `kind`, `device`, `lock` (`none` hides the lock badge)
- `unit` (the setter's base unit), `display_unit`/`display_scale` and
  `offset_unit`/`offset_scale` (base units per display unit, for absolute
  values and for offsets)
- `min`/`max` (base units, `null` = none; out-of-range values keep Start
  disabled) and `default_span`
- `current: {device, signal, abs}`: the telemetry signal shown as the live
  value (`abs: true` shows its magnitude). It is also the centre of the
  range preview in relative mode. The sequence reads its own start value.

A catalog device with no telemetry is listed as "(no telemetry)".

Devices with telemetry that no catalog entry uses, minus `other_exclude`,
are offered under **Other**. Their parameters are each `set_X` that has a
matching `get_X` and takes either a value, or `channel` and a value. An Other
target is sent as a `generic` entry with these keys: `kind: generic`,
`device`, `set_action`, `get_action`, `value_param`, `channel_param`
(`null` when there is none), `channel`, `lock: none` and `settle_s: 1.0`.
A sequence that accepts generic targets calls them with templated parameter
names (`docs/sequencer_config.md`, `call`).

`range` sets `relative` (bool), `from`/`to` (base units: offsets when
relative, absolute values otherwise) and `points`. Choosing a new target
resets its range to ±`default_span`/2 around the current value. The sequence
generates the values itself, e.g.
`gen: {linspace: {start: "${origin + range_from}", stop: "${origin + range_to}", num: "${range_points}"}}`.

## Example

`centrex-experimental-stack`:
`instances/state-preparation-b-detection/sequences/quick_parameter_scan.yaml`.
