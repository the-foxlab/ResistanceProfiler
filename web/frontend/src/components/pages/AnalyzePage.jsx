import { useEffect } from 'react';

import { AnalyzeHeader } from './analyze/AnalyzeHeader';
import { BatchAnalyzePanel } from './analyze/BatchAnalyzePanel';
import { ReportPreviewSection } from './analyze/ReportPreviewSection';
import { SingleAnalyzePanel } from './analyze/SingleAnalyzePanel';

export function AnalyzePage({ logic }) {
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
    batchRateLimitCooldown,
    setBatchRateLimitCooldown,
    inlineReportPath,
    buildReportUrl,
    PROFILE_MODES,
  } = logic;

  const isAnalyzeScopeLocked = isProfileBusy || isRegenerateBusy || batchSubmitting;

  useEffect(() => {
    if (batchRateLimitCooldown <= 0) return undefined;
    const timer = setInterval(() => {
      setBatchRateLimitCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [batchRateLimitCooldown, setBatchRateLimitCooldown]);

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
        {analyzeSubMode === 'single' ? <SingleAnalyzePanel logic={logic} /> : null}
        {analyzeSubMode === 'batch' ? <BatchAnalyzePanel logic={logic} /> : null}
      </article>

      <ReportPreviewSection
        showFrame={analyzeSubMode !== 'batch' && Boolean(inlineReportPath)}
        inlineReportPath={inlineReportPath}
        buildReportUrl={buildReportUrl}
      />
    </>
  );
}
