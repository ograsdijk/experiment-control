import { ScrollArea, Text } from "@mantine/core";

type YamlTokenKind =
  | "plain"
  | "key"
  | "string"
  | "number"
  | "bool"
  | "template"
  | "comment";

type YamlToken = {
  text: string;
  kind: YamlTokenKind;
};

type YamlPreviewProps = {
  text: string;
  colorScheme: "light" | "dark";
};

const VALUE_TOKEN_RE =
  /\$\{[^}]+\}|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:true|false|null)\b|\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g;

function findYamlCommentStart(line: string): number {
  let inSingle = false;
  let inDouble = false;
  let escaped = false;
  for (let idx = 0; idx < line.length; idx += 1) {
    const ch = line[idx];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\" && (inSingle || inDouble)) {
      escaped = true;
      continue;
    }
    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      continue;
    }
    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      continue;
    }
    if (ch === "#" && !inSingle && !inDouble) {
      return idx;
    }
  }
  return -1;
}

function tokenizeScalar(value: string): YamlToken[] {
  const out: YamlToken[] = [];
  VALUE_TOKEN_RE.lastIndex = 0;
  let last = 0;
  while (true) {
    const match = VALUE_TOKEN_RE.exec(value);
    if (!match) {
      break;
    }
    const start = match.index;
    const end = start + match[0].length;
    if (start > last) {
      out.push({ text: value.slice(last, start), kind: "plain" });
    }
    const token = match[0];
    let kind: YamlTokenKind = "plain";
    if (token.startsWith("${")) {
      kind = "template";
    } else if (token.startsWith("'") || token.startsWith('"')) {
      kind = "string";
    } else if (token === "true" || token === "false" || token === "null") {
      kind = "bool";
    } else {
      kind = "number";
    }
    out.push({ text: token, kind });
    last = end;
  }
  if (last < value.length) {
    out.push({ text: value.slice(last), kind: "plain" });
  }
  return out.length > 0 ? out : [{ text: value, kind: "plain" }];
}

function tokenizeYamlLine(line: string): YamlToken[] {
  const commentStart = findYamlCommentStart(line);
  const content = commentStart >= 0 ? line.slice(0, commentStart) : line;
  const comment = commentStart >= 0 ? line.slice(commentStart) : "";
  const out: YamlToken[] = [];

  const keyMatch = /^(\s*(?:-\s+)?)?([A-Za-z0-9_.-]+)(\s*:)(.*)$/.exec(content);
  if (keyMatch) {
    const prefix = keyMatch[1] ?? "";
    const key = keyMatch[2] ?? "";
    const colon = keyMatch[3] ?? "";
    const rest = keyMatch[4] ?? "";
    if (prefix) {
      out.push({ text: prefix, kind: "plain" });
    }
    out.push({ text: key, kind: "key" });
    out.push({ text: colon, kind: "plain" });
    out.push(...tokenizeScalar(rest));
  } else {
    out.push(...tokenizeScalar(content));
  }

  if (comment) {
    out.push({ text: comment, kind: "comment" });
  }
  return out;
}

function yamlTokenColor(kind: YamlTokenKind, colorScheme: "light" | "dark"): string {
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
  if (kind === "template") {
    return isDark ? "#faa2c1" : "#c2255c";
  }
  if (kind === "comment") {
    return isDark ? "#adb5bd" : "#868e96";
  }
  return "inherit";
}

export function YamlPreview({ text, colorScheme }: YamlPreviewProps) {
  const lines = text.length > 0 ? text.split("\n") : [""];
  return (
    <ScrollArea h={360}>
      <div
        style={{
          fontFamily:
            'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
          fontSize: 12,
          lineHeight: 1.5,
        }}
      >
        {lines.map((line, lineIdx) => {
          const tokens = tokenizeYamlLine(line);
          return (
            <div
              key={`yaml-line-${lineIdx + 1}`}
              style={{
                display: "grid",
                gridTemplateColumns: "56px 1fr",
                columnGap: 10,
                whiteSpace: "pre",
              }}
            >
              <Text
                size="xs"
                c="dimmed"
                style={{
                  textAlign: "right",
                  userSelect: "none",
                }}
              >
                {lineIdx + 1}
              </Text>
              <code style={{ whiteSpace: "pre" }}>
                {tokens.map((token, tokenIdx) => (
                  <span
                    key={`yaml-token-${lineIdx + 1}-${tokenIdx}`}
                    style={{ color: yamlTokenColor(token.kind, colorScheme) }}
                  >
                    {token.text}
                  </span>
                ))}
              </code>
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
