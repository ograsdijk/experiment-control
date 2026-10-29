import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Chip,
  Code,
  Collapse,
  Group,
  Modal,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { callDevice, fetchCapabilities } from "../api";
import type { TelemetrySignal } from "../types";
import {
  buildRunOverrides,
  catalogTargets,
  formatNumber,
  initialFormValues,
  optionsFromCallResult,
  optionsOf,
  otherTargetsFor,
  rangeProblems,
  rangeValues,
  summaryFigures,
  type ParsedRunForm,
  type RunFormField,
  type RunFormOption,
  type ScanTarget,
} from "../features/sequencer/run_form";

type Props = {
  opened: boolean;
  onClose: () => void;
  parsed: ParsedRunForm;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  busy: boolean;
  onStart: (overrides: Record<string, unknown>) => Promise<unknown> | void;
};

type Values = Record<string, unknown>;

const OTHER_PREFIX = "other:";

function num(value: unknown, fallback = 0): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function optionKey(value: unknown): string {
  return JSON.stringify(value);
}

/** The "Run with form" dialog for a sequence with a `form:` block. */
export function SequencerRunFormModal({
  opened,
  onClose,
  parsed,
  latestSignalsByDevice,
  busy,
  onStart,
}: Props) {
  const { form, vars } = parsed;
  const [values, setValues] = useState<Values>(() => initialFormValues(parsed));
  const [showOverrides, setShowOverrides] = useState(false);
  useEffect(() => {
    if (opened) {
      setValues(initialFormValues(parsed));
    }
  }, [opened, parsed]);

  const set = (patch: Values) => setValues((prev) => ({ ...prev, ...patch }));
  const enabled = (field: RunFormField) => !field.enabled_by || values[field.enabled_by] === true;

  const targetField = form.sections
    .flatMap((section) => section.fields)
    .find((field): field is Extract<RunFormField, { type: "target" }> => field.type === "target");
  const rangeField = form.sections
    .flatMap((section) => section.fields)
    .find((field): field is Extract<RunFormField, { type: "range" }> => field.type === "range");
  const target = (targetField ? values[targetField.var] : null) as ScanTarget | null;

  const origin = useLiveValue(target, latestSignalsByDevice);
  const scanValues =
    rangeField && target
      ? rangeValues(
          values[rangeField.relative] === true,
          num(values[rangeField.from]),
          num(values[rangeField.to]),
          num(values[rangeField.points], 1),
          origin
        )
      : null;
  const problems = rangeField ? rangeProblems(target, scanValues) : [];
  // Summary vars the form doesn't set keep the sequence's own values.
  const figures = summaryFigures(form.summary, { ...vars, ...values });
  const overrides = useMemo(() => buildRunOverrides(form, values), [form, values]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={form.title ?? "Run with form"}
      size="lg"
      zIndex={500}
    >
      <Stack gap="md">
        {form.presets.length > 0 && (
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              Presets
            </Text>
            {form.presets.map((preset) => (
              <Button
                key={preset.label}
                size="compact-xs"
                variant="default"
                onClick={() => set(preset.set)}
              >
                {preset.label}
              </Button>
            ))}
          </Group>
        )}

        {form.sections.map((section, index) => {
          const sectionOn = !section.toggle || values[section.toggle] === true;
          return (
            <Paper key={section.title ?? index} withBorder p="sm" radius="md">
              <Group justify="space-between" mb="xs">
                <Text fw={500} size="sm">
                  {section.title}
                </Text>
                {section.toggle && (
                  <Checkbox
                    size="xs"
                    label="Enabled"
                    checked={values[section.toggle] === true}
                    onChange={(event) =>
                      set({ [section.toggle as string]: event.currentTarget.checked })
                    }
                  />
                )}
              </Group>
              <Stack gap="xs" style={{ opacity: sectionOn ? 1 : 0.45 }}>
                {section.fields.map((field, fieldIndex) => (
                  <FormField
                    key={fieldIndex}
                    field={field}
                    values={values}
                    vars={vars}
                    disabled={!sectionOn || !enabled(field)}
                    onChange={set}
                    latestSignalsByDevice={latestSignalsByDevice}
                    target={target}
                    origin={origin}
                    scanValues={scanValues}
                    rangeField={rangeField ?? null}
                  />
                ))}
              </Stack>
            </Paper>
          );
        })}

        {problems.length > 0 && (
          <Alert color="red" variant="light" p="xs">
            {problems.join(". ")}.
          </Alert>
        )}

        <SimpleGrid cols={3} spacing="xs">
          {figures.map((figure) => (
            <Stat
              key={figure.label}
              label={figure.label}
              value={figure.value === null ? "–" : figure.value.toLocaleString()}
            />
          ))}
          <Stat
            label="Start value"
            value={
              origin === null || !target
                ? "read at start"
                : `${formatNumber(origin / (num(target.display_scale, 1) || 1))} ${target.display_unit}`
            }
          />
        </SimpleGrid>

        <Group justify="space-between">
          <Button size="xs" variant="subtle" onClick={() => setShowOverrides((v) => !v)}>
            {showOverrides ? "Hide" : "Show"} values sent
          </Button>
          <Group gap="xs">
            <Button size="xs" variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="xs"
              color="teal"
              loading={busy}
              disabled={problems.length > 0}
              onClick={async () => {
                // A failed start keeps the dialog, and its choices, open.
                if ((await onStart(overrides)) === true) {
                  onClose();
                }
              }}
            >
              Start scan
            </Button>
          </Group>
        </Group>
        <Collapse in={showOverrides}>
          <Text size="xs" c="dimmed" mb={4}>
            Sent as the run's overrides and recorded with it; the YAML is unchanged.
          </Text>
          {showOverrides && (
            <Code block style={{ maxHeight: 240, overflow: "auto" }}>
              {JSON.stringify(overrides, null, 2)}
            </Code>
          )}
        </Collapse>
      </Stack>
    </Modal>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Paper bg="var(--mantine-color-default-hover)" p="xs" radius="sm">
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" fw={500}>
        {value}
      </Text>
    </Paper>
  );
}

