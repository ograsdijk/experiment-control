import { NumberInput, Switch, TextInput } from "@mantine/core";
import type { CapabilityParam } from "../types";

export type ParamInputProps = {
  param: CapabilityParam;
  value: string;
  onChange: (value: string) => void;
};

export function coerceParamValue(raw: string, param: ParamInputProps["param"]) {
  const annotation = (param.annotation ?? "").toLowerCase();
  const defaultValue = param.default;
  const isInt =
    annotation.includes("int") ||
    (typeof defaultValue === "number" && Number.isInteger(defaultValue));
  const isFloat =
    annotation.includes("float") ||
    (typeof defaultValue === "number" && !Number.isInteger(defaultValue));
  if (annotation.includes("bool") || typeof defaultValue === "boolean") {
    return raw === "true" || raw === "1";
  }
  if (isInt) {
    const asNumber = Number(raw);
    if (Number.isFinite(asNumber)) {
      return Math.trunc(asNumber);
    }
  }
  if (isFloat || typeof defaultValue === "number") {
    const asNumber = Number(raw);
    if (Number.isFinite(asNumber)) {
      return asNumber;
    }
  }
  return raw;
}

export function ParamInput({ param, value, onChange }: ParamInputProps) {
  const annotation = (param.annotation ?? "").toLowerCase();
  const isBool = annotation.includes("bool");
  const isInt =
    annotation.includes("int") ||
    (typeof param.default === "number" && Number.isInteger(param.default));
  const isFloat =
    annotation.includes("float") ||
    (typeof param.default === "number" && !Number.isInteger(param.default));

  if (isBool) {
    return (
      <Switch
        label={param.name}
        checked={value === "true" || value === "1"}
        onChange={(event) => onChange(event.currentTarget.checked ? "true" : "false")}
      />
    );
  }

  if (isFloat) {
    return (
      <TextInput
        label={param.name}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder="e.g. 1.759e9"
        inputMode="decimal"
      />
    );
  }

  if (isInt) {
    return (
      <NumberInput
        label={param.name}
        value={value === "" ? undefined : Number(value)}
        onChange={(val) => onChange(val === "" || val === null ? "" : String(val))}
      />
    );
  }

  return (
    <TextInput
      label={param.name}
      value={value}
      onChange={(event) => onChange(event.currentTarget.value)}
      placeholder={param.required ? "required" : "optional"}
    />
  );
}
