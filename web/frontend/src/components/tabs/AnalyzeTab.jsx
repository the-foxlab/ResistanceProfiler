import { useEffect, useRef, useState } from 'react';

import analyzeIconSrc from '../../assets/icon-analyze.svg';
import batchIconSrc from '../../assets/batch.svg';
import infoIconSrc from '../../assets/info.svg';
import { Spinner } from '../Spinner';

const ANALYZE_SUBMODES = [
  { id: 'single', label: 'One Sample', iconSrc: analyzeIconSrc },
  { id: 'batch', label: 'Multiple Samples', iconSrc: batchIconSrc },
];

export function AnalyzeTab({
  selectedDatabase,
  vcfInput,
  setVcfInput,
  fastaInput,
  setFastaInput,
  jsonInputPath,
  isRegenerateBusy,
  runRegenerateFromJson,
  uploadFastaFile,
  uploadVcfFile,
  uploadReferenceFile,
  uploadBamFile,
  uploadJsonFile,
  uploadProgress,
  activeProfileMode,
  setActiveProfileMode,
  analyzeSubMode,
  setAnalyzeSubMode,
  isProfileBusy,
  canCancelJob,
  isCancelingJob,
  cancelActiveJob,
  runSelectedProfile,
  statusError,
  selectedProfileReportPath,
  setSelectedProfileReportPath,
  reportOptions,
  buildReportUrl,
  buildArtifactUrl,
  batchMode,
  setBatchMode,
  batchVcfFiles,
  batchFastaFiles,
  batchReferenceFasta,
  batchSamples,
  batchSubmitting,
  isBatchDownloadBusy,
  batchError,
  batchRateLimitCooldown,
  setBatchRateLimitCooldown,
  batchSubmitted,
  batchMaxSamples,
  sampleLimitPerMinute,
  batchVcfCutoffs,
  setBatchVcfCutoffs,
  addBatchVcfFiles,
  addBatchFastaFiles,
  removeBatchFile,
  uploadBatchReferenceFasta,
  submitBatch,
  downloadAllBatchArtifacts,
  resetBatch,
  inlineReportPath,
  isAnalyzeScopeLocked,
  PROFILE_MODES,
}) {
  const [reportFrameHeight, setReportFrameHeight] = useState(900);
  const analyzeSubmodeRowRef = useRef(null);
  const [analyzeSubmodeRowWidth, setAnalyzeSubmodeRowWidth] = useState(0);

  const selectedReportOption = reportOptions.find(
    (option) => option.path === selectedProfileReportPath,
  ) || null;

  useEffect(() => {
    // Count down the batch rate-limit cooldown each second until it reaches zero.
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

  useEffect(() => {
    const row = analyzeSubmodeRowRef.current;
    if (!row) {
      return undefined;
    }

    const updateWidth = () => {
      const nextWidth = Math.ceil(row.getBoundingClientRect().width);
      setAnalyzeSubmodeRowWidth(nextWidth);
    };

    updateWidth();

    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {
        updateWidth();
      });
      observer.observe(row);
      return () => {
        observer.disconnect();
      };
    }

    window.addEventListener('resize', updateWidth);
    return () => {
      window.removeEventListener('resize', updateWidth);
    };
  }, [isProfileBusy, isRegenerateBusy, batchSubmitting]);

  return (
    <>
      <article className="card profile-input-card tab-primary-tile">
        <div className="analyze-shell-header section-header">
          <div>
            <h2>{analyzeSubMode === 'batch' ? 'Analyze multiple samples' : 'Analyze'}</h2>
            <p>
              {analyzeSubMode === 'batch'
                ? 'Submit multiple sequence file at once (max 25 per batch and minute).'
                : 'Profile vcf files or consensus fasta sequence or regenerate a previous report'}
            </p>
          </div>
          <div className="analyze-submode-row" role="group" aria-label="Analysis workflow" ref={analyzeSubmodeRowRef}>
            {ANALYZE_SUBMODES.map((subMode) => (
              <button
                key={subMode.id}
                type="button"
                className={`analyze-submode-btn ${analyzeSubMode === subMode.id ? 'active' : ''}`}
                onClick={() => {
                  if (subMode.id === 'single') {
                    setActiveProfileMode('vcf');
                    setAnalyzeSubMode('single');
                    return;
                  }

                  if (subMode.id === 'batch') {
                    setBatchMode('vcf');
                    resetBatch();
                    setAnalyzeSubMode('batch');
                    return;
                  }
                }}
                disabled={isAnalyzeScopeLocked}
              >
                <span className="sidebar-icon-mask analyze-submode-icon" style={{ '--icon-src': `url(${subMode.iconSrc})` }} aria-hidden="true" />
                {subMode.label}
              </button>
            ))}
          </div>
        </div>

        {/* SINGLE SAMPLE submode */}
        {analyzeSubMode === 'single' ? (
          <div className="profile-input-subtile section-subtile">
            <div className="profile-mode-row">
              <div className="profile-settings-row" role="group" aria-label="Profiling mode">
                <div className="database-phenotype-switch">
                  {PROFILE_MODES.map((mode) => (
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
              <div className="upload-progress" aria-label="Upload progress">
                <div className="upload-progress-head">
                  <span>Upload progress</span>
                  <span>{uploadProgress.percent}%</span>
                </div>
                <div className="upload-progress-track" aria-hidden="true">
                  <div className="upload-progress-fill" style={{ width: `${uploadProgress.percent}%` }} />
                </div>
              </div>
            </div>

            {activeProfileMode === 'vcf' ? (
              <div className="profile-upload-row profile-upload-row-vcf">
                <label>
                  <span className="input-label-row">VCF file <button type="button" className="input-info-btn" aria-label="VCF help" title="Upload one VCF (.vcf or .vcf.gz) with standard headers."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="file"
                    accept=".vcf,.vcf.gz"
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      if (event.target.files && event.target.files[0]) {
                        uploadVcfFile(event.target.files[0]);
                      }
                    }}
                  />
                </label>
                <label>
                  <span className="input-label-row">Reference FASTA <button type="button" className="input-info-btn" aria-label="Reference FASTA help" title="Reference FASTA must match the VCF coordinate system."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="file"
                    accept=".fasta,.fa,.fna"
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      if (event.target.files && event.target.files[0]) {
                        uploadReferenceFile(event.target.files[0]);
                      }
                    }}
                  />
                </label>
                <label>
                  <span className="label-text input-label-row">BAM file <span className="field-optional">(optional)</span> <button type="button" className="input-info-btn" aria-label="BAM help" title="Optional sorted BAM. A BAM index is generated automatically."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="file"
                    accept=".bam"
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      if (event.target.files && event.target.files[0]) {
                        uploadBamFile(event.target.files[0]);
                      }
                    }}
                  />
                </label>
                <label>
                  Sample name
                  <input
                    className="sample-name-input"
                    value={vcfInput.sample}
                    disabled={isProfileBusy}
                    onChange={(event) => setVcfInput({ ...vcfInput, sample: event.target.value })}
                  />
                </label>
                <label>
                  <span className="label-text input-label-row">Frequency cutoff <button type="button" className="input-info-btn" aria-label="Frequency cutoff help" title="Minimum allele frequency from 0 to 1. Variants below this value are ignored."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.001"
                    value={vcfInput.min_af}
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (!Number.isFinite(value)) {
                        return;
                      }
                      setVcfInput({ ...vcfInput, min_af: value });
                    }}
                  />
                </label>
                <label>
                  <span className="label-text input-label-row">Coverage cutoff <button type="button" className="input-info-btn" aria-label="Coverage cutoff help" title="Minimum read depth required for including a position."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={vcfInput.min_depth}
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (!Number.isFinite(value)) {
                        return;
                      }
                      setVcfInput({ ...vcfInput, min_depth: Math.trunc(value) });
                    }}
                  />
                </label>
              </div>
            ) : null}

            {activeProfileMode === 'fasta' ? (
              <div className="profile-upload-row profile-upload-row-fasta">
                <label>
                  <span className="input-label-row">FASTA file <button type="button" className="input-info-btn" aria-label="FASTA help" title="Upload one consensus FASTA sequence file."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="file"
                    accept=".fasta,.fa,.fna,.faa"
                    disabled={isProfileBusy}
                    onChange={(event) => {
                      if (event.target.files && event.target.files[0]) {
                        uploadFastaFile(event.target.files[0]);
                      }
                    }}
                  />
                </label>
                <label>
                  Sample name
                  <input
                    className="sample-name-input"
                    value={fastaInput.sample}
                    disabled={isProfileBusy}
                    onChange={(event) => setFastaInput({ ...fastaInput, sample: event.target.value })}
                  />
                </label>
              </div>
            ) : null}

            {activeProfileMode === 'regenerate' ? (
              <div className="profile-upload-row profile-upload-row-regenerate">
                <label>
                  <span className="input-label-row">Results JSON <button type="button" className="input-info-btn" aria-label="Results JSON help" title="Upload a prior results JSON exported by ResistanceProfiler. This is matched by a unique db ID. After db updates, the json regenerate will not work."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                  <input
                    type="file"
                    accept=".json"
                    onChange={(event) => {
                      if (event.target.files && event.target.files[0]) {
                        uploadJsonFile(event.target.files[0]);
                      }
                    }}
                  />
                </label>
              </div>
            ) : null}

            <div className="profile-analyze-row">
              <button
                type="button"
                className="analyze-primary"
                onClick={() => {
                  if (activeProfileMode === 'regenerate') {
                    runRegenerateFromJson();
                    return;
                  }
                  runSelectedProfile();
                }}
                disabled={
                  activeProfileMode === 'regenerate' 
                    ? (isRegenerateBusy || !jsonInputPath) 
                    : activeProfileMode === 'vcf'
                      ? (isProfileBusy || !vcfInput.vcf_path || !vcfInput.ref_fasta_path)
                      : (isProfileBusy || !fastaInput.fasta_path)
                }
              >
                {(activeProfileMode === 'regenerate' ? isRegenerateBusy : isProfileBusy) ? (
                  <>
                    <Spinner /> {activeProfileMode === 'regenerate' ? 'Regenerate' : 'Analyze'}
                  </>
                ) : (
                  activeProfileMode === 'regenerate' ? 'Regenerate' : 'Analyze'
                )}
              </button>
              {canCancelJob ? (
                <button
                  type="button"
                  className="button-link report-action-btn"
                  onClick={() => cancelActiveJob()}
                  disabled={isCancelingJob}
                >
                  {isCancelingJob ? 'Canceling...' : 'Cancel job'}
                </button>
              ) : null}
              {selectedDatabase && !isProfileBusy && !isRegenerateBusy ? (
                <span className="field-optional" style={{ marginLeft: '0.5rem' }}>using {selectedDatabase.display_name}</span>
              ) : null}
              {statusError ? (
                <p className="status" style={{ color: 'var(--color-error, #c2410c)', marginLeft: '1rem' }}>{statusError}</p>
              ) : null}
            </div>

            <div className="inline-actions report-actions analyze-report-actions">
              <select
                value={selectedProfileReportPath}
                onChange={(event) => setSelectedProfileReportPath(event.target.value)}
                disabled={reportOptions.length === 0}
              >
                {reportOptions.length === 0 ? (
                  <option value="">No previous reports available</option>
                ) : null}
                {reportOptions.map((option) => (
                  <option key={option.path} value={option.path}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="button-link report-action-btn"
                onClick={() => {
                  if (selectedProfileReportPath) {
                    window.open(buildReportUrl(selectedProfileReportPath), '_blank', 'noopener,noreferrer');
                  }
                }}
                disabled={!selectedProfileReportPath}
              >
                Open in new tab
              </button>
              <button
                type="button"
                className="button-link report-action-btn download-action-btn"
                onClick={() => {
                  if (selectedReportOption?.pdfPath) {
                    window.open(buildArtifactUrl(selectedReportOption.pdfPath), '_blank', 'noopener,noreferrer');
                  }
                }}
                disabled={!selectedReportOption?.pdfPath}
              >
                Download PDF
              </button>
              <button
                type="button"
                className="button-link report-action-btn download-action-btn"
                onClick={() => {
                  if (selectedReportOption?.jsonPath) {
                    window.open(buildArtifactUrl(selectedReportOption.jsonPath), '_blank', 'noopener,noreferrer');
                  }
                }}
                disabled={!selectedReportOption?.jsonPath}
              >
                Download JSON
              </button>
            </div>
          </div>
        ) : null}

        {/* BATCH submode */}
        {analyzeSubMode === 'batch' ? (
          <div className="profile-input-subtile section-subtile">
          {!batchSubmitted ? (
            <>
              {/* Mode sub-selector */}
              <div className="profile-mode-row">
                <div className="profile-settings-row" role="group" aria-label="Batch mode">
                  <div className="database-phenotype-switch">
                    <button
                      type="button"
                      className={batchMode === 'vcf' ? 'active' : ''}
                      onClick={() => setBatchMode('vcf')}
                      disabled={batchSubmitting}
                    >
                      VCF batch
                    </button>
                    <button
                      type="button"
                      className={batchMode === 'fasta' ? 'active' : ''}
                      onClick={() => setBatchMode('fasta')}
                      disabled={batchSubmitting}
                    >
                      FASTA batch
                    </button>
                  </div>
                </div>
                <div className="upload-progress" aria-label="Batch upload progress">
                  <div className="upload-progress-head">
                    <span>Upload progress</span>
                    <span>{uploadProgress.percent}%</span>
                  </div>
                  <div className="upload-progress-track" aria-hidden="true">
                    <div className="upload-progress-fill" style={{ width: `${uploadProgress.percent}%` }} />
                  </div>
                </div>
              </div>

              {/* File upload area */}
              {batchMode === 'vcf' ? (
                <div className="profile-upload-row profile-upload-row-batch-vcf">
                  <label>
                    <span className="input-label-row">VCF files <button type="button" className="input-info-btn" aria-label="Batch VCF help" title="Upload one or more VCF files (.vcf or .vcf.gz)."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="file"
                      multiple
                      accept=".vcf,.vcf.gz"
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        if (!event.target.files) {
                          return;
                        }
                        addBatchVcfFiles(event.target.files);
                        event.target.value = '';
                      }}
                    />
                  </label>
                  <label>
                    <span className="label-text input-label-row">Shared reference FASTA <span className="field-optional">(required)</span> <button type="button" className="input-info-btn" aria-label="Batch reference help" title="Shared reference FASTA for all uploaded VCF files."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="file"
                      accept=".fasta,.fa,.fna"
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        if (event.target.files && event.target.files[0]) {
                          uploadBatchReferenceFasta(event.target.files[0]);
                        }
                      }}
                    />
                  </label>
                  <label className="batch-settings-label">
                    <span className="label-text input-label-row">Frequency cutoff <button type="button" className="input-info-btn" aria-label="Batch frequency cutoff help" title="Minimum allele frequency from 0 to 1 for all batch VCF runs."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.001"
                      value={batchVcfCutoffs.min_af}
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (!Number.isFinite(value)) {
                          return;
                        }
                        setBatchVcfCutoffs((prev) => ({ ...prev, min_af: value }));
                      }}
                    />
                  </label>
                  <label className="batch-settings-label">
                    <span className="label-text input-label-row">Coverage cutoff <button type="button" className="input-info-btn" aria-label="Batch coverage cutoff help" title="Minimum read depth for all batch VCF runs."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={batchVcfCutoffs.min_depth}
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (!Number.isFinite(value)) {
                          return;
                        }
                        setBatchVcfCutoffs((prev) => ({ ...prev, min_depth: Math.trunc(value) }));
                      }}
                    />
                  </label>
                </div>
              ) : (
                <div className="profile-upload-row">
                  <label>
                    <span className="input-label-row">FASTA files <button type="button" className="input-info-btn" aria-label="Batch FASTA help" title="Upload one or more consensus FASTA files."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="file"
                      multiple
                      accept=".fasta,.fa,.fna"
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        if (!event.target.files) {
                          return;
                        }
                        addBatchFastaFiles(event.target.files);
                        event.target.value = '';
                      }}
                    />
                  </label>
                </div>
              )}

              {/* Uploaded file list */}
              {(batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles).length > 0 ? (
                <div className="profile-upload-row profile-upload-row-batch-files">
                  <p className="field-optional">
                    {(batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles).length} / {batchMaxSamples} files
                    {(batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles).length >= batchMaxSamples ? (
                      <span style={{ color: 'var(--color-error, #c2410c)', marginLeft: '0.4em' }}>Limit reached</span>
                    ) : null}
                  </p>
                  <ul className="batch-uploaded-file-list">
                    {(batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles).map((file, index) => (
                      <li key={file.path} className="batch-uploaded-file-row">
                        <span className="batch-uploaded-file-name" title={file.name}>{file.name}</span>
                        <span className="batch-uploaded-file-size field-optional">{Math.round(file.size / 1024)} KB</span>
                        <button
                          type="button"
                          className="button-link batch-remove-file-btn"
                          onClick={() => removeBatchFile(index)}
                          disabled={batchSubmitting}
                          aria-label={`Remove ${file.name}`}
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {/* Shared reference FASTA selection status (VCF mode) */}
              {batchMode === 'vcf' && batchReferenceFasta ? (
                <p className="field-optional">Uploaded reference: {batchReferenceFasta.name}</p>
              ) : null}

              {/* Batch submit and status */}
              <div className="profile-analyze-row">
                <button
                  type="button"
                  className="analyze-primary"
                  onClick={() => submitBatch()}
                  disabled={
                    batchSubmitting
                    || batchRateLimitCooldown > 0
                    || (batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles).length === 0
                    || (batchMode === 'vcf' && !batchReferenceFasta)
                  }
                >
                  {batchSubmitting ? (
                    <>
                      <Spinner /> Submitting...
                    </>
                  ) : (
                    'Submit batch'
                  )}
                </button>
                {selectedDatabase && !batchSubmitting ? (
                  <span className="field-optional" style={{ marginLeft: '0.5rem' }}>using {selectedDatabase.display_name}</span>
                ) : null}
              </div>
              {batchRateLimitCooldown > 0 ? <p className="status">{`Rate limit reached. Try again in ${batchRateLimitCooldown}s.`}</p> : null}
              {batchError ? <p className="status" style={{ color: 'var(--color-error, #c2410c)' }}>{batchError}</p> : null}
            </>
          ) : (
            <>
              {batchError ? (
                <p className="status" style={{ color: 'var(--color-error, #c2410c)' }}>{batchError}</p>
              ) : null}
              <div className="table-wrap mutation-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Sample</th>
                      <th>Status</th>
                      <th>Error</th>
                      <th>HTML</th>
                      <th>PDF</th>
                      <th>JSON</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batchSamples.map((sample) => (
                      <tr key={sample.job_id}>
                        <td>{sample.sample_name}</td>
                        <td>
                          <span className={`job-status-badge status-${sample.status}`}>
                            {sample.status}
                          </span>
                        </td>
                        <td>{sample.errorMessage || '—'}</td>
                        <td>{sample.reportHtmlPath ? <a href={buildReportUrl(sample.reportHtmlPath)} target="_blank" rel="noreferrer">View</a> : '—'}</td>
                        <td>{sample.reportPdfPath ? <a href={buildArtifactUrl(sample.reportPdfPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                        <td>{sample.reportJsonPath ? <a href={buildArtifactUrl(sample.reportJsonPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="profile-analyze-row">
                <button
                  type="button"
                  className="analyze-primary"
                  onClick={() => downloadAllBatchArtifacts()}
                  disabled={isBatchDownloadBusy}
                >
                  {isBatchDownloadBusy ? (
                    <>
                      <Spinner /> Preparing...
                    </>
                  ) : (
                    'Download all'
                  )}
                </button>
                <button
                  type="button"
                  className="analyze-primary"
                  onClick={() => resetBatch()}
                >
                  New batch
                </button>
              </div>
            </>
          )}
          </div>
        ) : null}

      </article>

      {/* Report preview in its own tile below the analyze inputs/actions */}
      {analyzeSubMode !== 'batch' && inlineReportPath ? (
        <article className="card full-width-tile tab-primary-tile">
          <iframe
            title="ResistanceProfiler report"
            src={buildReportUrl(inlineReportPath)}
            className="workspace-frame"
            sandbox="allow-scripts allow-same-origin"
            style={{ height: `${reportFrameHeight}px` }}
            onLoad={(event) => {
              try {
                const frameDoc = event.currentTarget.contentDocument;
                if (!frameDoc || !frameDoc.body || !frameDoc.documentElement) {
                  return;
                }
                const nextHeight = Math.max(
                  frameDoc.body.scrollHeight,
                  frameDoc.documentElement.scrollHeight,
                  900,
                );
                setReportFrameHeight(nextHeight + 24);
              } catch {
                setReportFrameHeight(900);
              }
            }}
          />
        </article>
      ) : null}
    </>
  );
}
