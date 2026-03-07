import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
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
  useImperativeHandle,
  useMemo,
  useRef,
  type ForwardedRef,
} from "react";
import type { SequencerYamlEditorHandle } from "../types";
import { yamlTokenColor } from "../yaml_colors";

export type SequencerYamlCodeEditorProps = {
  value: string;
  onChange: (value: string) => void;
  colorScheme: "light" | "dark";
};

function SequencerYamlCodeEditorImpl(
  { value, onChange, colorScheme }: SequencerYamlCodeEditorProps,
  ref: ForwardedRef<SequencerYamlEditorHandle>
) {
  const editorViewRef = useRef<EditorView | null>(null);
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
        },
        ".cm-ec-template": {
          color: yamlTokenColor("template", colorScheme),
        },
        ".cm-ec-number": {
          color: yamlTokenColor("number", colorScheme),
        },
        ".cm-selectionBackground, .cm-content ::selection": {
          background: isDark ? "rgba(116, 192, 252, 0.28)" : "rgba(28, 126, 214, 0.25)",
        },
      }),
    ],
    [isDark, numberDecorator, templateDecorator]
  );

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
        onCreateEditor={(view) => {
          editorViewRef.current = view;
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
