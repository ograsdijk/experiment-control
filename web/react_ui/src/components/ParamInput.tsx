import { NumberInput, Select, Switch, TextInput } from "@mantine/core";
import type { CapabilityParam } from "../types";

export type ParamInputProps = {
  param: CapabilityParam;
  value: string;
  onChange: (value: string) => void;
};

const INTEGER_LITERAL_RE = /^(?:typing\.)?literal\[\s*[+-]?\d+(?:\s*,\s*[+-]?\d+)*\s*\]$/i;

export function integerLiteralValues(annotation: string | null | undefined): number[] | null {
  const normalized = (annotation ?? "").trim();
  if (!INTEGER_LITERAL_RE.test(normalized)) return null;
  const open = normalized.indexOf("[");
  const values = normalized
    .slice(open + 1, -1)
    .split(",")
    .map((part) => Number(part.trim()));
  return values.every((value) => Number.isInteger(value)) ? values : null;
}

export function hasIntegerAnnotation(annotation: string | null | undefined) {
  const normalized = (annotation ?? "").trim().toLowerCase();
  return normalized.includes("int") || integerLiteralValues(annotation) !== null;
}

export function coerceParamValue(raw: string, param: ParamInputProps["param"]) {
  const annotation = (param.annotation ?? "").toLowerCase();
  const defaultValue = param.default;
  const integerLiteral = integerLiteralValues(param.annotation);
  const hasBoolAnnotation = annotation.includes("bool");
  const hasFloatAnnotation = annotation.includes("float");
  const hasIntAnnotation = hasIntegerAnnotation(param.annotation);
  const defaultIsNumber = typeof defaultValue === "number";
  const defaultIsFloat = defaultIsNumber && !Number.isInteger(defaultValue);
  const defaultIsInt = defaultIsNumber && Number.isInteger(defaultValue);
  const isFloat = hasFloatAnnotation || defaultIsFloat;
  const isInt = !isFloat && (hasIntAnnotation || defaultIsInt);

  if (hasBoolAnnotation || typeof defaultValue === "boolean") {
    return raw === "true" || raw === "1";
  }
  if (isFloat) {
    const asNumber = Number(raw);
    if (Number.isFinite(asNumber)) {
      return asNumber;
    }
  }
  if (isInt) {
    const asNumber = Number(raw);
    if (
      Number.isInteger(asNumber) &&
      (integerLiteral === null || integerLiteral.includes(asNumber))
    ) {
      return asNumber;
    }
  }
  return raw;
}

export function ParamInput({ param, value, onChange }: ParamInputProps) {
  const annotation = (param.annotation ?? "").toLowerCase();
  const integerLiteral = integerLiteralValues(param.annotation);
  const isBool = annotation.includes("bool");
  const isFloat =
    annotation.includes("float") ||
    (typeof param.default === "number" && !Number.isInteger(param.default));
  const isInt =
    !isFloat &&
    (hasIntegerAnnotation(param.annotation) ||
      (typeof param.default === "number" && Number.isInteger(param.default)));

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

  if (integerLiteral !== null) {
    return (
      <Select
        label={param.name}
        data={integerLiteral.map((literal) => ({ value: String(literal), label: String(literal) }))}
        value={value || null}
        onChange={(selected) => onChange(selected ?? "")}
        allowDeselect={!param.required}
      />
    );
  }

  if (isInt) {
    return (
      <NumberInput
        label={param.name}
        value={value === "" ? undefined : Number(value)}
        onChange={(val) => onChange(val === "" || val === null ? "" : String(val))}
        allowDecimal={false}
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
