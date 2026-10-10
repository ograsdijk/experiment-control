import { isScalar, parseDocument } from "yaml";

function parseScalar(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    const doc = parseDocument(text);
    if (doc.errors.length > 0 || !isScalar(doc.contents)) {
      return { ok: false };
    }
    return { ok: true, value: doc.contents.value };
  } catch {
    return { ok: false };
  }
}

/**
 * Text to show in an editor field for a value held as YAML source.
 *
 * Fields hand typed text to the writers, which parse it as YAML and let the
 * serializer add whatever quoting the surrounding context needs. Showing the
 * raw source instead (e.g. `"${ch}"` inside a flow map) put those quotes in
 * the field, where the next edit wrapped them in another layer. So a quoted
 * string is shown bare when the bare text reads back as the same string;
 * quotes stay only where they carry meaning (the string `"5"`, not the
 * number). An empty string shows as an empty field.
 */
export function yamlSourceToDisplay(source: string | null | undefined): string {
  const text = source ?? "";
  const trimmed = text.trim();
  if (!trimmed.startsWith('"') && !trimmed.startsWith("'")) {
    return text;
  }
  const parsed = parseScalar(trimmed);
  if (!parsed.ok || typeof parsed.value !== "string") {
    return text;
  }
  const value = parsed.value;
  if (value === "") {
    return "";
  }
  if (value.includes("\n") || value !== value.trim()) {
    return text;
  }
  const bare = parseScalar(value);
  if (!bare.ok || bare.value !== value) {
    return text;
  }
  return value;
}
