import { memo } from 'react';
import { useOptionalAnalyzePageLogic } from '../../DashboardContext';
import { AnalyzeHeader } from './analyze/AnalyzeHeader';
import { BatchAnalyzePanel } from './analyze/BatchAnalyzePanel';
import { ReportPreviewSection } from './analyze/ReportPreviewSection';
import { SingleAnalyzePanel } from './analyze/SingleAnalyzePanel';

export const AnalyzePage = memo(function AnalyzePage({ logic: logicProp }) {
  const contextLogic = useOptionalAnalyzePageLogic();
  const logic = logicProp ?? contextLogic;
  const {
    uploadProgress,
    isUploading,
    isBatchUploading,
    cancelUpload,
    cancelBatchUpload,
    activeProfileMode,
    setActiveProfileMode,
    analyzeSubMode,
    setAnalyzeSubMode,
    isProfileBusy,
    isRegenerateBusy,
    batchMode,
    setBatchMode,
    batchSubmitting,
    batchSubmitted,
    inlineReportPath,
    buildReportUrl,
    PROFILE_MODES,
  } = logic;

  const isAnalyzeScopeLocked = isProfileBusy || isRegenerateBusy || batchSubmitting;

  return (
    <>
      <article className="card profile-input-card tab-primary-tile">
        <AnalyzeHeader
          uploadProgress={uploadProgress}
          isUploading={isUploading}
          isBatchUploading={isBatchUploading}
          cancelUpload={cancelUpload}
          cancelBatchUpload={cancelBatchUpload}
          analyzeSubMode={analyzeSubMode}
          setAnalyzeSubMode={setAnalyzeSubMode}
          activeProfileMode={activeProfileMode}
          setActiveProfileMode={setActiveProfileMode}
          batchMode={batchMode}
          setBatchMode={setBatchMode}
          batchSubmitted={batchSubmitted}
          batchSubmitting={batchSubmitting}
          isProfileBusy={isProfileBusy}
          isRegenerateBusy={isRegenerateBusy}
          isAnalyzeScopeLocked={isAnalyzeScopeLocked}
          profileModes={PROFILE_MODES}
        />
        {analyzeSubMode === 'single' ? <SingleAnalyzePanel logic={logicProp} /> : null}
        {analyzeSubMode === 'batch' ? <BatchAnalyzePanel logic={logicProp} /> : null}
      </article>

      <ReportPreviewSection
        showFrame={analyzeSubMode !== 'batch' && Boolean(inlineReportPath)}
        inlineReportPath={inlineReportPath}
        buildReportUrl={buildReportUrl}
      />
    </>
  );
});
