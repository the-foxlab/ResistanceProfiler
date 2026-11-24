import analyzeIconSrc from '../../../assets/icon-analyze.svg';
import batchIconSrc from '../../../assets/batch.svg';

const BATCH_MODES = [
  { id: 'vcf', label: 'VCF' },
  { id: 'fasta', label: 'FASTA' },
  { id: 'json', label: 'JSON' },
];

export function AnalyzeHeader({
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
      <div className="analyze-submode-summary">
        {/* Keep upload progress visible in every view, including results. */}
        <div className="analyze-submode-progress">
          <div className="upload-progress" aria-label="Upload progress">
            <div className="upload-progress-head">
              <span>Upload progress</span>
              <span>{uploadProgress.percent}%</span>
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
            <div className="upload-progress-track" aria-hidden="true">
              <div className="upload-progress-fill" style={{ width: `${uploadProgress.percent}%` }} />
            </div>
          </div>
        </div>
      </div>

      <div className="analyze-submode-row" role="group" aria-label="Analysis workflow">
        <div className="analyze-submode-controls">
          <div
            className="analyze-submode-switch"
            role="switch"
            aria-label="One or multiple samples"
            aria-checked={analyzeSubMode === 'batch'}
            data-state={analyzeSubMode}
          >
            <button
              type="button"
              className={`analyze-submode-switch-option ${analyzeSubMode === 'single' ? 'active' : ''}`}
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
}
