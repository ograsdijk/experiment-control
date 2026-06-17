import { YAMLMap } from "yaml";
import type { Document } from "yaml";
import type { SequencerOutlineMetadataEntry, SequencerStepOutlineNode } from "../types";
import { replaceStepSnippet } from "./shared";
import {
  bodyMap,
  cleanEntries,
  editStep,
  entriesToMap,
  setScalarOrDelete,
  textToNode,
} from "./yaml_write";

type Entry = SequencerOutlineMetadataEntry;
type Group = { name: string; entries: ReadonlyArray<Entry> };
type Metric = { name: string; sourceKind: string | null; config: ReadonlyArray<Entry> };

function groupsToMap(doc: Document, groups: ReadonlyArray<Group>): YAMLMap {
  const map = new YAMLMap();
  for (const group of groups) {
    const name = group.name.trim();
    if (!name) {
      continue;
    }
    const entries = cleanEntries(group.entries);
    if (entries.length === 1 && entries[0].name === "value") {
      map.set(name, textToNode(doc, entries[0].value));
    } else {
      map.set(name, entriesToMap(doc, entries));
    }
  }
  return map;
}

function metricsToMap(doc: Document, metrics: ReadonlyArray<Metric>): YAMLMap {
  const map = new YAMLMap();
  for (const metric of metrics) {
    const name = metric.name.trim();
    if (!name) {
      continue;
    }
    const entry = new YAMLMap();
    if (metric.sourceKind) {
      entry.set("kind", textToNode(doc, metric.sourceKind));
    }
    const config = cleanEntries(metric.config);
    if (config.length > 0) {
      entry.set("config", entriesToMap(doc, config));
    }
    map.set(name, entry);
  }
  return map;
}

export function applyEditedAdaptiveStep(
  yamlText: string,
  node: SequencerStepOutlineNode,
  adaptiveId: string,
  controllerKind: string,
  minLoss: string,
  controllerConfigExtra: ReadonlyArray<Entry>,
  space: ReadonlyArray<Group>,
  bind: ReadonlyArray<Entry>,
  metrics: ReadonlyArray<Metric>,
  aggregate: ReadonlyArray<Entry>,
  observeRepeats: string,
  score: string,
  maxTrials: string,
  stoppingExtra: ReadonlyArray<Entry>
): string {
  const out = editStep(node.snippet, (doc, item) => {
    const body = bodyMap(item, "adaptive");
    setScalarOrDelete(doc, body, "id", adaptiveId);

    const controller = new YAMLMap();
    controller.set(
      "kind",
      textToNode(
        doc,
        controllerKind || node.adaptiveDetail?.controllerKind || "adaptive.adaptive_grid_1d"
      )
    );
    const controllerConfig = controllerConfigExtra
      .filter((entry) => entry.name !== "min_loss")
      .map((entry) => ({ ...entry }));
    if (minLoss.trim()) {
      controllerConfig.push({ name: "min_loss", value: minLoss });
    }
    if (cleanEntries(controllerConfig).length > 0) {
      controller.set("config", entriesToMap(doc, controllerConfig));
    }
    body.set("controller", controller);

    if (space.length > 0) {
      body.set("space", groupsToMap(doc, space));
    } else {
      body.delete("space");
    }

    if (cleanEntries(bind).length > 0) {
      body.set("bind", entriesToMap(doc, bind));
    } else {
      body.delete("bind");
    }

    const observe = new YAMLMap();
    if (observeRepeats.trim()) {
      observe.set("repeats", textToNode(doc, observeRepeats));
    }
    if (metrics.length > 0) {
      observe.set("metrics", metricsToMap(doc, metrics));
    }
    if (aggregate.length > 0) {
      observe.set("aggregate", entriesToMap(doc, aggregate));
    }
    if (score.trim()) {
      observe.set("score", textToNode(doc, score));
    }
    body.set("observe", observe);

    const stopping = stoppingExtra
      .filter((entry) => entry.name !== "max_trials")
      .map((entry) => ({ ...entry }));
    if (maxTrials.trim()) {
      stopping.push({ name: "max_trials", value: maxTrials });
    }
    if (cleanEntries(stopping).length > 0) {
      body.set("stopping", entriesToMap(doc, stopping));
    } else {
      body.delete("stopping");
    }
  });
  return replaceStepSnippet(yamlText, node, out);
}
