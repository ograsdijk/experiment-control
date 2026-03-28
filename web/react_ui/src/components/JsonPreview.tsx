type JsonTokenKind =
  | "plain"
  | "key"
  | "string"
  | "number"
  | "bool"
  | "null"
  | "punctuation";

type JsonToken = {
  text: string;
  kind: JsonTokenKind;
};

type JsonPreviewProps = {
  text: string;
  colorScheme: "light" | "dark";
};

const JSON_TOKEN_RE =
  /"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\],:]/g;

function isJsonStringKey(line: string, tokenEnd: number): boolean {
  let idx = tokenEnd;
  while (idx < line.length && /\s/.test(line[idx])) {
    idx += 1;
  }
  return line[idx] === ":";
}

function tokenizeJsonLine(line: string, highlight: boolean): JsonToken[] {
  if (!highlight) {
    return [{ text: line, kind: "plain" }];
  }
  const out: JsonToken[] = [];
  JSON_TOKEN_RE.lastIndex = 0;
  let last = 0;
  while (true) {
    const match = JSON_TOKEN_RE.exec(line);
    if (!match) {
      break;
    }
    const start = match.index;
    const token = match[0];
    const end = start + token.length;
    if (start > last) {
      out.push({ text: line.slice(last, start), kind: "plain" });
    }
    let kind: JsonTokenKind = "plain";
    if (token.startsWith('"')) {
      kind = isJsonStringKey(line, end) ? "key" : "string";
    } else if (token === "true" || token === "false") {
      kind = "bool";
    } else if (token === "null") {
      kind = "null";
    } else if (
      token === "{" ||
      token === "}" ||
      token === "[" ||
      token === "]" ||
      token === "," ||
      token === ":"
    ) {
      kind = "punctuation";
    } else {
      kind = "number";
    }
    out.push({ text: token, kind });
    last = end;
  }
  if (last < line.length) {
    out.push({ text: line.slice(last), kind: "plain" });
  }
  return out.length > 0 ? out : [{ text: line, kind: "plain" }];
}

function jsonTokenColor(kind: JsonTokenKind, colorScheme: "light" | "dark"): string {
  const isDark = colorScheme === "dark";
  if (kind === "key") {
    return isDark ? "#74c0fc" : "#1c7ed6";
  }
  if (kind === "string") {
    return isDark ? "#8ce99a" : "#2b8a3e";
  }
  if (kind === "number") {
    return isDark ? "#ffa94d" : "#d9480f";
  }
  if (kind === "bool") {
    return isDark ? "#b197fc" : "#5f3dc4";
  }
  if (kind === "null") {
    return isDark ? "#faa2c1" : "#c2255c";
  }
  if (kind === "punctuation") {
    return isDark ? "#9aa4b2" : "#6c757d";
  }
  return "inherit";
}

function normalizeJsonText(text: string): { formatted: string; highlight: boolean } {
  const raw = String(text ?? "");
  const trimmed = raw.trim();
  if (!trimmed) {
    return { formatted: "{}", highlight: true };
  }
  try {
    const parsed = JSON.parse(trimmed);
    return { formatted: JSON.stringify(parsed, null, 2), highlight: true };
  } catch {
    return { formatted: raw, highlight: false };
  }
}

export function JsonPreview({ text, colorScheme }: JsonPreviewProps) {
  const normalized = normalizeJsonText(text);
  const lines = normalized.formatted.split("\n");
  return (
    <div
      style={{
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        fontSize: 12,
        lineHeight: 1.5,
        overflowX: "auto",
      }}
    >
      {lines.map((line, idx) => {
        const tokens = tokenizeJsonLine(line, normalized.highlight);
        return (
          <div
            key={`json-line-${idx + 1}`}
            style={{
              display: "grid",
              gridTemplateColumns: "44px 1fr",
              columnGap: 8,
              whiteSpace: "pre",
            }}
          >
            <span
              style={{
                textAlign: "right",
                userSelect: "none",
                color: colorScheme === "dark" ? "#7f8a99" : "#8a8f96",
              }}
            >
              {idx + 1}
            </span>
            <code style={{ whiteSpace: "pre", wordBreak: "break-word" }}>
              {tokens.map((token, tokenIdx) => (
                <span
                  key={`json-token-${idx + 1}-${tokenIdx}`}
                  style={{ color: jsonTokenColor(token.kind, colorScheme) }}
                >
                  {token.text}
                </span>
              ))}
            </code>
          </div>
        );
      })}
    </div>
  );
}
