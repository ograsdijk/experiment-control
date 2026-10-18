import { CloseButton, Group, Select, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { useEffect, useRef } from "react";

import type { ResourceSortMode } from "../features/devices/health";
import type { ResourceListView } from "../features/devices/useDeviceListView";

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

type Props = {
  view: ResourceListView;
  /** Plural noun for placeholders and tooltips, e.g. "devices". */
  noun: string;
  /** Label for the healthy count, e.g. "healthy" or "running". */
  healthyLabel?: string;
  healthyTitle?: string;
  /** Label for the caller's own ordering (sort mode "custom"). */
  customSortLabel?: string;
  /** Bind the "/" hotkey to this search box (only one toolbar should). */
  focusHotkey?: boolean;
  autoFocus?: boolean;
};

/**
 * Search + sort + health summary strip shared by the device sidebar, the
 * Devices overview and the Processes modal. Clicking the issue count toggles
 * the issues-only filter.
 */
export function ResourceListToolbar({
  view,
  noun,
  healthyLabel = "healthy",
  healthyTitle,
  customSortLabel = "Custom order",
  focusHotkey = false,
  autoFocus = false,
}: Props) {
  const { query, setQuery, issuesOnly, setIssuesOnly, sort, setSort, summary } = view;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const sortOptions: { value: ResourceSortMode; label: string }[] = [
    { value: "custom", label: customSortLabel },
    { value: "health", label: "Issues first" },
    { value: "name", label: "Name" },
  ];

  useEffect(() => {
    if (!focusHotkey) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      if (isTypingTarget(event.target)) {
        return;
      }
      const input = inputRef.current;
      // Hidden when the device panel is collapsed or on the deck tab.
      if (!input || input.offsetParent === null) {
        return;
      }
      event.preventDefault();
      input.focus();
      input.select();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusHotkey]);

  const issueColor =
    summary.failed > 0
      ? "var(--mantine-color-red-6)"
      : summary.issues > 0
      ? "var(--mantine-color-orange-6)"
      : "var(--mantine-color-dimmed)";

  return (
    <div className="resource-list-toolbar">
      <Group gap={6} wrap="wrap">
        <TextInput
          ref={inputRef}
          size="xs"
          style={{ flex: "1 1 140px", minWidth: 140 }}
          placeholder={focusHotkey ? `Search ${noun}  ( / )` : `Search ${noun}`}
          aria-label={`Search ${noun}`}
          leftSection={<IconSearch size={14} />}
          value={query}
          autoFocus={autoFocus}
          onChange={(event) => setQuery(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.stopPropagation();
              setQuery("");
            }
          }}
          rightSection={
            query ? (
              <CloseButton
                size="sm"
                aria-label="Clear search"
                onClick={() => setQuery("")}
              />
            ) : null
          }
        />
        <Select
          size="xs"
          w={118}
          aria-label={`Sort ${noun}`}
          data={sortOptions}
          value={sort}
          allowDeselect={false}
          comboboxProps={{ withinPortal: true }}
          onChange={(value) =>
            setSort(value === "name" || value === "health" ? value : "custom")
          }
        />
      </Group>
      <Group gap={10} mt={6} justify="space-between" wrap="nowrap">
        <Group gap={10} wrap="nowrap">
          <Text size="xs" c="dimmed" title={healthyTitle}>
            <span style={{ color: "var(--mantine-color-teal-6)" }}>●</span>{" "}
            {summary.healthy} {healthyLabel}
          </Text>
          <UnstyledButton
            className={`resource-issues-toggle${
              issuesOnly ? " resource-issues-toggle-active" : ""
            }`}
            onClick={() => setIssuesOnly(!issuesOnly)}
            aria-pressed={issuesOnly}
            title={issuesOnly ? `Show all ${noun}` : `Show only ${noun} with issues`}
          >
            <Text size="xs" c={summary.issues > 0 ? undefined : "dimmed"}>
              <span style={{ color: issueColor }}>▲</span> {summary.issues} issue
              {summary.issues === 1 ? "" : "s"}
              {issuesOnly ? " only" : ""}
            </Text>
          </UnstyledButton>
          {summary.remote > 0 ? (
            <Text size="xs" c="dimmed" title="Mirrored from federation peers">
              ⇄ {summary.remote} remote
            </Text>
          ) : null}
        </Group>
        {view.visible.length !== summary.total ? (
          <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
            {view.visible.length} / {summary.total}
          </Text>
        ) : null}
      </Group>
    </div>
  );
}
