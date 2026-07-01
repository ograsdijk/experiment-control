import type { ComponentProps } from "react";
import { useHdfController } from "../features/hdf/useHdfController";
import { HdfMeasurementNoteModal } from "./HdfMeasurementNoteModal";
import { HdfWriterModal } from "./HdfWriterModal";

type HdfControllerState = ReturnType<typeof useHdfController>;

type Props = {
  hdf: HdfControllerState;
  renderMeasurementFieldInput: ComponentProps<
    typeof HdfMeasurementNoteModal
  >["renderMeasurementFieldInput"];
};

export function HdfModalsLayer({ hdf, renderMeasurementFieldInput }: Props) {
  return (
    <>
      <HdfMeasurementNoteModal
        opened={hdf.hdfNoteModalOpen}
        onClose={() => hdf.setHdfNoteModalOpen(false)}
        title={`Measurement Note ${hdf.hdfWriterProcessId ?? ""}`}
        hdfWriterState={hdf.hdfWriterState}
        measurementType={hdf.hdfWriterStatus?.measurementType ?? null}
        measurementNotesRows={hdf.hdfWriterStatus?.measurementNotesRows ?? 0}
        filePath={hdf.hdfWriterStatus?.filePath ?? null}
        refreshLoading={hdf.hdfMeasurementSchemaLoading || hdf.hdfStatusBusy}
        refreshDisabled={hdf.hdfCommandsBlocked || hdf.hdfAnyCommandBusy}
        onRefresh={async () => {
          if (!hdf.hdfWriterProcessId) {
            return;
          }
          await Promise.all([
            hdf.refreshHdfWriterStatus(hdf.hdfWriterProcessId),
            hdf.fetchHdfMeasurementSchema(hdf.hdfWriterProcessId),
          ]);
        }}
        showMeasurementUi={hdf.hdfShowMeasurementUi}
        supportsMeasurementNote={hdf.hdfSupportsMeasurementNote}
        fields={hdf.hdfMeasurementSchema?.notes.fields ?? []}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
        noteValuesDraft={hdf.hdfNoteValuesDraft}
        noteCustomByField={hdf.hdfNoteCustomByField}
        onSetFieldValue={hdf.setHdfNoteFieldValue}
        onSetFieldUseCustom={hdf.setHdfNoteFieldUseCustom}
        measurementNoteBusy={hdf.hdfMeasurementNoteBusy}
        addNoteDisabled={
          !hdf.hdfShowMeasurementUi ||
          hdf.hdfCommandsBlocked ||
          !hdf.hdfSupportsMeasurementNote
        }
        onAddNote={hdf.executeHdfMeasurementNote}
      />

      <HdfWriterModal
        opened={hdf.hdfModalOpen}
        onClose={() => hdf.setHdfModalOpen(false)}
        title={`HDF Writer ${hdf.hdfWriterProcessId ?? ""}`}
        hdfWriterState={hdf.hdfWriterState}
        hdfWriterProcessId={hdf.hdfWriterProcessId}
        hdfWriterStatus={hdf.hdfWriterStatus ?? null}
        hdfWriterLoading={hdf.hdfWriterLoading}
        hdfStatusBusy={hdf.hdfStatusBusy}
        hdfCommandsBlocked={hdf.hdfCommandsBlocked}
        hdfSupportsStatus={hdf.hdfSupportsStatus}
        hdfSupportsWritingStart={hdf.hdfSupportsWritingStart}
        hdfSupportsWritingStop={hdf.hdfSupportsWritingStop}
        hdfAnyCommandBusy={hdf.hdfAnyCommandBusy}
        onRefreshStatus={hdf.executeHdfStatus}
        onExecuteWritingStart={hdf.executeHdfWritingStart}
        onExecuteWritingStop={hdf.executeHdfWritingStop}
        hdfProcessCapabilitiesError={hdf.hdfProcessCapabilitiesError ?? null}
        hdfMeasurementSchemaConfigured={hdf.hdfMeasurementSchemaConfigured}
        hdfMeasurementSchemaAvailable={hdf.hdfMeasurementSchemaAvailable}
        hdfSelectableDeviceIds={hdf.hdfSelectableDeviceIds}
        hdfMeasurementSchemaDisplayPath={hdf.hdfMeasurementSchemaDisplayPath}
        hdfMeasurementSchemaDisplayError={hdf.hdfMeasurementSchemaDisplayError}
        hdfSupportsMeasurementSchemaGet={hdf.hdfSupportsMeasurementSchemaGet}
        hdfMeasurementSchemaLoading={hdf.hdfMeasurementSchemaLoading}
        onRefreshSchema={async () => {
          if (!hdf.hdfWriterProcessId) {
            return;
          }
          await hdf.fetchHdfMeasurementSchema(hdf.hdfWriterProcessId);
        }}
        hdfRotateFilenameDraft={hdf.hdfRotateFilenameDraft}
        onRotateFilenameChange={hdf.setHdfRotateFilenameDraft}
        hdfRotateDisabledDevicesDraft={hdf.hdfRotateDisabledDevicesDraft}
        onRotateDisabledDevicesChange={hdf.setHdfRotateDisabledDevicesDraft}
        hdfSelectableDeviceOptions={hdf.hdfSelectableDeviceOptions}
        hdfShowMeasurementUi={hdf.hdfShowMeasurementUi}
        hdfRotateMeasurementProfileDraft={hdf.hdfRotateMeasurementProfileDraft}
        hdfRotateProfileOptions={hdf.hdfRotateProfileOptions}
        onSelectRotateMeasurementProfile={hdf.selectHdfRotateMeasurementProfile}
        hdfRotateSelectedProfile={hdf.hdfRotateSelectedProfile}
        renderMeasurementFieldInput={renderMeasurementFieldInput}
        hdfRotateMeasurementValuesDraft={hdf.hdfRotateMeasurementValuesDraft}
        hdfRotateMeasurementCustomByField={hdf.hdfRotateMeasurementCustomByField}
        onSetRotateFieldValue={hdf.setHdfRotateFieldValue}
        onSetRotateFieldUseCustom={hdf.setHdfRotateFieldUseCustom}
        hdfRotateBusy={hdf.hdfRotateBusy}
        hdfWritingStartBusy={hdf.hdfWritingStartBusy}
        hdfWritingStopBusy={hdf.hdfWritingStopBusy}
        hdfSupportsRotate={hdf.hdfSupportsRotate}
        onExecuteRotate={hdf.executeHdfRotate}
        hdfSupportsMeasurementNote={hdf.hdfSupportsMeasurementNote}
        hdfMeasurementSchema={hdf.hdfMeasurementSchema}
        hdfNoteValuesDraft={hdf.hdfNoteValuesDraft}
        hdfNoteCustomByField={hdf.hdfNoteCustomByField}
        onSetNoteFieldValue={hdf.setHdfNoteFieldValue}
        onSetNoteFieldUseCustom={hdf.setHdfNoteFieldUseCustom}
        hdfMeasurementNoteBusy={hdf.hdfMeasurementNoteBusy}
        onExecuteMeasurementNote={hdf.executeHdfMeasurementNote}
        hdfDevicesGetBusy={hdf.hdfDevicesGetBusy}
        hdfSupportsDevicesGet={hdf.hdfSupportsDevicesGet}
        onExecuteDevicesGet={hdf.executeHdfDevicesGet}
        hdfEnableDevicesDraft={hdf.hdfEnableDevicesDraft}
        onEnableDevicesDraftChange={hdf.setHdfEnableDevicesDraft}
        hdfDevicesEnableBusy={hdf.hdfDevicesEnableBusy}
        hdfSupportsDevicesEnable={hdf.hdfSupportsDevicesEnable}
        onExecuteDevicesEnable={hdf.executeHdfDevicesEnable}
        hdfDisableDevicesDraft={hdf.hdfDisableDevicesDraft}
        onDisableDevicesDraftChange={hdf.setHdfDisableDevicesDraft}
        hdfDevicesDisableBusy={hdf.hdfDevicesDisableBusy}
        hdfSupportsDevicesDisable={hdf.hdfSupportsDevicesDisable}
        onExecuteDevicesDisable={hdf.executeHdfDevicesDisable}
        hdfSelectableProcessOptions={hdf.hdfSelectableProcessOptions}
        hdfProcessesGetBusy={hdf.hdfProcessesGetBusy}
        hdfSupportsProcessesGet={hdf.hdfSupportsProcessesGet}
        onExecuteProcessesGet={hdf.executeHdfProcessesGet}
        hdfEnableProcessesDraft={hdf.hdfEnableProcessesDraft}
        onEnableProcessesDraftChange={hdf.setHdfEnableProcessesDraft}
        hdfProcessesEnableBusy={hdf.hdfProcessesEnableBusy}
        hdfSupportsProcessesEnable={hdf.hdfSupportsProcessesEnable}
        onExecuteProcessesEnable={hdf.executeHdfProcessesEnable}
        hdfDisableProcessesDraft={hdf.hdfDisableProcessesDraft}
        onDisableProcessesDraftChange={hdf.setHdfDisableProcessesDraft}
        hdfProcessesDisableBusy={hdf.hdfProcessesDisableBusy}
        hdfSupportsProcessesDisable={hdf.hdfSupportsProcessesDisable}
        onExecuteProcessesDisable={hdf.executeHdfProcessesDisable}
      />
    </>
  );
}
