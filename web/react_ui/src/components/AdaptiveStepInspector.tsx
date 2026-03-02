import { Badge, Card, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import type {
  SequencerAdaptiveDetail,
  SequencerAdaptiveFieldGroup,
  SequencerAdaptiveMetricDetail,
  SequencerOutlineMetadataEntry,
} from "../features/sequencer/types";

type Props = {
  detail: SequencerAdaptiveDetail;
};

type EntryListProps = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  emptyLabel: string;
};

function EntryList({ entries, emptyLabel }: EntryListProps) {
  if (entries.length <= 0) {
    return (
      <Text size="xs" c="dimmed">
        {emptyLabel}
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {entries.map((entry) => (
        <Group key={`${entry.name}:${entry.value ?? ""}`} gap={6} wrap="wrap" align="flex-start">
          <Badge size="xs" variant="light" color="gray">
            {entry.name}
          </Badge>
          <Text
            size="xs"
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              wordBreak: "break-word",
            }}
          >
            {entry.value ?? "n/a"}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

type SectionCardProps = {
  title: string;
  children: ReactNode;
};

function SectionCard({ title, children }: SectionCardProps) {
  return (
    <Card
      radius="sm"
      p="xs"
      style={{
        border: "1px solid var(--card-border)",
        background: "rgba(148, 163, 184, 0.04)",
      }}
    >
      <Stack gap={6}>
        <Text size="xs" fw={600}>
          {title}
        </Text>
        {children}
      </Stack>
    </Card>
  );
}

function SpaceSection({ space }: { space: ReadonlyArray<SequencerAdaptiveFieldGroup> }) {
  if (space.length <= 0) {
    return (
      <Text size="xs" c="dimmed">
        No search-space parameters.
      </Text>
    );
  }
  return (
    <Stack gap={6}>
      {space.map((param) => (
        <Card
          key={param.name}
          radius="sm"
          p="xs"
          style={{ border: "1px solid var(--card-border)" }}
        >
          <Stack gap={4}>
            <Group gap={6} wrap="wrap">
              <Badge size="xs" variant="light" color="orange">
                {param.name}
              </Badge>
            </Group>
            <EntryList entries={param.entries} emptyLabel="No parameter settings." />
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

function MetricsSection({ metrics }: { metrics: ReadonlyArray<SequencerAdaptiveMetricDetail> }) {
  if (metrics.length <= 0) {
    return (
      <Text size="xs" c="dimmed">
        No metrics configured.
      </Text>
    );
  }
  return (
    <Stack gap={6}>
      {metrics.map((metric) => (
        <Card
          key={metric.name}
          radius="sm"
          p="xs"
          style={{ border: "1px solid var(--card-border)" }}
        >
          <Stack gap={4}>
            <Group gap={6} wrap="wrap">
              <Badge size="xs" variant="light" color="teal">
                {metric.name}
              </Badge>
              {metric.sourceKind ? (
                <Text size="xs" c="dimmed">
                  {metric.sourceKind}
                </Text>
              ) : null}
            </Group>
            <EntryList entries={metric.config} emptyLabel="No metric config." />
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

export function AdaptiveStepInspector({ detail }: Props) {
  return (
    <Stack gap="sm">
      <SectionCard title="Controller">
        <Group gap={6} wrap="wrap">
          {detail.id ? (
            <Badge size="xs" variant="light" color="orange">
              {detail.id}
            </Badge>
          ) : null}
          {detail.controllerKind ? (
            <Badge size="xs" variant="outline" color="gray">
              {detail.controllerKind}
            </Badge>
          ) : null}
        </Group>
        <EntryList
          entries={detail.controllerConfig}
          emptyLabel="No controller config."
        />
      </SectionCard>

      <SectionCard title="Space">
        <SpaceSection space={detail.space} />
      </SectionCard>

      <SectionCard title="Bind">
        <EntryList entries={detail.bind} emptyLabel="No bound variables." />
      </SectionCard>

      <SectionCard title="Observe">
        <Stack gap={6}>
          <Group gap={6} wrap="wrap">
            <Badge size="xs" variant="light" color="teal">
              repeats
            </Badge>
            <Text
              size="xs"
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              }}
            >
              {detail.observeRepeats ?? "n/a"}
            </Text>
          </Group>
          <MetricsSection metrics={detail.metrics} />
          {detail.aggregate.length > 0 ? (
            <>
              <Text size="xs" fw={600}>
                Aggregate
              </Text>
              <EntryList
                entries={detail.aggregate}
                emptyLabel="No aggregates configured."
              />
            </>
          ) : null}
          <Group gap={6} wrap="wrap">
            <Badge size="xs" variant="light" color="violet">
              score
            </Badge>
            <Text
              size="xs"
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                wordBreak: "break-word",
              }}
            >
              {detail.score ?? "n/a"}
            </Text>
          </Group>
        </Stack>
      </SectionCard>

      <SectionCard title="Stopping">
        <EntryList entries={detail.stopping} emptyLabel="No stopping rules." />
      </SectionCard>
    </Stack>
  );
}
