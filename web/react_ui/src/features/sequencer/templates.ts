/**
 * `${...}` templates in sequence YAML text: finding them, the text the
 * runtime sees after YAML decoding, and the top-level `vars:` block.
 */

import { isScalar, parseDocument, visit } from "yaml";
import { has } from "./records";

const TEMPLATE_RE = /\$\{([^}]+)\}/g;
const SIMPLE_REF_RE = /^\s*(?:vars\.)?([A-Za-z_]\w*)\s*$/;

/** A fresh global regexp for `${...}` (for `matchAll` with positions). */
export function templateRegExp(): RegExp {
  return new RegExp(TEMPLATE_RE.source, "g");
}

export type TemplateSegment =
  | { kind: "text"; text: string }
  | { kind: "template"; text: string; inner: string };

export function splitTemplates(text: string): TemplateSegment[] {
  const out: TemplateSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(TEMPLATE_RE)) {
    const start = match.index ?? 0;
    if (start > last) {
      out.push({ kind: "text", text: text.slice(last, start) });
    }
    out.push({ kind: "template", text: match[0], inner: match[1] });
    last = start + match[0].length;
  }
  if (last < text.length) {
    out.push({ kind: "text", text: text.slice(last) });
  }
  return out;
}

/** Every distinct `${...}` in the text, as written. */
export function extractTemplateExpressions(text: string): string[] {
  return Array.from(new Set(Array.from(text.matchAll(TEMPLATE_RE), (m) => m[0])));
}

/** The var a template refers to, for `${name}` and `${vars.name}`. */
export function simpleTemplateRef(inner: string): string | null {
  return SIMPLE_REF_RE.exec(inner)?.[1] ?? null;
}

/** The expression inside a template that is the whole text, else the text. */
export function templateInner(text: string): string {
  const parts = splitTemplates(text);
  return parts.length === 1 && parts[0].kind === "template" ? parts[0].inner.trim() : text;
}

/**
 * Template text as written in the YAML, mapped to the text the runtime sees
 * after YAML decoding, where they differ: escaped quotes in a double-quoted
 * string, or a template folded over two lines.
 */
export function decodedTemplates(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  let doc;
  try {
    doc = parseDocument(text);
  } catch {
    return out;
  }
  visit(doc, {
    Scalar(_key, node) {
      if (!isScalar(node) || typeof node.value !== "string" || !node.range) {
        return;
      }
      const raw = text.slice(node.range[0], node.range[1]).match(TEMPLATE_RE) ?? [];
      const decoded = node.value.match(TEMPLATE_RE) ?? [];
      if (raw.length !== decoded.length) {
        return;
      }
      raw.forEach((rawTemplate, index) => {
        if (rawTemplate !== decoded[index] && !has(out, rawTemplate)) {
          out[rawTemplate] = decoded[index];
        }
      });
    },
  });
  return out;
}

/**
 * Lines [start, end) of the top-level `vars:` block. It ends at the next line
 * that starts in the first column (another key, `---`, `...`), except
 * comments. Shared by the request and the YAML editor, so both agree.
 */
export function varsBlockRange(lines: ReadonlyArray<string>): { start: number; end: number } | null {
  const start = lines.findIndex((line) => /^vars\s*:/.test(line));
  if (start < 0) {
    return null;
  }
  let end = start + 1;
  while (end < lines.length && !(/^\S/.test(lines[end]) && !lines[end].startsWith("#"))) {
    end += 1;
  }
  return { start, end };
}

/** A YAML alias (`*name`): it needs its anchor, which may be outside the block. */
const YAML_ALIAS_RE = /(?:^|[\s[{,:-])\*[^\s,[\]{}]+/m;

/**
 * The part of the YAML text resolve_vars needs: the top-level `vars:` block
 * (parsing just that keeps the request cheap on a long sequence), or all of
 * it when the block uses an alias.
 */
export function extractVarsBlock(text: string): string {
  const lines = text.split(/\r?\n/);
  const range = varsBlockRange(lines);
  if (!range) {
    return "vars: {}\n";
  }
  const block = `${lines.slice(range.start, range.end).join("\n")}\n`;
  return YAML_ALIAS_RE.test(block) ? text : block;
}
