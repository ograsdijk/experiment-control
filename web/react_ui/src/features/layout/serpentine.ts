export function arrayMove<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  const next = [...items];
  if (
    fromIndex < 0 ||
    fromIndex >= next.length ||
    toIndex < 0 ||
    toIndex >= next.length ||
    fromIndex === toIndex
  ) {
    return next;
  }
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

function clampColumns(columns: number): number {
  if (!Number.isFinite(columns)) {
    return 1;
  }
  return Math.max(1, Math.trunc(columns));
}

export function buildSerpentineRowMajorOrder(length: number, columns: number): number[] {
  const safeColumns = clampColumns(columns);
  const out: number[] = [];
  for (let row = 0; row * safeColumns < length; row += 1) {
    const start = row * safeColumns;
    const end = Math.min(start + safeColumns, length);
    if (row % 2 === 0) {
      for (let idx = start; idx < end; idx += 1) {
        out.push(idx);
      }
      continue;
    }
    for (let idx = end - 1; idx >= start; idx -= 1) {
      out.push(idx);
    }
  }
  return out;
}

export function reorderIdsSerpentine(
  idsRowMajor: string[],
  sourceId: string,
  targetId: string,
  columns: number
): string[] {
  const sourceRowMajor = idsRowMajor.indexOf(sourceId);
  const targetRowMajor = idsRowMajor.indexOf(targetId);
  if (
    sourceRowMajor < 0 ||
    targetRowMajor < 0 ||
    sourceRowMajor === targetRowMajor
  ) {
    return idsRowMajor;
  }
  const rowMajorAtSerpentine = buildSerpentineRowMajorOrder(
    idsRowMajor.length,
    columns
  );
  const serpentineAtRowMajor = new Array<number>(idsRowMajor.length).fill(-1);
  for (let serpentineIndex = 0; serpentineIndex < rowMajorAtSerpentine.length; serpentineIndex += 1) {
    serpentineAtRowMajor[rowMajorAtSerpentine[serpentineIndex]] = serpentineIndex;
  }
  const sourceSerpentine = serpentineAtRowMajor[sourceRowMajor];
  const targetSerpentine = serpentineAtRowMajor[targetRowMajor];
  if (sourceSerpentine < 0 || targetSerpentine < 0) {
    return idsRowMajor;
  }
  const serpentineIds = rowMajorAtSerpentine.map((rowMajor) => idsRowMajor[rowMajor]);
  const movedSerpentine = arrayMove(
    serpentineIds,
    sourceSerpentine,
    targetSerpentine
  );
  const next = new Array<string>(idsRowMajor.length).fill("");
  for (let rowMajor = 0; rowMajor < idsRowMajor.length; rowMajor += 1) {
    const serpentineIndex = serpentineAtRowMajor[rowMajor];
    next[rowMajor] = movedSerpentine[serpentineIndex];
  }
  return next;
}

export function detectGridColumns(
  container: HTMLElement | null,
  itemAttrName: string,
  rowTolerancePx: number = 16
): number {
  if (!container) {
    return 1;
  }
  const elements = Array.from(
    container.querySelectorAll<HTMLElement>(`[${itemAttrName}]`)
  );
  if (elements.length === 0) {
    return 1;
  }
  const firstTop = elements[0].getBoundingClientRect().top;
  let cols = 0;
  for (const element of elements) {
    const top = element.getBoundingClientRect().top;
    if (Math.abs(top - firstTop) > rowTolerancePx) {
      break;
    }
    cols += 1;
  }
  return Math.max(1, cols);
}
