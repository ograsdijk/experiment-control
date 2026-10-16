/** Small helpers for plain-object records read from the wire or from YAML. */

/** Own keys only: a name like `toString` must not find Object.prototype. */
export function has(rec: object | null | undefined, key: string): boolean {
  return rec != null && Object.prototype.hasOwnProperty.call(rec, key);
}

export function own<T>(rec: Record<string, T> | null | undefined, key: string): T | undefined {
  return rec != null && has(rec, key) ? rec[key] : undefined;
}

export function asRecord(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : null;
}

export function stringList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
}
