/**
 * Merges validator and local diagnostics. Free of the YAML stack (unlike
 * validation.ts) so the controller can use it eagerly.
 */

import type { SequencerDiagnostic } from "./types";

export function mergeDiagnostics(
  primary: ReadonlyArray<SequencerDiagnostic>,
  secondary: ReadonlyArray<SequencerDiagnostic>
): SequencerDiagnostic[] {
  const seen = new Set<string>();
  const out: SequencerDiagnostic[] = [];
  for (const item of [...primary, ...secondary]) {
    const key = [
      item.severity,
      item.source ?? "",
      item.line ?? "",
      item.column ?? "",
      item.message,
    ].join("|");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(item);
  }
  return out;
}
