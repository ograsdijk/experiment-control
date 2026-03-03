import { AdaptiveStepEditor } from "../features/sequencer/components/StepEditorAdaptive";
import { CallStepEditor } from "../features/sequencer/components/StepEditorCall";
import {
  IfStepEditor,
  WaitUntilStepEditor,
  WhileStepEditor,
} from "../features/sequencer/components/StepEditorControl";
import { ForStepEditor } from "../features/sequencer/components/StepEditorFor";
import {
  RepeatStepEditor,
  SleepStepEditor,
} from "../features/sequencer/components/StepEditorSimple";
import type { SequencerStepOutlineNode } from "../features/sequencer/types";
import type { CapabilityMember } from "../types";

type Props = {
  node: SequencerStepOutlineNode;
  yamlText: string;
  onYamlTextChange: (value: string) => void;
  capabilitiesByDevice: Record<string, CapabilityMember[]>;
  onSelectStep?: (id: string) => void;
};

export function EditableStepInspector({
  node,
  yamlText,
  onYamlTextChange,
  capabilitiesByDevice,
}: Props) {
  if (node.adaptiveDetail) {
    return (
      <AdaptiveStepEditor
        node={node}
        yamlText={yamlText}
        onYamlTextChange={onYamlTextChange}
        capabilitiesByDevice={capabilitiesByDevice}
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
