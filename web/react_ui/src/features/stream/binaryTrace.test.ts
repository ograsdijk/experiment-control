import { describe, expect, it } from "vitest";

import { decodeBinaryTraceValues } from "./binaryTrace";

describe("decodeBinaryTraceValues", () => {
  it("decodes float64 bodies, including frames without a dtype", () => {
    const body = new Float64Array([1.5, -2.25, 3]).buffer;
    expect([...decodeBinaryTraceValues(body, "float64", 24)!]).toEqual([1.5, -2.25, 3]);
    expect([...decodeBinaryTraceValues(body, null, null)!]).toEqual([1.5, -2.25, 3]);
  });

  it("widens float32 bodies to Float64Array", () => {
    const body = new Float32Array([0.5, -1, 2.75]).buffer;
    const values = decodeBinaryTraceValues(body, "float32", 12);
    expect(values).toBeInstanceOf(Float64Array);
    expect([...values!]).toEqual([0.5, -1, 2.75]);
  });

  it("rejects bodies that do not match the announced length or dtype", () => {
    expect(decodeBinaryTraceValues(new ArrayBuffer(24), "float64", 16)).toBeNull();
    expect(decodeBinaryTraceValues(new ArrayBuffer(12), "float64", null)).toBeNull();
    expect(decodeBinaryTraceValues(new ArrayBuffer(6), "float32", null)).toBeNull();
    expect(decodeBinaryTraceValues(new ArrayBuffer(8), "int16", null)).toBeNull();
  });
});
