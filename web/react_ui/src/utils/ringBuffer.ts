export class RingBuffer {
  private capacity: number;
  private times: number[];
  private values: number[];
  private start: number;
  private length: number;

  constructor(capacity: number) {
    this.capacity = Math.max(10, capacity);
    this.times = new Array(this.capacity);
    this.values = new Array(this.capacity);
    this.start = 0;
    this.length = 0;
  }

  push(time: number, value: number) {
    const index = (this.start + this.length) % this.capacity;
    this.times[index] = time;
    this.values[index] = value;
    if (this.length < this.capacity) {
      this.length += 1;
    } else {
      this.start = (this.start + 1) % this.capacity;
    }
  }

  clear() {
    this.start = 0;
    this.length = 0;
  }

  resize(nextCapacity: number) {
    const capacity = Math.max(10, nextCapacity);
    const [t, v] = this.toArrays();
    const keepStart = Math.max(0, t.length - capacity);
    const tSlice = t.slice(keepStart);
    const vSlice = v.slice(keepStart);
    this.capacity = capacity;
    this.times = new Array(this.capacity);
    this.values = new Array(this.capacity);
    this.start = 0;
    this.length = 0;
    for (let i = 0; i < tSlice.length; i += 1) {
      this.times[i] = tSlice[i];
      this.values[i] = vSlice[i];
      this.length += 1;
    }
  }

  toArrays(): [number[], number[]] {
    const t: number[] = [];
    const v: number[] = [];
    for (let i = 0; i < this.length; i += 1) {
      const idx = (this.start + i) % this.capacity;
      t.push(this.times[idx]);
      v.push(this.values[idx]);
    }
    return [t, v];
  }

  latest(): { time: number; value: number } | null {
    if (this.length <= 0) {
      return null;
    }
    const idx = (this.start + this.length - 1) % this.capacity;
    return {
      time: this.times[idx],
      value: this.values[idx],
    };
  }
}
