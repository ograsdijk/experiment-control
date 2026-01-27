import { Button, Group, Stack, Text } from "@mantine/core";
import type {
  StreamFitParamSample,
  StreamFitParamsMap,
  StreamParamsOutputValue,
} from "../features/stream/types";

type Props = {
  valuesByOutputId: Record<string, StreamParamsOutputValue>;
  selectedOutputIds: string[];
  onCopyJson: (payload: string) => void;
};

type RenderRow = {
  key: string;
  label: string;
  valueText: string;
};

function formatNumber(value: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs > 0 && (abs >= 1e4 || abs < 1e-3)) {
    return value.toExponential(6);
  }
  return value.toPrecision(8).replace(/\.?0+$/, "");
}

function formatParamSample(sample: StreamFitParamSample): string {
  const valueText = formatNumber(sample.value);
  const stderrText = formatNumber(sample.stderr);
  if (sample.stderr === null || !Number.isFinite(sample.stderr)) {
    return valueText;
  }
  if (sample.value === null || !Number.isFinite(sample.value)) {
    return `+/- ${stderrText}`;
  }
  return `${valueText} +/- ${stderrText}`;
}

function isFitParamsMap(value: StreamParamsOutputValue | null | undefined): value is StreamFitParamsMap {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function rowsForOutput(
  outputId: string,
  value: StreamParamsOutputValue | null | undefined
): RenderRow[] {
  if (typeof value === "number" && Number.isFinite(value)) {
    return [
      {
        key: outputId,
        label: outputId,
        valueText: formatNumber(value),
      },
    ];
  }
  if (!isFitParamsMap(value)) {
    return [
      {
        key: outputId,
        label: outputId,
        valueText: "-",
      },
    ];
  }
  const entries = Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length <= 0) {
    return [
      {
        key: outputId,
        label: outputId,
        valueText: "-",
      },
    ];
  }
  return entries.map(([name, sample]) => ({
    key: `${outputId}.${name}`,
    label: `${outputId}.${name}`,
    valueText: formatParamSample(sample),
  }));
}

export function StreamParamsPanel({
  valuesByOutputId,
  selectedOutputIds,
  onCopyJson,
}: Props) {
  const rows = selectedOutputIds.flatMap((outputId) =>
    rowsForOutput(outputId, valuesByOutputId[outputId])
  );
  const selectedValues = Object.fromEntries(
    selectedOutputIds.map((outputId) => [outputId, valuesByOutputId[outputId] ?? null])
  );

  return (
    <div className="plot-panel" style={{ minHeight: 220, padding: 12 }}>
      <Stack gap="xs">
        <Group justify="space-between" align="center">
          <Text size="xs" c="dimmed">
            Latest values
          </Text>
          <Button
            size="xs"
            variant="light"
            onClick={() => onCopyJson(JSON.stringify(selectedValues, null, 2))}
          >
            Copy JSON
          </Button>
        </Group>
        {rows.length <= 0 ? (
          <Text size="sm" c="dimmed">
            Select scalar or fit-params outputs in Plot options.
          </Text>
        ) : (
          rows.map((row) => (
            <Group key={row.key} justify="space-between" align="center">
              <Text size="sm" ff="monospace">
                {row.label}
              </Text>
              <Text size="sm" ff="monospace">
                {row.valueText}
              </Text>
            </Group>
          ))
        )}
      </Stack>
    </div>
  );
}
