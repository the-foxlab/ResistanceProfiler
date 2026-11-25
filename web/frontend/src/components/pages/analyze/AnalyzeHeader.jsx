import { memo, useCallback, useSyncExternalStore } from 'react';
import analyzeIconSrc from '../../../assets/icon-analyze.svg';
import batchIconSrc from '../../../assets/batch.svg';
import { useOptionalUploadProgressStore } from '../../../DashboardContext';

const BATCH_MODES = [
  { id: 'vcf', label: 'VCF' },
  { id: 'fasta', label: 'FASTA' },
  { id: 'json', label: 'JSON' },
];

const EMPTY_PROGRESS = { percent: 0, fileName: '' };
const NOOP_SUBSCRIBE = () => () => {};

const UploadProgressIndicator = memo(function UploadProgressIndicator({
  uploadProgress: uploadProgressProp,
  isUploading,
  isBatchUploading,
  cancelUpload,
  cancelBatchUpload,
}) {
  const store = useOptionalUploadProgressStore();
  const subscribe = useCallback(
    (listener) => (store ? store.subscribe(listener) : NOOP_SUBSCRIBE()),
    [store],
  );
  const getSnapshot = useCallback(
    () => (store ? store.getSnapshot() : EMPTY_PROGRESS),
    [store],
  );
  const storeProgress = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const uploadProgress = uploadProgressProp !== undefined ? uploadProgressProp : storeProgress;

  const rawPercent = Number(uploadProgress?.percent);
  const progressPercent = Number.isFinite(rawPercent)
    ? Math.max(0, Math.min(100, Math.round(rawPercent)))
    : 0;
  const progressValueText = uploadProgress?.fileName
    ? `${progressPercent}% (${uploadProgress.fileName})`
    : `${progressPercent}%`;

  return (
    <div className="analyze-submode-summary">
      {/* Keep upload progress visible in every view, including results. */}
      <div className="analyze-submode-progress">
        <div className="upload-progress">
          <div className="upload-progress-head">
            <span>Upload progress</span>
            <span>{progressPercent}%</span>
            {(isUploading || isBatchUploading) ? (
              <button
                type="button"
                className="upload-cancel-btn"
                onClick={() => (isUploading ? cancelUpload() : cancelBatchUpload())}
                title="Cancel"
                aria-label="Cancel"
              >
                ×
              </button>
            ) : null}
          </div>
          <div
            className="upload-progress-track"
            role="progressbar"
            aria-label="Upload progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressPercent}
            aria-valuetext={progressValueText}
          >
            <div className="upload-progress-fill" style={{ width: `${progressPercent}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
});

export const AnalyzeHeader = memo(function AnalyzeHeader({
  uploadProgress,
  isUploading,
  isBatchUploading,
  cancelUpload,
  cancelBatchUpload,
  analyzeSubMode,
  setAnalyzeSubMode,
  activeProfileMode,
  setActiveProfileMode,
  batchMode,
  setBatchMode,
  batchSubmitted,
  batchSubmitting,
  isProfileBusy,
  isRegenerateBusy,
  isAnalyzeScopeLocked,
  profileModes,
}) {
  return (
    <div className="analyze-shell-header section-header">
      <UploadProgressIndicator
        uploadProgress={uploadProgress}
        isUploading={isUploading}
        isBatchUploading={isBatchUploading}
        cancelUpload={cancelUpload}
        cancelBatchUpload={cancelBatchUpload}
      />

      <div className="analyze-submode-row" role="group" aria-label="Analysis workflow">
        <div className="analyze-submode-controls">
          <div
            className="analyze-submode-switch"
            role="group"
            aria-label="One or multiple samples"
            data-state={analyzeSubMode}
          >
            <button
              type="button"
              className={`analyze-submode-switch-option ${analyzeSubMode === 'single' ? 'active' : ''}`}
              aria-pressed={analyzeSubMode === 'single'}
              onClick={() => {
                setActiveProfileMode('vcf');
                setAnalyzeSubMode('single');
              }}
              disabled={isAnalyzeScopeLocked}
            >
              <span className="sidebar-icon-mask analyze-submode-icon" style={{ '--icon-src': `url(${analyzeIconSrc})` }} aria-hidden="true" />
              One Sample
            </button>
            <button
              type="button"
              className={`analyze-submode-switch-option ${analyzeSubMode === 'batch' ? 'active' : ''}`}
              aria-pressed={analyzeSubMode === 'batch'}
              onClick={() => {
                setBatchMode('vcf');
                setAnalyzeSubMode('batch');
              }}
              disabled={isAnalyzeScopeLocked}
            >
              <span className="sidebar-icon-mask analyze-submode-icon" style={{ '--icon-src': `url(${batchIconSrc})` }} aria-hidden="true" />
              Multiple Samples
            </button>
            <span className="analyze-submode-switch-knob" aria-hidden="true" />
          </div>

          {analyzeSubMode === 'single' ? (
            <div className="profile-settings-row" role="group" aria-label="Profiling mode">
              <div className="database-phenotype-switch" data-active-mode={activeProfileMode}>
                {profileModes.map((mode) => (
                  <button
                    key={mode.id}
                    type="button"
                    className={activeProfileMode === mode.id ? 'active' : ''}
                    aria-pressed={activeProfileMode === mode.id}
                    onClick={() => setActiveProfileMode(mode.id)}
                    disabled={activeProfileMode === 'regenerate' ? isRegenerateBusy : isProfileBusy}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {analyzeSubMode === 'batch' && !batchSubmitted ? (
            <div className="profile-settings-row" role="group" aria-label="Batch mode">
              <div className="database-phenotype-switch" data-active-mode={batchMode}>
                {BATCH_MODES.map((mode) => (
                  <button
                    key={mode.id}
                    type="button"
                    className={batchMode === mode.id ? 'active' : ''}
                    aria-pressed={batchMode === mode.id}
                    onClick={() => setBatchMode(mode.id)}
                    disabled={batchSubmitting}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
});
