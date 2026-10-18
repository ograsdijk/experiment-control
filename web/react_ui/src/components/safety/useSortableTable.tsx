import { Table, UnstyledButton } from "@mantine/core";
import { useMemo, useState } from "react";

export type SortValue = string | number | null;

function compareValues(a: SortValue, b: SortValue): number {
  if (typeof a === "string" && typeof b === "string") {
    return a.localeCompare(b);
  }
  const av = typeof a === "number" ? a : Number.POSITIVE_INFINITY;
  const bv = typeof b === "number" ? b : Number.POSITIVE_INFINITY;
  return av - bv;
}

/**
 * Column-header sorting on top of the list toolbar's own order: click once
 * ascending, twice descending, three times back to the toolbar order.
 */
export function useSortableTable<T, C extends string>(
  rows: readonly T[],
  sortValue: (row: T, column: C) => SortValue
) {
  const [sort, setSort] = useState<{ column: C; reverse: boolean } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) {
      return rows;
    }
    const out = [...rows].sort((a, b) =>
      compareValues(sortValue(a, sort.column), sortValue(b, sort.column))
    );
    return sort.reverse ? out.reverse() : out;
    // sortValue is a pure function of the row; callers pass a module-level fn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);

  const header = (column: C, label: string) => {
    const active = sort?.column === column;
    return (
      <Table.Th key={column}>
        <UnstyledButton
          className="resource-overview-th"
          onClick={() =>
            setSort((prev) =>
              prev?.column === column
                ? prev.reverse
                  ? null
                  : { column, reverse: true }
                : { column, reverse: false }
            )
          }
        >
          {label}
          {active ? (sort?.reverse ? " ▼" : " ▲") : ""}
        </UnstyledButton>
      </Table.Th>
    );
  };

  return { sorted, header };
}
