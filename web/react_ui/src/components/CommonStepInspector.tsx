import { Badge, Card, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import type {
  SequencerAtomicDetail,
  SequencerAssignDetail,
  SequencerCallDetail,
  SequencerIfDetail,
  SequencerOutlineMetadataEntry,
  SequencerParallelDetail,
  SequencerPauseDetail,
  SequencerSetContextDetail,
  SequencerSetDetail,
  SequencerSleepDetail,
  SequencerWaitUntilDetail,
  SequencerWhileDetail,
} from "../features/sequencer/types";

type CommonKind =
  | "call"
  | "sleep"
  | "set"
  | "assign"
  | "wait_until"
  | "set_context"
  | "if"
  | "while"
  | "atomic"
  | "pause"
  | "parallel";

type Props =
  | { kind: "call"; detail: SequencerCallDetail }
  | { kind: "sleep"; detail: SequencerSleepDetail }
  | { kind: "set"; detail: SequencerSetDetail }
  | { kind: "assign"; detail: SequencerAssignDetail }
  | { kind: "wait_until"; detail: SequencerWaitUntilDetail }
  | { kind: "set_context"; detail: SequencerSetContextDetail }
  | { kind: "if"; detail: SequencerIfDetail }
  | { kind: "while"; detail: SequencerWhileDetail }
  | { kind: "atomic"; detail: SequencerAtomicDetail }
  | { kind: "pause"; detail: SequencerPauseDetail }
  | { kind: "parallel"; detail: SequencerParallelDetail };

const KIND_COLORS: Record<CommonKind, string> = {
  call: "blue",
  sleep: "gray",
  set: "indigo",
  assign: "indigo",
  wait_until: "teal",
  set_context: "violet",
  if: "orange",
  while: "orange",
  atomic: "cyan",
  pause: "yellow",
  parallel: "gray",
};

type EntryListProps = {
  entries: ReadonlyArray<SequencerOutlineMetadataEntry>;
  emptyLabel: string;
  color?: string;
};

function EntryList({ entries, emptyLabel, color = "gray" }: EntryListProps) {
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
          <Badge size="xs" variant="light" color={color}>
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

function LabeledValue({
  label,
  value,
  color,
}: {
  label: string;
  value: string | null;
  color: string;
}) {
  return (
    <Group gap={6} wrap="wrap">
      <Badge size="xs" variant="light" color={color}>
        {label}
      </Badge>
      <Text
        size="xs"
        style={{
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          wordBreak: "break-word",
        }}
      >
        {value ?? "n/a"}
      </Text>
    </Group>
  );
}

export function CommonStepInspector(props: Props) {
  const color = KIND_COLORS[props.kind];

  if (props.kind === "call") {
    return (
      <SectionCard title="Call">
        <LabeledValue label="device" value={props.detail.device} color={color} />
        <LabeledValue label="action" value={props.detail.action} color={color} />
        <EntryList entries={props.detail.params} emptyLabel="No params." color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "sleep") {
    return (
      <SectionCard title="Sleep">
        <LabeledValue label="duration" value={props.detail.duration} color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "set") {
    return (
      <SectionCard title="Set">
        <LabeledValue label="device" value={props.detail.device} color={color} />
        <LabeledValue label="name" value={props.detail.name} color={color} />
        <LabeledValue label="value" value={props.detail.value} color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "assign") {
    return (
      <SectionCard title="Assign">
        <EntryList entries={props.detail.entries} emptyLabel="No assignments." color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "wait_until") {
    return (
      <Stack gap="sm">
        <SectionCard title="Timing">
          <LabeledValue label="timeout_s" value={props.detail.timeoutS} color={color} />
          <LabeledValue label="every_s" value={props.detail.everyS} color={color} />
        </SectionCard>
        <SectionCard title="Sample">
          <EntryList entries={props.detail.sample} emptyLabel="No sample config." color={color} />
        </SectionCard>
        <SectionCard title="Condition">
          <EntryList
            entries={props.detail.condition}
            emptyLabel="No condition config."
            color={color}
          />
        </SectionCard>
      </Stack>
    );
  }

  if (props.kind === "if") {
    return (
      <Stack gap="sm">
        <SectionCard title="Condition">
          <EntryList
            entries={props.detail.condition}
            emptyLabel="No condition config."
            color={color}
          />
        </SectionCard>
        <SectionCard title="Branches">
          <LabeledValue
            label="then"
            value={String(props.detail.thenCount)}
            color={color}
          />
          <LabeledValue
            label="else"
            value={String(props.detail.elseCount)}
            color={color}
          />
        </SectionCard>
      </Stack>
    );
  }

  if (props.kind === "while") {
    return (
      <SectionCard title="Condition">
        <EntryList
          entries={props.detail.condition}
          emptyLabel="No condition config."
          color={color}
        />
      </SectionCard>
    );
  }

  if (props.kind === "atomic") {
    return (
      <SectionCard title="Atomic">
        <LabeledValue label="name" value={props.detail.name} color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "pause") {
    return (
      <SectionCard title="Pause">
        <LabeledValue label="reason" value={props.detail.reason} color={color} />
      </SectionCard>
    );
  }

  if (props.kind === "parallel") {
    return (
      <SectionCard title="Parallel">
        <LabeledValue
          label="branches"
          value={String(props.detail.branchCount)}
          color={color}
        />
        <Text size="xs" c="dimmed">
          Accepted by the YAML parser but not supported at runtime in v1.
        </Text>
      </SectionCard>
    );
  }

  return (
    <Stack gap="sm">
      <SectionCard title="Streams">
        {props.detail.streams.length <= 0 ? (
          <Text size="xs" c="dimmed">
            No streams configured.
          </Text>
        ) : (
          <Stack gap={4}>
            {props.detail.streams.map((stream, index) => (
              <Card
                key={`${stream.device ?? "device"}:${stream.stream ?? "stream"}:${index}`}
                radius="sm"
                p="xs"
                style={{ border: "1px solid var(--card-border)" }}
              >
                <Stack gap={4}>
                  <LabeledValue label="device" value={stream.device} color={color} />
                  <LabeledValue label="stream" value={stream.stream} color={color} />
                </Stack>
              </Card>
            ))}
          </Stack>
        )}
      </SectionCard>
      <SectionCard title="Fields">
        <EntryList entries={props.detail.fields} emptyLabel="No context fields." color={color} />
      </SectionCard>
    </Stack>
  );
}
