import { brotliCompress, constants, gzip } from "node:zlib";
import { promisify } from "node:util";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const gzipAsync = promisify(gzip);
const brotliAsync = promisify(brotliCompress);
const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const compressibleExtensions = new Set([
  ".css",
  ".html",
  ".js",
  ".json",
  ".map",
  ".svg",
  ".txt",
  ".wasm",
]);

async function* walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(path);
      continue;
    }
    yield path;
  }
}

for await (const path of walk(dist)) {
  if (path.endsWith(".br") || path.endsWith(".gz")) {
    continue;
  }
  if (!compressibleExtensions.has(extname(path))) {
    continue;
  }
  const input = await readFile(path);
  if (input.byteLength < 1024) {
    continue;
  }
  const [gz, br] = await Promise.all([
    gzipAsync(input, { level: 9 }),
    brotliAsync(input, {
      params: {
        [constants.BROTLI_PARAM_QUALITY]: 11,
      },
    }),
  ]);
  await Promise.all([
    writeFile(`${path}.gz`, gz),
    writeFile(`${path}.br`, br),
  ]);
}
