export class RingBuffer {
  private capacity: number;
  private times: number[];
  private values: number[];
  private start: number;
  private count: number;
  private revision = 0;
  private nextSequence = 0;
  private structureRevision = 0;

  constructor(capacity: number) {
    this.capacity = Math.max(10, capacity);
    this.times = new Array(this.capacity);
    this.values = new Array(this.capacity);
    this.start = 0;
    this.count = 0;
  }

  get length(): number {
    return this.count;
  }

  get size(): number {
    return this.count;
  }

  get generation(): number {
    return this.revision;
  }

  /** Number of samples pushed over this buffer's lifetime. */
  get sequence(): number {
    return this.nextSequence;
  }

  /** Changes only when clear/resize invalidates incremental consumers. */
  get structuralGeneration(): number {
    return this.structureRevision;
  }

  push(time: number, value: number) {
    const index = (this.start + this.count) % this.capacity;
    this.times[index] = time;
    this.values[index] = value;
    if (this.count < this.capacity) {
      this.count += 1;
    } else {
      this.start = (this.start + 1) % this.capacity;
    }
    this.nextSequence += 1;
    this.revision += 1;
  }

  clear() {
    this.start = 0;
    this.count = 0;
    this.revision += 1;
    this.structureRevision += 1;
  }

  resize(nextCapacity: number) {
    const capacity = Math.max(10, nextCapacity);
    if (capacity === this.capacity) {
      return;
    }
    const keep = Math.min(this.count, capacity);
    const t: number[] = [];
    const v: number[] = [];
    this.copyTailInto(t, v, keep);
    this.capacity = capacity;
    this.times = new Array(this.capacity);
    this.values = new Array(this.capacity);
    this.start = 0;
    this.count = keep;
    for (let i = 0; i < keep; i += 1) {
      this.times[i] = t[i];
      this.values[i] = v[i];
    }
    this.revision += 1;
    this.structureRevision += 1;
  }

  copyInto(timeTarget: number[], valueTarget: number[]): number {
    return this.copyTailInto(timeTarget, valueTarget, this.count);
  }

  copyTailInto(
    timeTarget: number[],
    valueTarget: number[],
    requestedCount: number
  ): number {
    const copyCount = Math.max(0, Math.min(this.count, Math.floor(requestedCount)));
    const offset = this.count - copyCount;
    timeTarget.length = copyCount;
    valueTarget.length = copyCount;
    for (let i = 0; i < copyCount; i += 1) {
      const idx = (this.start + offset + i) % this.capacity;
      timeTarget[i] = this.times[idx];
      valueTarget[i] = this.values[idx];
    }
    return copyCount;
  }

  copyTailTimesInto(target: number[], requestedCount: number): number {
    return this.copyTailFieldInto(this.times, target, requestedCount);
  }

  copyTailValuesInto(target: number[], requestedCount: number): number {
    return this.copyTailFieldInto(this.values, target, requestedCount);
  }

  private copyTailFieldInto(
    source: readonly number[],
    target: number[],
    requestedCount: number
  ): number {
    const copyCount = Math.max(0, Math.min(this.count, Math.floor(requestedCount)));
    const offset = this.count - copyCount;
    target.length = copyCount;
    for (let i = 0; i < copyCount; i += 1) {
      target[i] = source[(this.start + offset + i) % this.capacity];
    }
    return copyCount;
  }

  forEachOrdered(
    visitor: (time: number, value: number, index: number) => void,
    startIndex = 0
  ): void {
    const first = Math.max(0, Math.min(this.count, Math.floor(startIndex)));
    for (let i = first; i < this.count; i += 1) {
      const idx = (this.start + i) % this.capacity;
      visitor(this.times[idx], this.values[idx], i);
    }
  }

  toArrays(): [number[], number[]] {
    const t: number[] = [];
    const v: number[] = [];
    this.copyInto(t, v);
    return [t, v];
  }

  latest(): { time: number; value: number } | null {
    if (this.count <= 0) {
      return null;
    }
    const idx = (this.start + this.count - 1) % this.capacity;
    return {
      time: this.times[idx],
      value: this.values[idx],
    };
  }
}
