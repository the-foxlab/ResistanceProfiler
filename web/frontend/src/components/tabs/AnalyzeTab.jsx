import { useEffect, useRef, useState } from 'react';

import analyzeIconSrc from '../../assets/icon-analyze.svg';
import batchIconSrc from '../../assets/batch.svg';
import exampleIconSrc from '../../assets/example.svg';
import fileIconSrc from '../../assets/file.svg';
import infoIconSrc from '../../assets/info.svg';
import { Spinner } from '../Spinner';

export function AnalyzeTab({
  selectedDatabase,
  vcfInput,
  setVcfInput,
  fastaInput,
  setFastaInput,
  jsonInputId,
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
  runExampleProfile,
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
  batchJsonFiles,
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
  addBatchJsonFiles,
  addBatchBamFiles,
  attachBatchBam,
  removeBatchFile,
  uploadBatchReferenceFasta,
  submitBatch,
  downloadAllBatchArtifacts,
  resetBatch,
  inlineReportPath,
  isAnalyzeScopeLocked,
  PROFILE_MODES,
}) {
  const [reportFrameHeight, setReportFrameHeight] = useState(null);
  const [hostedPlot, setHostedPlot] = useState(null);
  const analyzeSubmodeRowRef = useRef(null);
  const reportFrameRef = useRef(null);
  const [analyzeSubmodeRowWidth, setAnalyzeSubmodeRowWidth] = useState(0);

  const selectedReportOption = reportOptions.find(
    (option) => option.path === selectedProfileReportPath,
  ) || null;

  useEffect(() => {
    setReportFrameHeight(null);
    setHostedPlot(null);
  }, [inlineReportPath]);

  // Derive the origin the embedded report is served from, so we can validate
  // incoming postMessage events against it. In production the report is
  // same-origin (apiBase === ''); in dev it is served from the API host
  // (e.g. http://127.0.0.1:8000). We read it from the iframe's own src so
  // the check stays correct regardless of deployment topology.
  const reportOrigin = (() => {
    try {
      const src = reportFrameRef.current?.src || (inlineReportPath ? buildReportUrl(inlineReportPath) : '');
      return src ? new URL(src).origin : '';
    } catch {
      return '';
    }
  })();

  useEffect(() => {
    const handleMessage = (event) => {
      // Only trust messages from the embedded report iframe's origin.
      if (event.origin !== reportOrigin) {
        return;
      }
      // And only from its contentWindow (defence-in-depth alongside origin).
      if (event.source !== reportFrameRef.current?.contentWindow) {
        return;
      }

      if (event.data?.type === 'respro:open-plot') {
        // The report sends the image src/alt in the payload, so we never need
        // cross-origin contentDocument access (which throws in dev mode).
        if (event.data.src) {
          setHostedPlot({
            src: event.data.src,
            alt: event.data.alt || 'Resistance plot',
          });
        }
        return;
      }

      if (event.data?.type === 'respro:report-height' && typeof event.data.height === 'number') {
        // The report measures its own content height and reports it; this
        // works cross-origin (where contentDocument access throws) and
        // catches late layout shifts via the report's ResizeObserver.
        if (event.data.height > 0) {
          setReportFrameHeight(event.data.height + 2);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [reportOrigin]);

  useEffect(() => {
    if (!hostedPlot) {
      return undefined;
    }

    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setHostedPlot(null);
      }
    };

    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [hostedPlot]);

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
            {selectedDatabase?.has_example ? (
              <button
                type="button"
                className="analyze-submode-btn example-btn"
                onClick={() => {
                  setActiveProfileMode('fasta');
                  setAnalyzeSubMode('single');
                  runExampleProfile();
                }}
                disabled={isProfileBusy}
                title="Load and profile the example consensus FASTA shipped with this database"
              >
                <span className="sidebar-icon-mask analyze-submode-icon" style={{ '--icon-src': `url(${exampleIconSrc})` }} aria-hidden="true" />
                Example
              </button>
            ) : null}
            <p>
              Profile VCF files, consensus FASTA sequences, or regenerate a previous report from JSON.
              BAM files are optional and can be used for coverage analysis.
            </p>
          </div>
          {analyzeSubMode === 'single' || !batchSubmitted ? (
            <div className="analyze-submode-progress">
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
          ) : null}
          <div className="analyze-submode-row" role="group" aria-label="Analysis workflow" ref={analyzeSubmodeRowRef}>
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
                    resetBatch();
                    setAnalyzeSubMode('batch');
                  }}
                  disabled={isAnalyzeScopeLocked}
                >
                  <span className="sidebar-icon-mask analyze-submode-icon" style={{ '--icon-src': `url(${batchIconSrc})` }} aria-hidden="true" />
                  Multiple Samples
                </button>
                <span className="analyze-submode-switch-knob" aria-hidden="true" />
              </div>
            </div>
          </div>
        </div>

        {/* SINGLE SAMPLE submode */}
        {analyzeSubMode === 'single' ? (
          <div className="profile-input-subtile section-subtile">
            <div className="profile-mode-row">
              <div className="profile-settings-row" role="group" aria-label="Profiling mode">
                <div className="database-phenotype-switch" data-active-mode={activeProfileMode}>
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
            </div>

            {activeProfileMode === 'vcf' ? (
              <div className="profile-upload-row profile-upload-row-vcf">
                <label data-tour-target="vcf-file">
                  <span className="input-label-row">VCF file <button type="button" className="input-info-btn" aria-label="VCF help" title="Upload one VCF (.vcf or .vcf.gz) with standard headers. The VCF may be multi-chrom; each CHROM must match one record in the reference FASTA by header name."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
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
                <label data-tour-target="vcf-reference">
                  <span className="input-label-row">Reference FASTA <button type="button" className="input-info-btn" aria-label="Reference FASTA help" title="Reference FASTA must match the VCF coordinate system. May be multi-record (one FASTA record per VCF CHROM); each record header must match a CHROM name."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
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
                <label data-tour-target="vcf-bam">
                  <span className="label-text input-label-row">BAM file <span className="field-optional">(optional)</span> <button type="button" className="input-info-btn" aria-label="BAM help" title="Optional sorted BAM. A BAM index is generated automatically. Used for coverage evaluation."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
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
                <label data-tour-target="vcf-sample-name">
                  Sample name
                  <input
                    className="sample-name-input"
                    value={vcfInput.sample}
                    disabled={isProfileBusy}
                    onChange={(event) => setVcfInput({ ...vcfInput, sample: event.target.value })}
                  />
                </label>
                <label data-tour-target="vcf-frequency-cutoff">
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
                <label data-tour-target="vcf-coverage-cutoff">
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
                    ? (isRegenerateBusy || !jsonInputId) 
                    : activeProfileMode === 'vcf'
                      ? (isProfileBusy || !vcfInput.vcf_id || !vcfInput.reference_id)
                      : (isProfileBusy || !fastaInput.fasta_id)
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
              <button
                type="button"
                className="button-link report-action-btn download-action-btn"
                onClick={() => {
                  if (selectedReportOption?.tsvPath) {
                    window.open(buildArtifactUrl(selectedReportOption.tsvPath), '_blank', 'noopener,noreferrer');
                  }
                }}
                disabled={!selectedReportOption?.tsvPath}
              >
                Download TSV
              </button>
            </div>

            {!inlineReportPath ? (
              <div className="report-placeholder">
                <div className="report-placeholder-icon-wrap">
                  <img src={fileIconSrc} alt="" className="report-placeholder-icon" aria-hidden="true" />
                </div>
                <p className="report-placeholder-text">Your analysis report will open here</p>
              </div>
            ) : null}
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
                  <div className="database-phenotype-switch" data-active-mode={batchMode}>
                    <button
                      type="button"
                      className={batchMode === 'vcf' ? 'active' : ''}
                      onClick={() => setBatchMode('vcf')}
                      disabled={batchSubmitting}
                    >
                      VCF
                    </button>
                    <button
                      type="button"
                      className={batchMode === 'fasta' ? 'active' : ''}
                      onClick={() => setBatchMode('fasta')}
                      disabled={batchSubmitting}
                    >
                      FASTA
                    </button>
                    <button
                      type="button"
                      className={batchMode === 'json' ? 'active' : ''}
                      onClick={() => setBatchMode('json')}
                      disabled={batchSubmitting}
                    >
                      JSON
                    </button>
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
                  <label>
                    <span className="input-label-row">BAM files <span className="field-optional">(optional)</span> <button type="button" className="input-info-btn" aria-label="Batch BAM help" title="Upload one or more BAM files, used for per-sample coverage evaluation. Each BAM is auto-paired to the VCF with the same filename stem (e.g. sample1.vcf ↔ sample1.bam). Unmatched or already-paired cases are reported near the Analyze button and can be fixed per row."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="file"
                      multiple
                      accept=".bam"
                      disabled={batchSubmitting}
                      onChange={async (event) => {
                        if (!event.target.files) {
                          return;
                        }
                        await addBatchBamFiles(event.target.files);
                        event.target.value = '';
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
              ) : batchMode === 'fasta' ? (
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
              ) : (
                <div className="profile-upload-row profile-upload-row-batch-json">
                  <label>
                    <span className="input-label-row">Results JSON files <button type="button" className="input-info-btn" aria-label="Batch JSON help" title="Upload one or more prior results JSON files exported by ResistanceProfiler. Each JSON is matched to its database by a unique project fingerprint."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
                    <input
                      type="file"
                      multiple
                      accept=".json"
                      disabled={batchSubmitting}
                      onChange={(event) => {
                        if (!event.target.files) {
                          return;
                        }
                        addBatchJsonFiles(event.target.files);
                        event.target.value = '';
                      }}
                    />
                  </label>
                </div>
              )}

              {/* Uploaded file list */}
              {(() => {
                const batchFiles = batchMode === 'vcf' ? batchVcfFiles : batchMode === 'fasta' ? batchFastaFiles : batchJsonFiles;
                const fileColumnLabel = batchMode === 'vcf' ? 'VCF file' : batchMode === 'fasta' ? 'FASTA file' : 'JSON file';
                return batchFiles.length > 0 ? (
                <div className="profile-upload-row profile-upload-row-batch-files">
                  <p className="field-optional">
                    {batchFiles.length} / {batchMaxSamples} files
                    {batchFiles.length >= batchMaxSamples ? (
                      <span style={{ color: 'var(--color-error, #c2410c)', marginLeft: '0.4em' }}>Limit reached</span>
                    ) : null}
                  </p>
                  <div className="table-wrap batch-uploaded-table-wrap">
                    <table className="batch-uploaded-table">
                      <thead>
                        <tr>
                          <th>{fileColumnLabel}</th>
                          <th>Size</th>
                          {batchMode === 'vcf' ? <th>BAM file</th> : null}
                          {batchMode === 'vcf' ? <th>BAM size</th> : null}
                          {batchMode === 'vcf' ? <th>Upload BAM</th> : null}
                          <th aria-label="Remove row" />
                        </tr>
                      </thead>
                      <tbody>
                        {batchFiles.map((file, index) => (
                          <tr key={file.uploadId}>
                            <td className="batch-uploaded-cell-name" title={file.name}>{file.name}</td>
                            <td className="batch-uploaded-cell-size field-optional">{Math.round(file.size / 1024)} KB</td>
                            {batchMode === 'vcf' ? (
                              <td className="batch-uploaded-cell-bam-name" title={file.bamName || 'No BAM'}>
                                {file.bamName || '—'}
                              </td>
                            ) : null}
                            {batchMode === 'vcf' ? (
                              <td className="batch-uploaded-cell-bam-size field-optional">
                                {file.bamSize != null ? `${Math.round(file.bamSize / 1024)} KB` : '—'}
                              </td>
                            ) : null}
                            {batchMode === 'vcf' ? (
                              <td className="batch-uploaded-cell-bam-upload">
                                <label className="batch-uploaded-bam-attach">
                                  <input
                                    type="file"
                                    accept=".bam"
                                    disabled={batchSubmitting}
                                    aria-label={file.bamName ? `Replace BAM for ${file.name}` : `Attach BAM to ${file.name}`}
                                    onChange={(event) => {
                                      if (event.target.files && event.target.files[0]) {
                                        attachBatchBam(index, event.target.files[0]);
                                      }
                                      event.target.value = '';
                                    }}
                                  />
                                </label>
                              </td>
                            ) : null}
                            <td className="batch-uploaded-cell-remove">
                              <button
                                type="button"
                                className="button-link batch-remove-file-btn"
                                onClick={() => removeBatchFile(index)}
                                disabled={batchSubmitting}
                                aria-label={`Remove ${file.name}`}
                              >
                                ✕
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null;
              })()}

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
                    || (batchMode === 'vcf' ? batchVcfFiles : batchMode === 'fasta' ? batchFastaFiles : batchJsonFiles).length === 0
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
                      <th>TSV</th>
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
                        <td>{sample.reportTsvPath ? <a href={buildArtifactUrl(sample.reportTsvPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
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
            ref={reportFrameRef}
            title="ResistanceProfiler report"
            src={buildReportUrl(inlineReportPath)}
            className="workspace-frame"
            sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads"
            style={reportFrameHeight ? { height: `${reportFrameHeight}px` } : undefined}
            onLoad={(event) => {
              try {
                const frameDoc = event.currentTarget.contentDocument;
                if (!frameDoc || !frameDoc.body || !frameDoc.documentElement) {
                  return;
                }
                const nextHeight = Math.max(
                  frameDoc.body.scrollHeight,
                  frameDoc.documentElement.scrollHeight,
                );
                setReportFrameHeight(nextHeight + 2);
              } catch {
                // Cross-origin (dev mode): contentDocument access throws.
                // Leave the height unset so the CSS min-height floor on
                // .workspace-frame keeps the frame visible; the report's
                // ResizeObserver will post a respro:report-height message
                // once it mounts and set a precise height.
                setReportFrameHeight(null);
              }
            }}
          />
        </article>
      ) : null}

      {hostedPlot ? (
        <div className="report-preview-plot-modal" role="dialog" aria-modal="true" aria-label="Resistance plot">
          <div className="report-preview-plot-backdrop" onClick={() => setHostedPlot(null)} aria-hidden="true" />
          <section className="report-preview-plot-panel">
            <button
              type="button"
              className="report-preview-plot-close"
              aria-label="Close resistance plot"
              onClick={() => setHostedPlot(null)}
              autoFocus
            >
              &times;
            </button>
            <img src={hostedPlot.src} alt={hostedPlot.alt} className="report-preview-plot-image" />
          </section>
        </div>
      ) : null}
    </>
  );
}
