import { AdaptiveStepEditor } from "../features/sequencer/components/StepEditorAdaptive";
import { CallStepEditor } from "../features/sequencer/components/StepEditorCall";
import {
  AssignStepEditor,
  IfStepEditor,
  SetStepEditor,
  SetContextStepEditor,
  WaitUntilStepEditor,
  WhileStepEditor,
} from "../features/sequencer/components/StepEditorControl";
import { ForStepEditor } from "../features/sequencer/components/StepEditorFor";
import {
  RepeatStepEditor,
  SleepStepEditor,
} from "../features/sequencer/components/StepEditorSimple";
import type { SequencerStepOutlineNode } from "../features/sequencer/types";
import type { StreamAnalysisWorkspaceConfig } from "../features/stream/types";
import type { CapabilityMember } from "../types";
import type { StreamCatalogEntry } from "../types";
import type { TelemetrySignal } from "../types";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  streamCatalog: StreamCatalogEntry[];
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  streamWorkspaces: Record<string, StreamAnalysisWorkspaceConfig>;
  latestSignalsByDevice: Record<string, Record<string, TelemetrySignal>>;
  onSelectStep?: (id: string) => void;
};

export function EditableStepInspector({
  node,
  yamlText,
  onYamlTextChange,
  streamCatalog,
  capabilitiesByDevice,
  streamWorkspaces,
  latestSignalsByDevice,
}: Props) {
  if (node.adaptiveDetail) {
    return (
      <AdaptiveStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        capabilitiesByDevice={capabilitiesByDevice}
        streamWorkspaces={streamWorkspaces}
        latestSignalsByDevice={latestSignalsByDevice}
      />
    );
  }

  if (node.callDetail) {
    return (
      <CallStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        capabilitiesByDevice={capabilitiesByDevice}
      />
    );
  }

  if (node.forDetail) {
    return (
      <ForStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  if (node.sleepDetail) {
    return (
      <SleepStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  if (node.waitUntilDetail) {
    return (
      <WaitUntilStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        capabilitiesByDevice={capabilitiesByDevice}
        latestSignalsByDevice={latestSignalsByDevice}
      />
    );
  }

  if (node.setDetail) {
    return (
      <SetStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        capabilitiesByDevice={capabilitiesByDevice}
      />
    );
  }

  if (node.assignDetail) {
    return (
      <AssignStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  if (node.setContextDetail) {
    return (
      <SetContextStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        streamCatalog={streamCatalog}
      />
    );
  }

  if (node.repeatDetail) {
    return (
      <RepeatStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  if (node.ifDetail) {
    return (
      <IfStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  if (node.whileDetail) {
    return (
      <WhileStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
      />
    );
  }

  return null;
}
