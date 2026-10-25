/**
 * Decodes the binary body that follows a `binary-frame` metadata message.
 *
 * Sockets opened with `dtype=auto` may receive float32 bodies: the gateway
 * only uses them when float32 rounding is far below the trace's span. They
 * are widened to Float64Array so the rest of the pipeline sees one type.
 * Returns null when the body does not match the announced length or dtype.
 */
export function decodeBinaryTraceValues(
  data: ArrayBuffer,
  dtype: string | null | undefined,
  expectedBytes: number | null | undefined
): Float64Array | null {
  if (
    typeof expectedBytes === "number" &&
    Number.isFinite(expectedBytes) &&
    expectedBytes > 0 &&
    expectedBytes !== data.byteLength
  ) {
    return null;
  }
  if (dtype === "float32") {
    if (data.byteLength % Float32Array.BYTES_PER_ELEMENT !== 0) {
      return null;
    }
    return Float64Array.from(new Float32Array(data));
  }
  if (dtype && dtype !== "float64") {
    return null;
  }
  if (data.byteLength % Float64Array.BYTES_PER_ELEMENT !== 0) {
    return null;
  }
  return new Float64Array(data);
}
