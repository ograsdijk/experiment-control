# SHM Ring Memory Ordering on ARM

## Status

The shared-memory ring's sequence validation is designed for the project's current x86-64 deployments. It is not a formally portable seqlock on weakly ordered architectures such as ARM.

F13 added a second sequence check after copying each payload. This prevents a reader from accepting a slot that the writer visibly overwrote during the copy. The sequence fields are still accessed through ordinary Python `struct.pack_into()` and `struct.unpack_from()` operations, however, which do not provide cross-process acquire/release memory ordering.

Current x86-64 deployments rely on the platform's strong memory ordering and the marker placements produced by the configured stream dtypes. This is a practical deployment assumption rather than a portable atomicity guarantee, because the existing variable-length layout does not guarantee natural marker alignment. On ARM, a reader could theoretically observe the committed sequence marker before every preceding payload store has become visible. Both sequence checks could then match while the payload still contains stale bytes.

ARM is therefore not currently claimed as a correctness-supported architecture for concurrent SHM ring access. Legacy layout versions 1 through 3 have the same limitation.

## Why This Is Deferred

A correct ARM implementation is not a Python-only fence or an additional sequence comparison. It requires:

- naturally aligned sequence storage;
- lock-free cross-process 64-bit atomic operations;
- an acquire-release transition into the write-in-progress state, a release commit, and acquire loads on readers;
- a native extension and ARM64 wheel/CI coverage;
- a compatible layout and deployment transition.

The current slot table begins after variable-length dtype and shape metadata, so its sequence fields are not guaranteed to be naturally aligned. Retrofitting atomic access directly onto those fields would be unsafe.

A cross-process lock would avoid native atomics, but it would allow slow readers to block the writer while copying large frames. That conflicts with the ring's intentional overwrite-and-detect behavior.

## Suggested Layout Version 4

The least disruptive future design is to retain all existing header, slot-table, and payload offsets, then append an aligned atomic generation-token table after the payload area. One aligned 64-bit token would be associated with each slot:

```text
0             empty
(seq << 1)|1  write in progress
(seq << 1)    committed
```

The writer would publish a slot as follows:

1. Atomically exchange the token for the odd in-progress value with acquire-release semantics, preventing subsequent payload writes from becoming visible before the invalidation.
2. Copy the payload and write its timestamps and metadata.
3. Update the legacy sequence markers for backward compatibility.
4. Store the even committed token with release semantics.

A version-4 reader would:

1. Load the token with acquire semantics and reject zero or odd values.
2. Copy the metadata and payload.
3. Load the token again with acquire semantics.
4. Accept only when both token values are identical and committed.

The atomic helper should expose an acquire-release exchange, an acquire load, and a release store from a small native module and fail clearly when lock-free 64-bit atomics are unavailable.

## Compatibility and Validation

New readers should retain the existing version 1 through 3 code path. Version-4 writers should be enabled only after every independently deployed reader understands the new token table. Older readers could continue locating a version-4 payload through unchanged legacy offsets, but they would retain the ARM ordering limitation.

Before ARM support is claimed, validation should include:

- native ARM64 multiprocess stress tests with one writer and multiple readers;
- sequence-derived payload patterns that detect any accepted stale or torn frame;
- writer termination during an in-progress slot update;
- alignment and lock-free-atomic checks;
- x86-64 and ARM64 performance measurements;
- compatibility tests reading layout versions 1 through 4.

This work is intentionally deferred until ARM deployment is required. The existing post-copy validation remains valuable on supported x86-64 systems because it closes the observable ring-overrun race without adding locks to the stream path.