/** The target's live value in base units, or null when unknown. */
function useLiveValue(
  target: ScanTarget | null,
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>
): number | null {
  const [called, setCalled] = useState<{ id: string; value: number | null } | null>(null);
  const current = target?.current ?? null;
  const fromTelemetry = current
    ? latestSignalsByDevice[current.device]?.[current.signal]?.value
    : undefined;
  const isOther = target?.kind === "generic";
  const targetId = target?.id ?? "";
  const channel = target?.channel;
  useEffect(() => {
    if (!isOther || !target || typeof target.get_action !== "string") {
      return;
    }
    let cancelled = false;
    const params: Record<string, unknown> = {};
    if (typeof target.channel_param === "string") {
      params[target.channel_param] = target.channel;
    }
    void callDevice(target.device, target.get_action, params).then((resp) => {
      if (!cancelled) {
        const value = Number(resp.ok ? resp.result : NaN);
        setCalled({ id: targetId, value: Number.isFinite(value) ? value : null });
      }
    });
    return () => {
      cancelled = true;
    };
    // Re-read when the target or its channel changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOther, targetId, channel]);
  if (isOther) {
    return called?.id === targetId ? called.value : null;
  }
  if (typeof fromTelemetry !== "number" || !Number.isFinite(fromTelemetry)) {
    return null;
  }
  return current?.abs ? Math.abs(fromTelemetry) : fromTelemetry;
}

type FieldProps = {
  field: RunFormField;
  values: Values;
  vars: Record<string, unknown>;
  disabled: boolean;
  onChange: (patch: Values) => void;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  target: ScanTarget | null;
  origin: number | null;
  scanValues: number[] | null;
  /** The range a target change resets, centred on the new target. */
  rangeField: Extract<RunFormField, { type: "range" }> | null;
};

function FormField(props: FieldProps) {
  const { field, values, disabled, onChange } = props;
  switch (field.type) {
    case "checkbox":
      return (
        <Checkbox
          size="xs"
          label={field.label ?? field.var}
          disabled={disabled}
          checked={values[field.var] === true}
          onChange={(event) => onChange({ [field.var]: event.currentTarget.checked })}
        />
      );
    case "number":
    case "integer":
      return (
        <NumberInput
          size="xs"
          label={field.unit ? `${field.label ?? field.var} (${field.unit})` : field.label ?? field.var}
          disabled={disabled}
          min={field.min}
          max={field.max}
          step={field.type === "integer" ? 1 : field.step}
          allowDecimal={field.type !== "integer"}
          value={num(values[field.var])}
          onChange={(value) => {
            const n = num(value, num(values[field.var]));
            onChange({ [field.var]: field.type === "integer" ? Math.trunc(n) : n });
          }}
          maw={220}
        />
      );
    case "text":
      return (
        <TextInput
          size="xs"
          label={field.label ?? field.var}
          disabled={disabled}
          value={String(values[field.var] ?? "")}
          onChange={(event) => onChange({ [field.var]: event.currentTarget.value })}
          maw={320}
        />
      );
    case "select":
      return <SelectField {...props} field={field} />;
    case "chips":
      return <ChipsField {...props} field={field} />;
    case "target":
      return <TargetField {...props} field={field} />;
    case "range":
      return <RangeField {...props} field={field} />;
    default:
      return null;
  }
}

function SelectField({
  field,
  values,
  vars,
  disabled,
  onChange,
}: FieldProps & { field: Extract<RunFormField, { type: "select" }> }) {
  const [called, setCalled] = useState<RunFormOption[] | null>(null);
  const call = field.options_call;
  useEffect(() => {
    if (!call) {
      return;
    }
    let cancelled = false;
    void callDevice(call.device, call.action, call.params ?? {}).then((resp) => {
      if (!cancelled) {
        setCalled(resp.ok ? optionsFromCallResult(call, resp.result) : []);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [call]);
  const options = call ? called ?? [] : optionsOf(field.options ? vars[field.options] : null);
  const data = [
    ...(field.empty_label !== undefined ? [{ value: optionKey(""), label: field.empty_label }] : []),
    ...options.map((option) => ({ value: optionKey(option.value), label: option.label })),
  ];
  const current = optionKey(values[field.var] ?? "");
  if (!data.some((item) => item.value === current)) {
    data.push({ value: current, label: String(values[field.var]) });
  }
  return (
    <Select
      size="xs"
      label={field.label ?? field.var}
      disabled={disabled}
      data={data}
      value={current}
      allowDeselect={false}
      comboboxProps={{ zIndex: 600 }}
      onChange={(value) => value !== null && onChange({ [field.var]: JSON.parse(value) })}
      maw={320}
    />
  );
}

function ChipsField({
  field,
  values,
  vars,
  disabled,
  onChange,
}: FieldProps & { field: Extract<RunFormField, { type: "chips" }> }) {
  const options = optionsOf(vars[field.options]);
  const selected = Array.isArray(values[field.var]) ? (values[field.var] as unknown[]) : [];
  const keys = selected.map(optionKey);
  return (
    <Stack gap={4}>
      <Group gap={6}>
        <Text size="xs" fw={500}>
          {field.label ?? field.var}
        </Text>
        <Text size="xs" c="dimmed">
          {selected.length} of {options.length}
        </Text>
      </Group>
      <Chip.Group
        multiple
        value={keys}
        onChange={(next) =>
          // Keep the catalog's order, not the click order.
          onChange({
            [field.var]: options
              .filter((option) => next.includes(optionKey(option.value)))
              .map((option) => option.value),
          })
        }
      >
        <Group gap={6}>
          {options.map((option) => (
            <Chip key={optionKey(option.value)} value={optionKey(option.value)} size="xs" disabled={disabled}>
              {option.label}
            </Chip>
          ))}
        </Group>
      </Chip.Group>
    </Stack>
  );
}

function TargetField({
  field,
  values,
  vars,
  disabled,
  onChange,
  latestSignalsByDevice,
  target,
  origin,
  rangeField,
}: FieldProps & { field: Extract<RunFormField, { type: "target" }> }) {
  const catalog = useMemo(() => catalogTargets(vars[field.catalog]), [vars, field.catalog]);
  const [otherTargets, setOtherTargets] = useState<ScanTarget[]>([]);
  const deviceLabels = [...new Set(catalog.map((entry) => entry.device_label))];
  const hasTelemetry = (deviceId: string | undefined) =>
    Boolean(deviceId && Object.keys(latestSignalsByDevice[deviceId] ?? {}).length > 0);
  const catalogDevices = new Set(
    catalog.flatMap((entry) => [entry.device, entry.current?.device ?? ""])
  );
  const exclude = new Set(field.other_exclude ?? []);
  const otherDevices = Object.keys(latestSignalsByDevice)
    .filter((deviceId) => !catalogDevices.has(deviceId) && !exclude.has(deviceId))
    .sort();

  // An Other device chosen in the Device dropdown, before its parameters load.
  const [pendingOther, setPendingOther] = useState<string | null>(null);
  const selectedDevice = pendingOther
    ? `${OTHER_PREFIX}${pendingOther}`
    : target?.kind === "generic"
      ? `${OTHER_PREFIX}${target.device}`
      : target?.device_label ?? null;
  const otherDevice = selectedDevice?.startsWith(OTHER_PREFIX)
    ? selectedDevice.slice(OTHER_PREFIX.length)
    : null;
  useEffect(() => {
    if (!otherDevice) {
      setOtherTargets([]);
      return;
    }
    let cancelled = false;
    void fetchCapabilities(otherDevice).then((members) => {
      if (cancelled) {
        return;
      }
      const found = otherTargetsFor(otherDevice, members, field.other_defaults);
      setOtherTargets(found);
      if (pendingOther === otherDevice) {
        // A device without set_/get_ pairs can't be scanned: keep the old target.
        setPendingOther(null);
        if (found.length > 0) {
          choose(found[0]);
        } else {
          notifications.show({
            color: "yellow",
            title: `${otherDevice} has nothing to scan`,
            message: "It has no set_X action with a matching get_X.",
          });
        }
      }
    });
    return () => {
      cancelled = true;
    };
    // `choose` and `pendingOther` are read once the device's list arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [otherDevice]);

  const parameters = otherDevice
    ? otherTargets
    : catalog.filter((entry) => entry.device_label === selectedDevice);
  const choose = (entry: ScanTarget | undefined) => {
    if (!entry) {
      return;
    }
    // A new target starts from a range centred on its current value.
    const patch: Values = { [field.var]: entry };
    const range = rangeField?.target === field.var ? rangeField : null;
    if (range) {
      patch[range.relative] = true;
      patch[range.from] = -num(entry.default_span, 1) / 2;
      patch[range.to] = num(entry.default_span, 1) / 2;
    }
    onChange(patch);
  };
  const deviceData = [
    {
      group: "Listed targets",
      items: deviceLabels.map((label) => {
        const live = catalog
          .filter((entry) => entry.device_label === label)
          .some((entry) => hasTelemetry(entry.current?.device ?? entry.device));
        return { value: label, label: live ? label : `${label} (no telemetry)` };
      }),
    },
    ...(otherDevices.length > 0
      ? [
          {
            group: "Other (set_/get_ pairs, raw units)",
            items: otherDevices.map((deviceId) => ({
              value: `${OTHER_PREFIX}${deviceId}`,
              label: deviceId,
            })),
          },
        ]
      : []),
  ];

  return (
    <Stack gap="xs">
      <SimpleGrid cols={2} spacing="xs">
        <Select
          size="xs"
          label="Device"
          disabled={disabled}
          data={deviceData}
          value={selectedDevice}
          allowDeselect={false}
          searchable
          comboboxProps={{ zIndex: 600 }}
          onChange={(value) => {
            if (!value) {
              return;
            }
            if (value.startsWith(OTHER_PREFIX)) {
              setPendingOther(value.slice(OTHER_PREFIX.length));
              return;
            }
            setPendingOther(null);
            choose(catalog.find((entry) => entry.device_label === value));
          }}
        />
        <Select
          size="xs"
          label="Parameter"
          disabled={disabled || parameters.length === 0}
          data={parameters.map((entry) => ({ value: entry.id, label: entry.param_label }))}
          value={target?.id ?? null}
          allowDeselect={false}
          comboboxProps={{ zIndex: 600 }}
          onChange={(value) => choose(parameters.find((entry) => entry.id === value))}
        />
      </SimpleGrid>
      {target?.kind === "generic" && typeof target.channel_param === "string" && (
        <NumberInput
          size="xs"
          label="Channel"
          disabled={disabled}
          value={num(target.channel, 1)}
          step={1}
          allowDecimal={false}
          onChange={(value) =>
            onChange({ [field.var]: { ...target, channel: Math.trunc(num(value, 1)) } })
          }
          maw={120}
        />
      )}
      {target && (
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            Current
          </Text>
          <Text size="sm" ff="monospace">
            {origin === null
              ? "unknown"
              : `${formatNumber(origin / num(target.display_scale, 1))} ${target.display_unit}`}
          </Text>
          {target.kind === "generic" ? (
            <Badge size="xs" color="yellow" variant="light">
              raw units
            </Badge>
          ) : typeof target.lock_label === "string" && target.lock_label ? (
            <Badge size="xs" variant="light">
              waits for {target.lock_label}
            </Badge>
          ) : null}
        </Group>
      )}
    </Stack>
  );
}

function RangeField({
  field,
  values,
  disabled,
  onChange,
  target,
  origin,
  scanValues,
}: FieldProps & { field: Extract<RunFormField, { type: "range" }> }) {
  const relative = values[field.relative] === true;
  const from = num(values[field.from]);
  const to = num(values[field.to]);
  // Start symmetric only when the sequence's range already is.
  const [symmetric, setSymmetric] = useState(
    () => Math.abs(from + to) <= Math.abs(to - from) * 1e-9
  );
  const scale = num(relative ? target?.offset_scale : target?.display_scale, 1) || 1;
  const unit = (relative ? target?.offset_unit : target?.display_unit) ?? "";
  const show = (base: number) => Number(formatNumber(base / scale));

  const switchMode = (nextRelative: boolean) => {
    if (nextRelative === relative) {
      return;
    }
    // Keep the same absolute endpoints when the start value is known.
    if (origin !== null) {
      const shift = nextRelative ? -origin : origin;
      onChange({ [field.relative]: nextRelative, [field.from]: from + shift, [field.to]: to + shift });
    } else {
      onChange({ [field.relative]: nextRelative });
    }
  };

  const first = scanValues?.length ? Math.min(...scanValues) : null;
  const last = scanValues?.length ? Math.max(...scanValues) : null;
  const points = Math.max(1, Math.trunc(num(values[field.points], 1)));
  const displayScale = num(target?.display_scale, 1) || 1;
  const offsetScale = num(target?.offset_scale, 1) || 1;
  return (
    <Stack gap="xs">
      <Group gap="sm">
        <SegmentedControl
          size="xs"
          disabled={disabled}
          value={relative ? "rel" : "abs"}
          onChange={(value) => switchMode(value === "rel")}
          data={[
            { value: "abs", label: "Absolute range" },
            { value: "rel", label: "From current setpoint" },
          ]}
        />
        {relative && (
          <Checkbox
            size="xs"
            label="Symmetric (± span)"
            disabled={disabled}
            checked={symmetric}
            onChange={(event) => {
              const next = event.currentTarget.checked;
              setSymmetric(next);
              if (next) {
                const half = (Math.abs(from) + Math.abs(to)) / 2;
                onChange({ [field.from]: -half, [field.to]: half });
              }
            }}
          />
        )}
      </Group>
      <SimpleGrid cols={3} spacing="xs">
        {relative && symmetric ? (
          <NumberInput
            size="xs"
            label={`± span (${unit})`}
            disabled={disabled}
            value={show((to - from) / 2)}
            onChange={(value) => {
              const half = Math.abs(num(value)) * scale;
              onChange({ [field.from]: -half, [field.to]: half });
            }}
          />
        ) : (
          <>
            <NumberInput
              size="xs"
              label={`${relative ? "Offset from" : "Start"} (${unit})`}
              disabled={disabled}
              value={show(from)}
              onChange={(value) => onChange({ [field.from]: num(value) * scale })}
            />
            <NumberInput
              size="xs"
              label={`${relative ? "Offset to" : "Stop"} (${unit})`}
              disabled={disabled}
              value={show(to)}
              onChange={(value) => onChange({ [field.to]: num(value) * scale })}
            />
          </>
        )}
        <NumberInput
          size="xs"
          label="Points"
          disabled={disabled}
          min={1}
          step={1}
          allowDecimal={false}
          value={points}
          onChange={(value) => onChange({ [field.points]: Math.max(1, Math.trunc(num(value, 1))) })}
        />
      </SimpleGrid>
      <Text size="xs" c="dimmed" ff="monospace">
        {first !== null && last !== null
          ? `${formatNumber(first / displayScale)} → ${formatNumber(last / displayScale)} ${
              target?.display_unit ?? ""
            } · step ${
              points > 1 ? formatNumber((last - first) / (points - 1) / offsetScale, 6) : "–"
            } ${target?.offset_unit ?? ""}`
          : "Relative to the value read when the scan starts"}
        {relative && first !== null ? " · centre read again when the scan starts" : ""}
      </Text>
    </Stack>
  );
}
