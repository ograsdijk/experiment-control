import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { lintGutter, setDiagnostics, type Diagnostic } from "@codemirror/lint";
import { EditorState, type Text as CmText } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  MatchDecorator,
  ViewPlugin,
  placeholder,
} from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { tags } from "@lezer/highlight";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ForwardedRef,
} from "react";
import type { SequencerDiagnostic, SequencerYamlEditorHandle } from "../types";
import { yamlTokenColor } from "../yaml_colors";

export type SequencerYamlCodeEditorProps = {
  value: string;
  onChange: (value: string) => void;
  colorScheme: "light" | "dark";
  diagnostics?: ReadonlyArray<SequencerDiagnostic>;
  /** Read-only view (the YAML tab's Preview) with the same highlighting and marks. */
  readOnly?: boolean;
};

/**
 * Editor marks for diagnostics that point at the current text. Stale ones
 * (computed for different text) are left out: their lines may be wrong.
 */
function toEditorDiagnostics(
  doc: CmText,
  diagnostics: ReadonlyArray<SequencerDiagnostic>
): Diagnostic[] {
  const out: Diagnostic[] = [];
  for (const diag of diagnostics) {
    if (diag.stale || diag.line == null || diag.line < 1 || diag.line > doc.lines) {
      continue;
    }
    const line = doc.line(diag.line);
    const indent = line.text.length - line.text.trimStart().length;
    const from =
      diag.column != null
        ? Math.min(line.to, line.from + Math.max(0, diag.column - 1))
        : line.from + indent;
    out.push({
      from,
      to: Math.max(from, line.to),
      severity: diag.severity,
      message: diag.message,
      source: diag.source ?? undefined,
    });
  }
  return out;
}

function SequencerYamlCodeEditorImpl(
  { value, onChange, colorScheme, diagnostics = [], readOnly = false }: SequencerYamlCodeEditorProps,
  ref: ForwardedRef<SequencerYamlEditorHandle>
) {
  const editorViewRef = useRef<EditorView | null>(null);
  const [view, setView] = useState<EditorView | null>(null);
  const isDark = colorScheme === "dark";

  const templateDecorator = useMemo(
    () =>
      new MatchDecorator({
        regexp: /\$\{[^}\n]+\}/g,
        decoration: Decoration.mark({ class: "cm-ec-template" }),
      }),
    []
  );
  const numberDecorator = useMemo(
    () =>
      new MatchDecorator({
        regexp: /\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,
        decoration: Decoration.mark({ class: "cm-ec-number" }),
      }),
    []
  );

  const extensions = useMemo(
    () => [
      yaml(),
      lintGutter(),
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      EditorView.lineWrapping,
      syntaxHighlighting(
        HighlightStyle.define([
          {
            tag: [tags.propertyName, tags.labelName, tags.attributeName],
            color: yamlTokenColor("key", colorScheme),
          },
          { tag: tags.string, color: yamlTokenColor("string", colorScheme) },
          {
            tag: [tags.number, tags.integer, tags.float],
            color: yamlTokenColor("number", colorScheme),
          },
          {
            tag: [tags.bool, tags.null, tags.atom],
            color: yamlTokenColor("bool", colorScheme),
          },
          { tag: tags.comment, color: yamlTokenColor("comment", colorScheme) },
        ])
      ),
      ViewPlugin.fromClass(
        class {
          decorations;
          constructor(view: EditorView) {
            this.decorations = templateDecorator.createDeco(view);
          }
          update(update: Parameters<typeof templateDecorator.updateDeco>[0]) {
            this.decorations = templateDecorator.updateDeco(update, this.decorations);
          }
        },
        { decorations: (v) => v.decorations }
      ),
      ViewPlugin.fromClass(
        class {
          decorations;
          constructor(view: EditorView) {
            this.decorations = numberDecorator.createDeco(view);
          }
          update(update: Parameters<typeof numberDecorator.updateDeco>[0]) {
            this.decorations = numberDecorator.updateDeco(update, this.decorations);
          }
        },
        { decorations: (v) => v.decorations }
      ),
      placeholder("Paste or upload sequence YAML"),
      EditorView.theme({
        "&": {
          height: "100%",
          fontSize: "12px",
        },
        ".cm-scroller": {
          overflow: "auto",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        },
        ".cm-gutters": {
          background: "transparent",
          borderRight: "none",
        },
        ".cm-lineNumbers .cm-gutterElement": {
          color: yamlTokenColor("comment", colorScheme),
        },
        ".cm-activeLineGutter": {
          background: "transparent",
        },
        ".cm-activeLine": {
          background: isDark ? "rgba(173, 181, 189, 0.08)" : "rgba(134, 142, 150, 0.08)",
        },
        ".cm-content": {
          minHeight: "100%",
          lineHeight: "1.5",
          // theme="none" leaves the caret black, invisible on the dark theme.
          caretColor: isDark ? "#e9ecef" : "#212529",
        },
        ".cm-ec-template": {
          color: yamlTokenColor("template", colorScheme),
        },
        ".cm-ec-number": {
          color: yamlTokenColor("number", colorScheme),
        },
        ".cm-cursor, .cm-dropCursor": {
          borderLeftColor: isDark ? "#e9ecef" : "#212529",
        },
        ".cm-selectionBackground, .cm-content ::selection": {
          background: isDark ? "rgba(116, 192, 252, 0.28)" : "rgba(28, 126, 214, 0.25)",
        },
      }),
    ],
    [isDark, numberDecorator, readOnly, templateDecorator]
  );

  useEffect(() => {
    if (!view) {
      return;
    }
    view.dispatch(setDiagnostics(view.state, toEditorDiagnostics(view.state.doc, diagnostics)));
  }, [view, diagnostics, value]);

  useImperativeHandle(
    ref,
    () => ({
      focus: () => {
        editorViewRef.current?.focus();
      },
      focusAtOffset: (offset: number) => {
        const view = editorViewRef.current;
        if (!view) {
          return;
        }
        const clamped = Math.max(0, Math.min(offset, view.state.doc.length));
        view.dispatch({
          selection: { anchor: clamped, head: clamped },
          scrollIntoView: true,
        });
        view.focus();
      },
    }),
    []
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <CodeMirror
        value={value}
        height="100%"
        theme="none"
        extensions={extensions}
        onChange={onChange}
        onCreateEditor={(created) => {
          editorViewRef.current = created;
          setView(created);
        }}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLine: true,
          highlightActiveLineGutter: true,
          foldGutter: true,
          indentOnInput: true,
        }}
      />
    </div>
  );
}

export const SequencerYamlCodeEditor = forwardRef(SequencerYamlCodeEditorImpl);
export default SequencerYamlCodeEditor;
