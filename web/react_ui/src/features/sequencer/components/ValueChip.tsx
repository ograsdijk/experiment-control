import { Text, Tooltip } from "@mantine/core";
import { createContext, Fragment, useContext, type CSSProperties, type ReactNode } from "react";
import {
  describeTemplate,
  splitTemplates,
  type ValueChip as ValueChipModel,
  type ValueChipKind,
  type ValueContext,
} from "../resolved_values";

/** Resolved values for the chips; null when values are hidden or unknown. */
export const SequencerValuesContext = createContext<ValueContext | null>(null);

export function useSequencerValues(): ValueContext | null {
  return useContext(SequencerValuesContext);
}

/** Chip colors, shared with the YAML editor's widgets (`cm-ec-value-*`). */
export const VALUE_CHIP_COLORS: Record<
  ValueChipKind,
  { border: string; background: string; color: string }
> = {
  vars: {
    border: "var(--card-border)",
    background: "var(--mantine-color-default-hover)",
    color: "var(--mantine-color-text)",
  },
  override: {
    border: "var(--mantine-color-orange-6)",
    background: "var(--mantine-color-orange-light)",
    color: "var(--mantine-color-orange-light-color)",
  },
  derived: {
    border: "var(--mantine-color-violet-5)",
    background: "var(--mantine-color-violet-light)",
    color: "var(--mantine-color-violet-light-color)",
  },
  runtime: {
    border: "var(--mantine-color-cyan-6)",
    background: "var(--mantine-color-cyan-light)",
    color: "var(--mantine-color-cyan-light-color)",
  },
  unknown: {
    border: "var(--mantine-color-yellow-6)",
    background: "var(--mantine-color-yellow-light)",
    color: "var(--mantine-color-yellow-light-color)",
  },
  live: {
    border: "var(--mantine-color-green-6)",
    background: "var(--mantine-color-green-light)",
    color: "var(--mantine-color-green-light-color)",
  },
  error: {
    border: "var(--mantine-color-red-6)",
    background: "var(--mantine-color-red-light)",
    color: "var(--mantine-color-red-light-color)",
  },
};

export const OVERRIDE_DOT_COLOR = "var(--mantine-color-orange-6)";

function ChipTooltip({ chip, children }: { chip: ValueChipModel; children: ReactNode }) {
  return (
    <Tooltip
      multiline
      withinPortal
      zIndex={1000}
      maw={420}
      label={chip.detail.join("\n")}
      style={{ whiteSpace: "pre-wrap", fontFamily: "var(--mantine-font-family-monospace)" }}
    >
      {children}
    </Tooltip>
  );
}

/** Name and value: `[ƒ sweep_start_hz | 985.5e6]`. */
export function ValueChip({ chip, unit }: { chip: ValueChipModel; unit?: string }) {
  const colors = VALUE_CHIP_COLORS[chip.kind];
  const outer: CSSProperties = {
    display: "inline-flex",
    alignItems: "baseline",
    borderRadius: 6,
    border: `1px ${chip.kind === "runtime" ? "dashed" : "solid"} ${colors.border}`,
    overflow: "hidden",
    fontSize: 11,
    lineHeight: 1.5,
    verticalAlign: "baseline",
    cursor: "help",
    maxWidth: "100%",
    opacity: chip.stale ? 0.5 : undefined,
  };
  const showValue = chip.value !== "";
  return (
    <ChipTooltip chip={chip}>
      <span style={outer} data-value-chip={chip.kind}>
        <span
          style={{
            padding: "0 5px",
            color: "var(--mantine-color-dimmed)",
            fontWeight: 400,
            fontStyle: chip.kind === "runtime" || chip.kind === "live" ? "italic" : undefined,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {chip.formula ? (
            <span
              style={{
                fontFamily: "Georgia, serif",
                fontStyle: "italic",
                fontWeight: 700,
                color: VALUE_CHIP_COLORS.derived.border,
                marginRight: 3,
              }}
            >
              ƒ
            </span>
          ) : null}
          {chip.name}
        </span>
        {showValue ? (
          <span
            style={{
              padding: "0 6px",
              fontWeight: 600,
              background: colors.background,
              color: colors.color,
              whiteSpace: "nowrap",
            }}
          >
            {chip.kind === "runtime" ? <i>{chip.value}</i> : chip.value}
            {unit && chip.kind !== "error" && chip.kind !== "runtime" ? ` ${unit}` : ""}
            {chip.overrideDot ? (
              <span style={{ color: OVERRIDE_DOT_COLOR, marginLeft: 4 }}>●</span>
            ) : null}
          </span>
        ) : (
          <span style={{ padding: "0 6px", color: "var(--mantine-color-dimmed)", fontStyle: "italic" }}>
            runtime
          </span>
        )}
      </span>
    </ChipTooltip>
  );
}

/**
 * Text with each `${...}` replaced by its value chip (kept as text when there
 * is no value to show).
 */
export function TextWithValues({ text, unit }: { text: string; unit?: string }) {
  const ctx = useSequencerValues();
  if (!text.includes("${")) {
    return <>{text}</>;
  }
  return (
    <>
      {splitTemplates(text).map((segment, index) => {
        if (segment.kind === "text") {
          return <Fragment key={index}>{segment.text}</Fragment>;
        }
        const chip = ctx ? describeTemplate(segment.text, segment.inner, ctx) : null;
        // Without a chip (values hidden or not resolved yet) the unit stays in the text.
        return chip ? (
          <ValueChip key={index} chip={chip} unit={unit} />
        ) : (
          <Fragment key={index}>{unit ? `${segment.text} ${unit}` : segment.text}</Fragment>
        );
      })}
    </>
  );
}

/** One line under an input: `= 50  run override · without overrides 20`. */
export function ValueHint({ text, unit }: { text: string; unit?: string }) {
  const ctx = useSequencerValues();
  if (!ctx || !text.includes("${")) {
    return null;
  }
  const segments = splitTemplates(text.trim());
  // Only a whole-value template has one value to show.
  if (segments.length !== 1 || segments[0].kind !== "template") {
    return null;
  }
  const chip = describeTemplate(segments[0].text, segments[0].inner, ctx);
  if (!chip) {
    return null;
  }
  const colors = VALUE_CHIP_COLORS[chip.kind];
  const color = chip.kind === "vars" ? "var(--mantine-color-dimmed)" : colors.border;
  if (chip.kind === "error") {
    return (
      <Text size="xs" c="red">
        {chip.detail[0]}
      </Text>
    );
  }
  if (chip.kind === "runtime" || chip.kind === "unknown") {
    return (
      <Text size="xs" style={{ color, whiteSpace: "pre-wrap" }}>
        {chip.detail.join("\n")}
      </Text>
    );
  }
  const [, ...rest] = chip.detail;
  return (
    <Text size="xs" style={{ color, whiteSpace: "pre-wrap" }}>
      {chip.value ? `= ${chip.value}${unit ? ` ${unit}` : ""}` : ""}
      {chip.formula ? (
        <span style={{ fontFamily: "var(--mantine-font-family-monospace)" }}>
          {"\n"}
          {rest.join("\n")}
        </span>
      ) : (
        <span style={{ color: "var(--mantine-color-dimmed)" }}> {rest.join(" · ")}</span>
      )}
    </Text>
  );
}
