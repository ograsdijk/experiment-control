import { Center, Loader, Stack, Text } from "@mantine/core";
import { Suspense, useEffect } from "react";
import { useSequencerContext } from "../features/sequencer/SequencerContext";
import { SequencerModalSlot, type SequencerModalSlotProps } from "./AppModalsLayer";

type Props = Omit<SequencerModalSlotProps, "page"> & { instanceLabel: string };

/**
 * The `?view=sequencer` page: the Sequencer modal's content filling the
 * window, with no dashboard rendered behind it. It opens the sequencer the
 * way the header chip does, once the stack's process list names one.
 */
export function SequencerPage({ instanceLabel, ...slotProps }: Props) {
  const { sequencerOpen, openSequencerModal } = useSequencerContext();
  const { sequencerProcessId } = slotProps;

  useEffect(() => {
    document.title = `Sequencer ${instanceLabel}`;
  }, [instanceLabel]);

  useEffect(() => {
    if (sequencerProcessId && !sequencerOpen) {
      void openSequencerModal();
    }
  }, [sequencerProcessId, sequencerOpen, openSequencerModal]);

  const waiting = (
    <Center h="100dvh">
      <Stack gap="xs" align="center">
        <Loader size="sm" />
        <Text size="sm" c="dimmed">
          {sequencerProcessId
            ? "Opening the Sequencer…"
            : "Waiting for this stack's sequencer process…"}
        </Text>
      </Stack>
    </Center>
  );

  if (!sequencerOpen) {
    return waiting;
  }
  return (
    <Suspense fallback={waiting}>
      <SequencerModalSlot {...slotProps} page />
    </Suspense>
  );
}
