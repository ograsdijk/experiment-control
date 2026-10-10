import {
  RangeSet,
  StateEffect,
  StateField,
  type EditorState,
  type Extension,
} from "@codemirror/state";
import { Decoration, EditorView, GutterMarker, gutter, type DecorationSet } from "@codemirror/view";
import type { ActiveStep, LineRange } from "./active_step";

/** What the editor needs to draw: line ranges, no step ids. */
export type ActiveStepRange = Pick<ActiveStep, "highlight" | "bars">;

/** Stable key, so a new but equal range does not dispatch again. */
export function activeRangeKey(range: ActiveStepRange | null): string {
  if (!range) {
    return "";
  }
  return [range.highlight, ...range.bars].map((r) => `${r.from}-${r.to}`).join(",");
}

export const setActiveStepRange = StateEffect.define<ActiveStepRange | null>();

class ActiveMarker extends GutterMarker {
  constructor(
    readonly arrow: boolean,
    readonly bar: boolean
  ) {
    super();
  }
  eq(other: GutterMarker): boolean {
    return other instanceof ActiveMarker && other.arrow === this.arrow && other.bar === this.bar;
  }
  toDOM(): Node {
    const el = document.createElement("div");
    el.className = `cm-ec-active-gutter${this.bar ? " cm-ec-active-bar" : ""}`;
    el.textContent = this.arrow ? "▶" : "";
    return el;
  }
}

const lineDeco = Decoration.line({ class: "cm-ec-active-line" });

type ActiveFieldValue = {
  range: ActiveStepRange | null;
  decorations: DecorationSet;
  markers: RangeSet<GutterMarker>;
};

const EMPTY: ActiveFieldValue = {
  range: null,
  decorations: Decoration.none,
  markers: RangeSet.empty,
};

function clampLines(state: EditorState, r: LineRange): LineRange | null {
  const from = Math.max(1, r.from);
  const to = Math.min(state.doc.lines, r.to);
  return from > to ? null : { from, to };
}

function build(state: EditorState, range: ActiveStepRange | null): ActiveFieldValue {
  if (!range) {
    return EMPTY;
  }
  const highlight = clampLines(state, range.highlight);
  const bars = range.bars
    .map((bar) => clampLines(state, bar))
    .filter((bar): bar is LineRange => bar !== null);
  const decos = [];
  const markers = [];
  if (highlight) {
    for (let n = highlight.from; n <= highlight.to; n += 1) {
      decos.push(lineDeco.range(state.doc.line(n).from));
    }
  }
  const first = Math.min(highlight?.from ?? Infinity, ...bars.map((bar) => bar.from));
  const last = Math.max(highlight?.to ?? 0, ...bars.map((bar) => bar.to));
  for (let n = first; n <= last && n <= state.doc.lines; n += 1) {
    const arrow = highlight !== null && n === highlight.from;
    const bar = bars.some((b) => n >= b.from && n <= b.to);
    if (arrow || bar) {
      markers.push(new ActiveMarker(arrow, bar).range(state.doc.line(n).from));
    }
  }
  return {
    range,
    decorations: Decoration.set(decos),
    markers: RangeSet.of(markers),
  };
}

const activeField = StateField.define<ActiveFieldValue>({
  create: () => EMPTY,
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setActiveStepRange)) {
        return build(tr.state, effect.value);
      }
    }
    // Lines are of the loaded text; the owner clears the range when the text
    // is edited, so just stay inside the document meanwhile.
    return tr.docChanged && value.range ? build(tr.state, value.range) : value;
  },
  provide: (field) => EditorView.decorations.from(field, (v) => v.decorations),
});

/** Line highlight plus gutter markers for the running step. */
export function activeStepExtension(): Extension {
  return [
    activeField,
    gutter({
      class: "cm-ec-active-gutters",
      markers: (view) => view.state.field(activeField).markers,
      initialSpacer: () => new ActiveMarker(false, false),
    }),
  ];
}

/** Theme for the above, in the editor's light/dark scheme. */
export function activeStepTheme(isDark: boolean): Extension {
  const accent = isDark ? "#51cf66" : "#2f9e44";
  return EditorView.theme({
    ".cm-ec-active-line": {
      background: isDark ? "rgba(81, 207, 102, 0.14)" : "rgba(47, 158, 68, 0.12)",
    },
    ".cm-ec-active-gutter": {
      width: "12px",
      textAlign: "center",
      fontSize: "9px",
      color: accent,
      boxSizing: "border-box",
    },
    ".cm-ec-active-bar": {
      borderLeft: `2px solid ${isDark ? "rgba(81, 207, 102, 0.45)" : "rgba(47, 158, 68, 0.4)"}`,
    },
  });
}
