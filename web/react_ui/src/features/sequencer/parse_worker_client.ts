import type { ParsedSequencerOutline } from "./outline_parse";
import { perfCount } from "../performance/perfInstrumentation";
import { loadSequencerYamlTools } from "./useSequencerYamlTools";

export type ParseResults = {
  outline: ParsedSequencerOutline;
  templates: Record<string, string>;
};
export type ParseKind = keyof ParseResults;
export type ParseRequest = { id: number; kind: ParseKind; text: string };
export type ParseResponse = { id: number; result?: unknown; error?: string };

/** A request replaced by a newer one of the same kind before it was sent. */
export class ParseSupersededError extends Error {
  constructor() {
    super("superseded by a newer parse");
  }
}

type Pending<K extends ParseKind> = {
  text: string;
  resolve: (result: ParseResults[K]) => void;
  reject: (error: unknown) => void;
};

/** The fallback: parse in the page, with the lazily loaded YAML helpers. */
async function parseHere<K extends ParseKind>(kind: K, text: string): Promise<ParseResults[K]> {
  const tools = await loadSequencerYamlTools();
  return (
    kind === "outline" ? tools.parseSequencerOutline(text) : tools.decodedTemplates(text)
  ) as ParseResults[K];
}

let worker: Worker | null | undefined;
let nextId = 1;
const inFlight = new Map<number, { kind: ParseKind; pending: Pending<ParseKind> }>();
const busy: Record<ParseKind, boolean> = { outline: false, templates: false };
const queued: { [K in ParseKind]?: Pending<K> } = {};

/** Parse in the page instead, for good: the worker could not start or failed. */
function failWorker() {
  worker?.terminate();
  worker = null;
  const waiting = [...inFlight.values()];
  inFlight.clear();
  for (const { kind, pending } of waiting) {
    busy[kind] = false;
    settleHere(kind, pending);
  }
  for (const kind of Object.keys(busy) as ParseKind[]) {
    const next = queued[kind];
    if (next) {
      delete queued[kind];
      settleHere(kind, next as Pending<ParseKind>);
    }
  }
}

function settleHere<K extends ParseKind>(kind: K, pending: Pending<K>) {
  parseHere(kind, pending.text).then(pending.resolve, pending.reject);
}

function getWorker(): Worker | null {
  if (worker !== undefined) {
    return worker;
  }
  if (typeof Worker === "undefined") {
    worker = null;
    return worker;
  }
  try {
    worker = new Worker(new URL("./parse.worker.ts", import.meta.url), { type: "module" });
  } catch {
    worker = null;
    return worker;
  }
  worker.onmessage = (event: MessageEvent<ParseResponse>) => {
    const entry = inFlight.get(event.data.id);
    if (!entry) {
      return;
    }
    inFlight.delete(event.data.id);
    busy[entry.kind] = false;
    if (event.data.error !== undefined) {
      entry.pending.reject(new Error(event.data.error));
    } else {
      entry.pending.resolve(event.data.result as never);
    }
    sendNext(entry.kind);
  };
  worker.onerror = failWorker;
  worker.onmessageerror = failWorker;
  return worker;
}

function sendNext(kind: ParseKind) {
  const next = queued[kind];
  if (!next || busy[kind]) {
    return;
  }
  delete queued[kind];
  const target = getWorker();
  if (!target) {
    settleHere(kind, next as Pending<ParseKind>);
    return;
  }
  const id = nextId++;
  busy[kind] = true;
  inFlight.set(id, { kind, pending: next as Pending<ParseKind> });
  perfCount(`sequencer.parse.${kind}.sent`);
  const request: ParseRequest = { id, kind, text: next.text };
  target.postMessage(request);
}

/**
 * Parse sequence text in the shared parse worker. Each kind keeps at most one
 * request in the worker and one waiting: a newer request replaces the waiting
 * one, which rejects with ParseSupersededError, so a burst of keystrokes
 * costs two parses, not one per key. Results of one kind arrive in order.
 * Without worker support (tests, a failed start) it parses in the page.
 */
export function parseInWorker<K extends ParseKind>(kind: K, text: string): Promise<ParseResults[K]> {
  perfCount(`sequencer.parse.${kind}.requested`);
  return new Promise<ParseResults[K]>((resolve, reject) => {
    const replaced = queued[kind];
    if (replaced) {
      replaced.reject(new ParseSupersededError());
    }
    (queued as Record<K, Pending<K>>)[kind] = { text, resolve, reject };
    sendNext(kind);
  });
}
