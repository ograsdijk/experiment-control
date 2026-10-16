/**
 * Resolved-value widgets for the YAML editor: a small chip after each
 * `${...}` and on the vars-block lines whose value is derived or overridden.
 * Widgets are drawn after the text and are not part of it, so editing,
 * copying and line numbers are unchanged.
 */
import { StateEffect, StateField, type Extension, type Text as CmText } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view";
import { VALUE_CHIP_COLORS, OVERRIDE_DOT_COLOR } from "./components/ValueChip";
import {
  describeTemplate,
  describeVarDefinition,
  templateRegExp,
  varsBlockRange,
  type ValueChip,
  type ValueContext,
} from "./resolved_values";

export const setValueContext = StateEffect.define<ValueContext | null>();

const valueContextField = StateField.define<ValueContext | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setValueContext)) {
        return effect.value;
      }
    }
    return value;
  },
});

class ValueChipWidget extends WidgetType {
  constructor(
    readonly chip: ValueChip,
    readonly definition: boolean
  ) {
    super();
  }

  private key(): string {
    const { chip } = this;
    return [chip.kind, chip.value, chip.formula, chip.overrideDot, chip.stale, this.definition, ...chip.detail].join("\u0000");
  }

  eq(other: ValueChipWidget): boolean {
    return other.key() === this.key();
  }

  toDOM(): HTMLElement {
    const { chip } = this;
    const el = document.createElement("span");
    el.className = `cm-ec-value cm-ec-value-${chip.kind}${chip.stale ? " cm-ec-value-stale" : ""}`;
    el.title = chip.detail.join("\n");
    el.setAttribute("aria-label", chip.detail.join(", "));
    if (chip.formula) {
      const fx = document.createElement("span");
      fx.className = "cm-ec-value-fx";
      fx.textContent = "ƒ";
      el.appendChild(fx);
    }
    if (chip.kind === "override" && this.definition) {
      el.appendChild(document.createTextNode(`${chip.value} override`));
    } else {
      el.appendChild(document.createTextNode(chip.value || "runtime"));
    }
    if (chip.overrideDot) {
      const dot = document.createElement("span");
      dot.className = "cm-ec-value-dot";
      dot.textContent = "●";
      el.appendChild(dot);
    }
    return el;
  }

  ignoreEvent(): boolean {
    return false;
  }
}

const VAR_DEF_RE = /^(\s+)([A-Za-z_]\w*)\s*:/;

/** Line numbers (1-based) of the top-level `vars:` block entries, by name. */
export function varDefinitionLines(doc: CmText): Map<number, string> {
  const out = new Map<number, string>();
  const lines = doc.toString().split("\n");
  const range = varsBlockRange(lines);
  if (!range) {
    return out;
  }
  let entryIndent: number | null = null;
  for (let index = range.start + 1; index < range.end; index += 1) {
    const def = VAR_DEF_RE.exec(lines[index]);
    if (def) {
      entryIndent ??= def[1].length;
      if (def[1].length === entryIndent) {
        out.set(index + 1, def[2]);
      }
    }
  }
  return out;
}

function buildDecorations(view: EditorView, defs: Map<number, string>): DecorationSet {
  const ctx = view.state.field(valueContextField);
  if (!ctx?.resolved) {
    return Decoration.none;
  }
  const doc = view.state.doc;
  const widgets: { pos: number; deco: Decoration }[] = [];
  for (const { from, to } of view.visibleRanges) {
    // Var definitions: a chip at the end of the line, instead of template chips.
    let line = doc.lineAt(from);
    for (;;) {
      const defName = defs.get(line.number);
      if (defName) {
        const chip = describeVarDefinition(defName, ctx);
        if (chip) {
          widgets.push({
            pos: line.to,
            deco: Decoration.widget({ widget: new ValueChipWidget(chip, true), side: 1 }),
          });
        }
      }
      if (line.to >= to || line.number >= doc.lines) {
        break;
      }
      line = doc.line(line.number + 1);
    }
    // Templates, matched on the text itself so one spanning lines is found too.
    const text = doc.sliceString(from, to);
    for (const match of text.matchAll(templateRegExp())) {
      const start = from + (match.index ?? 0);
      const end = start + match[0].length;
      if (defs.has(doc.lineAt(start).number)) {
        continue;
      }
      const chip = describeTemplate(match[0], match[1], ctx);
      if (chip) {
        widgets.push({
          pos: end,
          deco: Decoration.widget({ widget: new ValueChipWidget(chip, false), side: 1 }),
        });
      }
    }
  }
  return Decoration.set(
    widgets.map((w) => w.deco.range(w.pos)),
    true
  );
}

const valueChipsPlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    defs: Map<number, string>;

    constructor(view: EditorView) {
      this.defs = varDefinitionLines(view.state.doc);
      this.decorations = buildDecorations(view, this.defs);
    }

    update(update: ViewUpdate) {
      if (update.docChanged) {
        this.defs = varDefinitionLines(update.state.doc);
      }
      const contextChanged =
        update.startState.field(valueContextField) !== update.state.field(valueContextField);
      if (
        update.docChanged ||
        update.viewportChanged ||
        contextChanged
      ) {
        this.decorations = buildDecorations(update.view, this.defs);
      }
    }
  },
  { decorations: (v) => v.decorations }
);

export function valueChipsExtension(): Extension {
  return [valueContextField, valueChipsPlugin];
}

export function valueChipsTheme(): Extension {
  const kinds = Object.fromEntries(
    Object.entries(VALUE_CHIP_COLORS).map(([kind, colors]) => [
      `.cm-ec-value-${kind}`,
      { borderColor: colors.border, background: colors.background, color: colors.color },
    ])
  );
  return EditorView.theme({
    ".cm-ec-value": {
      display: "inline-block",
      marginLeft: "6px",
      padding: "0 5px",
      borderRadius: "4px",
      border: "1px solid",
      fontSize: "10.5px",
      lineHeight: "1.35",
      fontWeight: "600",
      verticalAlign: "1px",
      cursor: "help",
      userSelect: "none",
      fontFamily: "var(--mantine-font-family)",
    },
    ".cm-ec-value-live, .cm-ec-value-runtime": { fontStyle: "italic" },
    ".cm-ec-value-runtime": { borderStyle: "dashed" },
    ".cm-ec-value-stale": { opacity: "0.5" },
    // While typing, the cursor's line shows no chips, so they don't jump
    // around. CodeMirror marks that line; no rebuild on cursor moves.
    "&.cm-focused .cm-content[contenteditable='true'] .cm-activeLine .cm-ec-value": {
      display: "none",
    },
    ".cm-ec-value-fx": {
      fontFamily: "Georgia, serif",
      fontStyle: "italic",
      fontWeight: "700",
      marginRight: "3px",
    },
    ".cm-ec-value-dot": { color: OVERRIDE_DOT_COLOR, marginLeft: "4px" },
    ...kinds,
  });
}
