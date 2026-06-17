import { Autocomplete } from "@mantine/core";

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
 * names are preserved rather than blanked by a strict dropdown.
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
  return (
    <Autocomplete
      size="xs"
      label={label}
      aria-label={ariaLabel}
      placeholder={placeholder}
      data={options}
      value={value}
      onChange={onChange}
      error={error}
      disabled={disabled}
      limit={50}
      comboboxProps={{ withinPortal: false }}
    />
  );
}
