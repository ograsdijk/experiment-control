import type { TraceKey } from "../../types";
import { RingBuffer } from "../../utils/ringBuffer";

type SourceCache = {
  buffer: RingBuffer | null;
  generation: number;
};

function traceId(trace: TraceKey): string {
  return `${trace.deviceId}:${trace.signal}`;
}

/** Reuses source and aligned arrays across visual refreshes for one plot. */
export class TelemetryPlotDataCache {
  private sources: SourceCache[] = [];
  private aligned: number[][] = [[]];
  private alignedLength = 0;
  private changedSources: boolean[] = [];

  update(
    traces: readonly TraceKey[],
    buffers: ReadonlyMap<string, RingBuffer>
  ): number[][] {
    const shapeChanged = this.sources.length !== traces.length;
    let changed = shapeChanged;
    const changedSources = this.changedSources;
    changedSources.length = traces.length;
    changedSources.fill(false);
    this.sources.length = traces.length;
    for (let index = 0; index < traces.length; index += 1) {
      const buffer = buffers.get(traceId(traces[index])) ?? null;
      let source = this.sources[index];
      if (!source) {
        source = { buffer: null, generation: -1 };
        this.sources[index] = source;
        changed = true;
        changedSources[index] = true;
      }
      if (source.buffer !== buffer || source.generation !== (buffer?.generation ?? -1)) {
        source.buffer = buffer;
        source.generation = buffer?.generation ?? -1;
        changed = true;
        changedSources[index] = true;
      }
    }
    if (!changed) {
      return this.aligned;
    }

    if (traces.length === 0) {
      this.aligned.length = 1;
      this.aligned[0].length = 0;
      this.alignedLength = 0;
      return this.aligned;
    }
    let minLength = Number.POSITIVE_INFINITY;
    for (const source of this.sources) {
      if (source.buffer && source.buffer.length > 0) {
        minLength = Math.min(minLength, source.buffer.length);
      }
    }
    if (!Number.isFinite(minLength) || minLength <= 0) {
      minLength = 0;
    }
    const alignmentChanged = shapeChanged || minLength !== this.alignedLength;
    this.alignedLength = minLength;
    this.aligned.length = traces.length + 1;
    for (let seriesIndex = 0; seriesIndex < this.aligned.length; seriesIndex += 1) {
      this.aligned[seriesIndex] ??= [];
    }
    const firstBuffer = this.sources[0]?.buffer ?? null;
    if (alignmentChanged || changedSources[0]) {
      if (firstBuffer) {
        firstBuffer.copyTailTimesInto(this.aligned[0], minLength);
      } else {
        this.aligned[0].length = 0;
      }
    }
    for (let traceIndex = 0; traceIndex < this.sources.length; traceIndex += 1) {
      const target = this.aligned[traceIndex + 1];
      if (alignmentChanged || changedSources[traceIndex]) {
        const buffer = this.sources[traceIndex].buffer;
        if (buffer) {
          buffer.copyTailValuesInto(target, minLength);
        } else {
          target.length = 0;
        }
      }
    }
    return this.aligned;
  }
}
