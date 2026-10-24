/**
 * Run `fn` over `items` with at most `limit` calls in flight. Results keep
 * the input order. A rejected call rejects the whole run, like Promise.all;
 * callers that want per-item tolerance catch inside `fn`.
 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await fn(items[index], index);
    }
  };
  const workerCount = Math.max(1, Math.min(Math.floor(limit), items.length));
  await Promise.all(Array.from({ length: workerCount }, worker));
  return results;
}
