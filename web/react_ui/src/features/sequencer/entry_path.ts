// Dotted entry names (`freq_hz.call.device`) are how the inspector edits a
// nested mapping in flat rows. A key that itself contains a dot (or starts with
// a quote) is written as a JSON-quoted segment (`"a.b".c`) so it reads back as
// one key instead of being split into nested maps.

/** One path segment, quoted only when it would not read back as itself. */
export function entryPathSegment(key: string): string {
  return key.includes(".") || key.startsWith('"') ? JSON.stringify(key) : key;
}

/** Split a dotted entry name into keys, honouring quoted segments; empty keys dropped. */
export function splitEntryPath(name: string): string[] {
  const parts: string[] = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < name.length; i += 1) {
    const char = name[i];
    if (quoted) {
      current += char;
      if (char === "\\" && i + 1 < name.length) {
        current += name[i + 1];
        i += 1;
      } else if (char === '"') {
        quoted = false;
      }
    } else if (char === ".") {
      parts.push(current);
      current = "";
    } else {
      if (char === '"' && current.trim() === "") {
        quoted = true;
      }
      current += char;
    }
  }
  parts.push(current);
  return parts.map(unquoteSegment).filter((part) => part.length > 0);
}

function unquoteSegment(segment: string): string {
  const trimmed = segment.trim();
  if (trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      return String(JSON.parse(trimmed));
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  return trimmed;
}
