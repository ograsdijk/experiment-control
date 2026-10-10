import { Autocomplete } from "@mantine/core";
import { useState } from "react";
import { useDraftValue } from "../useDraftValue";

type Props = {
  /** Field label (omit for inline/unstyled usage where a label is rendered elsewhere). */
  label?: string;
  value: string;
  /** Suggestions; the field is free-text, so any typed value is allowed. */
  options: string[];
  onChange: (value: string) => void;
  placeholder?: string;
  error?: string;
  ariaLabel?: string;
  disabled?: boolean;
};

/**
 * Searchable free-text field used across the sequencer step editors for
 * device/action/signal/field selection. Suggestions narrow as you type, but the
 * value is whatever is typed — so offline/federated devices and ${template}
 * names are preserved rather than blanked by a strict dropdown. Typed text is
 * written back on blur or Enter (see `useDraftValue`); picking a suggestion
 * writes it immediately.
 */
export function FieldAutocomplete({
  label,
  value,
  options,
  onChange,
  placeholder,
  error,
  ariaLabel,
  disabled,
}: Props) {
  const [dropdownOpened, setDropdownOpened] = useState(false);
  const draft = useDraftValue(value, onChange);
  return (
    <Autocomplete
      size="xs"
      label={label}
      aria-label={ariaLabel}
      placeholder={placeholder}
      data={options}
      value={draft.text}
      onChange={draft.edit}
      onOptionSubmit={(option) => draft.commit(option)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          draft.commit();
        } else if (event.key === "Escape" && draft.editing) {
          draft.cancel();
          event.stopPropagation();
        }
      }}
      error={error}
      disabled={disabled}
      dropdownOpened={dropdownOpened}
      onFocus={() => setDropdownOpened(true)}
      onClick={() => setDropdownOpened(true)}
      onBlur={() => {
        setDropdownOpened(false);
        draft.commit();
      }}
      limit={50}
      comboboxProps={{ withinPortal: true, zIndex: 10000 }}
    />
  );
}
