/// <reference lib="webworker" />
/**
 * Parses sequence YAML off the page's main thread: a long sequence takes tens
 * of milliseconds per parse. See parse_worker_client.ts for the protocol.
 */
import { parseSequencerOutline } from "./outline_parse";
import type { ParseKind, ParseRequest, ParseResponse } from "./parse_worker_client";
import { decodedTemplates } from "./templates";

function parse(kind: ParseKind, text: string) {
  return kind === "outline" ? parseSequencerOutline(text) : decodedTemplates(text);
}

self.onmessage = (event: MessageEvent<ParseRequest>) => {
  const { id, kind, text } = event.data;
  let response: ParseResponse;
  try {
    response = { id, result: parse(kind, text) };
  } catch (error) {
    response = { id, error: error instanceof Error ? error.message : String(error) };
  }
  self.postMessage(response);
};
