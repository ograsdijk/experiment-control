import { useEffect, useMemo, useState } from "react";

import type { DeviceStatus } from "../../types";
import {
  deviceAdapter,
  isResourceViewFiltered,
  selectVisibleResources,
  summarizeResources,
  type ResourceAdapter,
  type ResourceSortMode,
} from "./health";

function loadSort(key: string, fallback: ResourceSortMode): ResourceSortMode {
  try {
    const raw = String(localStorage.getItem(`${key}.sort`) ?? "").trim();
    return raw === "name" || raw === "health" || raw === "custom" ? raw : fallback;
  } catch {
    return fallback;
  }
}

function loadIssuesOnly(key: string): boolean {
  try {
    return localStorage.getItem(`${key}.issuesOnly`) === "1";
  } catch {
    return false;
  }
}

type ListViewOptions = {
  /** localStorage prefix; omit to keep the view state in memory only. */
  persistKey?: string;
  initialSort?: ResourceSortMode;
};

/**
 * Search / issues-only / sort state for a resource list. With a persistKey,
 * sort and the issues-only toggle persist per browser; the search text never
 * does, so a reload cannot silently hide resources behind a forgotten query.
 */
export function useResourceListView<T>(
  items: readonly T[],
  adapter: ResourceAdapter<T>,
  { persistKey, initialSort = "custom" }: ListViewOptions = {}
) {
  const [query, setQuery] = useState("");
  const [issuesOnly, setIssuesOnly] = useState<boolean>(() =>
    persistKey ? loadIssuesOnly(persistKey) : false
  );
  const [sort, setSort] = useState<ResourceSortMode>(() =>
    persistKey ? loadSort(persistKey, initialSort) : initialSort
  );

  useEffect(() => {
    if (!persistKey) {
      return;
    }
    try {
      localStorage.setItem(`${persistKey}.sort`, sort);
      localStorage.setItem(`${persistKey}.issuesOnly`, issuesOnly ? "1" : "0");
    } catch {
      // Storage unavailable (private window); state stays in memory.
    }
  }, [persistKey, sort, issuesOnly]);

  const visible = useMemo(
    () => selectVisibleResources(items, adapter, { query, issuesOnly, sort }),
    [items, adapter, query, issuesOnly, sort]
  );
  const summary = useMemo(() => summarizeResources(items, adapter), [items, adapter]);
  const filtered = isResourceViewFiltered({ query, issuesOnly, sort });

  return {
    query,
    setQuery,
    issuesOnly,
    setIssuesOnly,
    sort,
    setSort,
    visible,
    summary,
    filtered,
  };
}

export type ResourceListView = ReturnType<typeof useResourceListView>;

export function useDeviceListView(
  orderedDevices: DeviceStatus[],
  { persist = true, initialSort = "custom" as ResourceSortMode } = {}
) {
  const view = useResourceListView(orderedDevices, deviceAdapter, {
    persistKey: persist ? "ecui.devicePanel" : undefined,
    initialSort,
  });
  return { ...view, visibleDevices: view.visible };
}

export type DeviceListView = ReturnType<typeof useDeviceListView>;
