# Sequencer run form (`form:`)

A sequence can describe a form that the Sequencer UI shows as **Run with
form…**, next to Start. Each field sets one of the sequence's declared `vars`
for the next start, sent as `vars_override`. Nothing is written to the YAML.
The engine never reads `form:` while running. It is checked at load
(`sequencer/run_form.py`), so a field that names an undeclared var fails
then, rather than becoming a field that silently sets nothing.

The form is only a way to set vars. What a var does, which devices exist and
how a value is set, waited for or restored all live in the sequence. The
start event records `vars_override` and the resolved vars
(`docs/protocol.md`, `sequencer.lifecycle`), so the HDF file keeps what the
form chose.

The button is disabled while the editor doesn't match the loaded sequence
(unloaded edits, or another client loaded a different one), because the form
would describe text the sequencer has not loaded. A start the sequencer
rejects leaves the dialog open with its choices.

## Schema

```yaml
form:
  title: Frequency scan                 # optional
  presets:                              # optional: buttons that set several vars
    - label: Quick look
      set: {passes: 1, points: 5}
  summary:                              # optional: figures under the form
    - {label: Values, product: [points]}
    - {label: Total shots, product: [points, passes, shots_per_value]}
  sections:                             # required, non-empty
    - title: Monitor
      toggle: monitor_enabled           # optional: a bool var shown as the section's switch
      fields:                           # required, non-empty
        - {type: chips, var: monitor_channels, options: monitor_channel_options, label: Channels}
```

Each `summary` figure is the product of its vars. A var the form doesn't set
uses the sequence's own value.

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
| `target` | `var`, `catalog`, `other_exclude`, `other_defaults` | the chosen catalog entry (a dict) |
| `range` | `target`, `relative`, `from`, `to`, `points` | those four vars |

`options` names a var holding a list of `{value, label}` (`label` defaults
to the value). `options_call` fills a select from a device call instead:

```yaml
options_call: {device: timer, action: list_profiles, list: profiles, value: name}
```

`list` is the result key holding the list (the result itself when absent), and
`value` and `label` are the item keys (the item itself when absent).
`empty_label` adds a first option whose value is `""`.

### `target` and `range`

`catalog` names a var holding a list of targets. The form reads these keys of
an entry. Every other key is the sequence's own, passed through unchanged:

- `id`, `device_label` (the Device dropdown), `param_label` (the Parameter
  dropdown), `device`
- `lock_label` (optional): shown as "waits for …" next to the live value
- `unit` (the base unit the vars use), `display_unit`/`display_scale` and
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
matching `get_X` and takes either a value, or `channel` and a value. An
Other target is sent as an entry with `kind: generic`, `device`,
`set_action`, `get_action`, `value_param`, `channel_param` (`null` when there
is none), `channel` and raw units. `other_defaults` adds the sequence's own
keys to every Other entry (e.g. a settle time). It never replaces the keys
above. A sequence that accepts Other targets calls them with templated
parameter names (`docs/sequencer_config.md`, `call`):

```yaml
- call:
    device: "${vars.target.device}"
    action: "${vars.target.set_action}"
    params: {"${vars.target.value_param}": "${value}"}
```

`range` sets `relative` (bool), `from`/`to` (base units: offsets when
relative, absolute values otherwise) and `points`. Choosing a new target
resets its range to ±`default_span`/2 around the current value. The sequence
generates the values itself, e.g.
`gen: {linspace: {start: "${origin + range_from}", stop: "${origin + range_to}", num: "${range_points}"}}`.
