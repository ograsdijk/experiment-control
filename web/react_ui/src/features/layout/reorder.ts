import type { DragEvent } from "react";

export type ReorderMode = "swap" | "before" | "after";

export function computeVerticalReorderMode(event: DragEvent<HTMLElement>): ReorderMode {
  const rect = event.currentTarget.getBoundingClientRect();
  const y = event.clientY - rect.top;
  const threshold = Math.min(28, rect.height * 0.25);
  if (y <= threshold) {
    return "before";
  }
  if (y >= rect.height - threshold) {
    return "after";
  }
  return "swap";
}

export function computeHorizontalReorderMode(event: DragEvent<HTMLElement>): ReorderMode {
  const rect = event.currentTarget.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const threshold = Math.min(40, rect.width * 0.25);
  if (x <= threshold) {
    return "before";
  }
  if (x >= rect.width - threshold) {
    return "after";
  }
  return "swap";
}

type GridEntry = {
  id: string;
  rect: DOMRect;
};

export function collectGridEntries(
  container: HTMLElement,
  attrName: string,
  excludeId?: string
): GridEntry[] {
  const selector = `[${attrName}]`;
  const elements = Array.from(container.querySelectorAll<HTMLElement>(selector));
  const entries: GridEntry[] = [];
  for (const element of elements) {
    const id = element.getAttribute(attrName) ?? "";
    if (!id || (excludeId && id === excludeId)) {
      continue;
    }
    entries.push({ id, rect: element.getBoundingClientRect() });
  }
  entries.sort((a, b) => {
    const dy = Math.abs(a.rect.top - b.rect.top);
    if (dy > 8) {
      return a.rect.top - b.rect.top;
    }
    return a.rect.left - b.rect.left;
  });
  return entries;
}

export function computeInsertIndexFromGrid(
  entries: GridEntry[],
  clientX: number,
  clientY: number
): number {
  if (entries.length === 0) {
    return 0;
  }
  type Row = { entries: GridEntry[]; top: number; bottom: number; start: number };
  const rows: Row[] = [];
  for (const entry of entries) {
    const last = rows[rows.length - 1];
    if (!last || Math.abs(entry.rect.top - last.top) > 20) {
      rows.push({
        entries: [entry],
        top: entry.rect.top,
        bottom: entry.rect.bottom,
        start: 0,
      });
      continue;
    }
    last.entries.push(entry);
    last.top = Math.min(last.top, entry.rect.top);
    last.bottom = Math.max(last.bottom, entry.rect.bottom);
  }
  let rowStart = 0;
  for (const row of rows) {
    row.entries.sort((a, b) => a.rect.left - b.rect.left);
    row.start = rowStart;
    rowStart += row.entries.length;
  }
  if (clientY < rows[0].top) {
    return 0;
  }
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    const next = rows[i + 1];
    const inRowBand =
      clientY <= row.bottom ||
      !next ||
      clientY < (row.bottom + next.top) / 2;
    if (!inRowBand) {
      continue;
    }
    for (let j = 0; j < row.entries.length; j += 1) {
      const entry = row.entries[j];
      const midX = entry.rect.left + entry.rect.width / 2;
      if (clientX < midX) {
        return row.start + j;
      }
    }
    return row.start + row.entries.length;
  }
  return entries.length;
}
