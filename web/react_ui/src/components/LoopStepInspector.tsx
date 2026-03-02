import { Badge, Card, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import type {
  SequencerForDetail,
  SequencerOutlineMetadataEntry,
  SequencerRepeatDetail,
} from "../features/sequencer/types";

type Props =
  | {
      kind: "for";
      detail: SequencerForDetail;
    }
  | {
      kind: "repeat";
      detail: SequencerRepeatDetail;
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
          <Badge size="xs" variant="light" color="cyan">
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

export function LoopStepInspector(props: Props) {
  if (props.kind === "repeat") {
    return (
      <SectionCard title="Repeat">
        <Group gap={6} wrap="wrap">
          <Badge size="xs" variant="light" color="cyan">
            times
          </Badge>
          <Text
            size="xs"
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
            }}
          >
            {props.detail.times ?? "n/a"}
          </Text>
        </Group>
      </SectionCard>
    );
  }

  return (
    <Stack gap="sm">
      <SectionCard title="Bind">
        <EntryList entries={props.detail.bind} emptyLabel="No loop bindings." />
      </SectionCard>

      <SectionCard title="Iterable">
        <Group gap={6} wrap="wrap">
          <Badge size="xs" variant="light" color="cyan">
            source
          </Badge>
          <Text
            size="xs"
            style={{
              fontFamily:
                "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
            }}
          >
            {props.detail.sourceMode}
          </Text>
        </Group>
        {props.detail.sourceMode === "generator" ? (
          <Group gap={6} wrap="wrap">
            <Badge size="xs" variant="light" color="cyan">
              kind
            </Badge>
            <Text
              size="xs"
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              }}
            >
              {props.detail.generatorKind ?? "n/a"}
            </Text>
          </Group>
        ) : (
          <Group gap={6} wrap="wrap">
            <Badge size="xs" variant="light" color="cyan">
              expression
            </Badge>
            <Text
              size="xs"
              style={{
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              }}
            >
              {props.detail.directValue ?? "n/a"}
            </Text>
          </Group>
        )}
        {props.detail.generatorModifiers.length > 0 ? (
          <EntryList
            entries={props.detail.generatorModifiers}
            emptyLabel="No generator modifiers."
          />
        ) : null}
        <EntryList
          entries={props.detail.iterableConfig}
          emptyLabel="No iterable settings."
        />
      </SectionCard>
    </Stack>
  );
}
