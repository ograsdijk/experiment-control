from __future__ import annotations

# Framework-owned context field injected by the sequencer runtime into every
# `set_context` it executes, carrying the sequencer run id that produced the
# context. Sequence YAML may not declare or set it (rejected at parse time),
# and the HDF writer projects it as an int64 context column.
SEQUENCER_RUN_ID_FIELD = "sequencer_run_id"
