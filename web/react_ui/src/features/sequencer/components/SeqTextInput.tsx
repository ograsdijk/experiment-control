import { TextInput, type TextInputProps } from "@mantine/core";
import { useDraftValue } from "../useDraftValue";
import { ValueHint } from "./ValueChip";

type Props = Omit<TextInputProps, "value" | "onChange" | "defaultValue"> & {
  /** Current value as YAML source text (null/undefined shows empty). */
  value: string | null | undefined;
  /** Called with the edited text once, on blur or Enter (not per keystroke). */
  onChange: (value: string) => void;
  /** Unit shown after a resolved `${...}` value (e.g. "s" for durations). */
  valueUnit?: string;
};

/**
 * Text field for sequencer step/var values. Typing stays in a local draft and
 * is written back on blur or Enter, so half-typed text (`-`, `[1,`, `a:`)
 * never reaches the YAML; Escape discards the draft. See
 * `yamlSourceToDisplay` for how quoted values are shown.
 */
export function SeqTextInput({ value, onChange, onBlur, onKeyDown, valueUnit, ...rest }: Props) {
  const draft = useDraftValue(value, onChange);
  // The resolved value of a `${...}` goes under the input; the text stays as written.
  const hint = draft.text.includes("${") ? <ValueHint text={draft.text} unit={valueUnit} /> : null;
  return (
    <TextInput
      {...rest}
      description={
        hint && rest.description ? (
          <>
            {rest.description}
            {hint}
          </>
        ) : (
          hint ?? rest.description
        )
      }
      inputWrapperOrder={hint ? ["label", "input", "description", "error"] : rest.inputWrapperOrder}
      value={draft.text}
      onChange={(event) => draft.edit(event.currentTarget.value)}
      onBlur={(event) => {
        draft.commit();
        onBlur?.(event);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          draft.commit();
        } else if (event.key === "Escape" && draft.editing) {
          draft.cancel();
          event.stopPropagation();
        }
        onKeyDown?.(event);
      }}
    />
  );
}
