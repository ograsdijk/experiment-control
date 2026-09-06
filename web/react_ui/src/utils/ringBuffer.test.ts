import { describe, expect, it } from "vitest";

import { RingBuffer } from "./ringBuffer";

function pushRange(buffer: RingBuffer, start: number, end: number) {
  for (let value = start; value <= end; value += 1) {
    buffer.push(value, value * 10);
  }
}

describe("RingBuffer", () => {
  it("copies ordered values into reusable targets before and after wrap", () => {
    const buffer = new RingBuffer(10);
    pushRange(buffer, 0, 7);
    const times = [999, 999];
    const values = [999];
    expect(buffer.copyInto(times, values)).toBe(8);
    expect(times).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(values).toEqual([0, 10, 20, 30, 40, 50, 60, 70]);

    pushRange(buffer, 8, 12);
    expect(buffer.copyInto(times, values)).toBe(10);
    expect(times).toEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(values).toEqual([30, 40, 50, 60, 70, 80, 90, 100, 110, 120]);
  });

  it("copies a tail and traverses an ordered suffix", () => {
    const buffer = new RingBuffer(10);
    pushRange(buffer, 0, 12);
    const times: number[] = [];
    const values: number[] = [];
    buffer.copyTailInto(times, values, 3);
    expect(times).toEqual([10, 11, 12]);
    expect(values).toEqual([100, 110, 120]);
    expect(buffer.copyTailTimesInto(times, 2)).toBe(2);
    expect(buffer.copyTailValuesInto(values, 2)).toBe(2);
    expect(times).toEqual([11, 12]);
    expect(values).toEqual([110, 120]);
    const visited: number[] = [];
    buffer.forEachOrdered((_time, value) => visited.push(value), 8);
    expect(visited).toEqual([110, 120]);
  });

  it("retains the newest data when resized smaller or larger", () => {
    const buffer = new RingBuffer(12);
    pushRange(buffer, 0, 11);
    buffer.resize(10);
    expect(buffer.toArrays()[0]).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    buffer.resize(20);
    pushRange(buffer, 12, 14);
    expect(buffer.toArrays()[0]).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]);
  });

  it("tracks size, mutations, structure changes, sequence, clear, and latest", () => {
    const buffer = new RingBuffer(10);
    expect(buffer.length).toBe(0);
    expect(buffer.generation).toBe(0);
    expect(buffer.sequence).toBe(0);
    expect(buffer.structuralGeneration).toBe(0);
    buffer.push(1, 2);
    expect(buffer.size).toBe(1);
    expect(buffer.latest()).toEqual({ time: 1, value: 2 });
    expect(buffer.generation).toBe(1);
    expect(buffer.sequence).toBe(1);
    buffer.resize(20);
    expect(buffer.generation).toBe(2);
    expect(buffer.structuralGeneration).toBe(1);
    expect(buffer.sequence).toBe(1);
    buffer.clear();
    expect(buffer.length).toBe(0);
    expect(buffer.latest()).toBeNull();
    expect(buffer.generation).toBe(3);
    expect(buffer.structuralGeneration).toBe(2);
    expect(buffer.sequence).toBe(1);
  });
});
