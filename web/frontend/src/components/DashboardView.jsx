import { useEffect, useMemo, useRef, useState } from 'react';

import logoSrc from '../assets/logo.svg';
import aboutIconSrc from '../assets/icon-about.svg';
import analyzeIconSrc from '../assets/icon-analyze.svg';
import aboutScopeIconSrc from '../assets/icon-scope.svg';
import aboutNomenclatureIconSrc from '../assets/icon-nomenclature.svg';
import aboutCliIconSrc from '../assets/icon-cli.svg';
import aboutCommunityIconSrc from '../assets/icon-community.svg';
import databaseIconSrc from '../assets/icon-database.svg';
import githubIconSrc from '../assets/icon-github.svg';
import websiteIconSrc from '../assets/website.svg';
import mutationsIconSrc from '../assets/search.svg';
import batchIconSrc from '../assets/batch.svg';
import { FRONTEND_CONFIG } from '../config';
import { buildDatabasePlots } from './database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from './database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from './database-plots/DatabasePositionPlot';
import { DatabaseDrugDistributionPlot } from './database-plots/DatabaseDrugDistributionPlot';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { Spinner } from './Spinner';
import regenerateIconSrc from '../assets/icon-regenerate.svg';
import homeIconSrc from '../assets/home.svg';
import reportIconSrc from '../assets/reports.svg';
import infoIconSrc from '../assets/info.svg';
import searchIconSrc from '../assets/search.svg';
import resetFilterIconSrc from '../assets/reset_filter.svg';

const MODES = [
  { id: 'analyze', label: 'Analysis', iconSrc: homeIconSrc },
  { id: 'results', label: 'Reports', iconSrc: reportIconSrc },
  { id: 'database', label: 'Database Dashboard', iconSrc: databaseIconSrc },
  { id: 'mutations', label: 'Browse Mutations', iconSrc: mutationsIconSrc },
  { id: 'about', label: 'About', iconSrc: aboutIconSrc },
];

const ANALYZE_SUBMODES = [
  { id: 'single', label: 'One Sample', iconSrc: analyzeIconSrc },
  { id: 'batch', label: 'Multiple Samples', iconSrc: batchIconSrc },
];

function _isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function _renderPmidLinks(value) {
  const pmidText = String(value);
  const pmids = Array.from(new Set(pmidText.match(/\d+/g) || []));
  if (pmids.length === 0) {
    return pmidText;
  }
  return (
    <>
      {pmids.map((pmid, index) => (
        <span key={pmid}>
          {index > 0 ? ', ' : ''}
          <a
            href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
            target="_blank"
            rel="noreferrer"
          >
            PMID:{pmid}
          </a>
        </span>
      ))}
    </>
  );
}

function _renderDatabaseMetaValue(entry) {
  const valueText = String(entry.value).trim();
  if (entry.key === 'website') {
    return (
      <a href={valueText} target="_blank" rel="noreferrer">{valueText}</a>
    );
  }
  if (entry.key === 'publication_doi') {
    const doi = valueText.replace(/^https?:\/\/doi\.org\//i, '');
    return (
      <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer">
        {valueText}
      </a>
    );
  }
  if (entry.key === 'publication_pmid') {
    return _renderPmidLinks(valueText);
  }
  if (entry.key === 'contact' && valueText.includes('@') && !valueText.includes('mailto:')) {
    return (
      <a href={`mailto:${valueText}`}>{valueText}</a>
    );
  }
  return entry.value;
}

export function DashboardView({
  API_BASE,
  PROFILE_MODES,
  vcfInput,
  setVcfInput,
  fastaInput,
  setFastaInput,
  rules,
  formulaRules,
  databases,
  selectedDatabase,
  selectedDatabaseId,
  setSelectedDatabaseId,
  statusError,
  selectedProfileReportPath,
  setSelectedProfileReportPath,
  mutationFilter,
  setMutationFilter,
  mutationFilterColumn,
  setMutationFilterColumn,
  mutationSortColumn,
  setMutationSortColumn,
  mutationSortAsc,
  setMutationSortAsc,
  formulaFilter,
  setFormulaFilter,
  formulaFilterColumn,
  setFormulaFilterColumn,
  mutationsLoaded,
  activeMode,
  setActiveMode,
  activeProfileMode,
  setActiveProfileMode,
  analyzeSubMode,
  setAnalyzeSubMode,
  sessionResults,
  inlineReportPath,
  inlineReportLabel,
  mutationColumns,
  formulaColumns,
  mutationPlotMeta,
  displayedRules,
  displayedFormulaRules,
  reportOptions,
  isProfileBusy,
  canCancelJob,
  isCancelingJob,
  cancelActiveJob,
  runSelectedProfile,
  buildReportUrl,
  buildArtifactUrl,
  uploadFastaFile,
  uploadVcfFile,
  uploadReferenceFile,
  uploadBamFile,
  uploadJsonFile,
  jsonInputPath,
  isRegenerateBusy,
  runRegenerateFromJson,
  downloadMutationsAsTsv,
  downloadFormulaRulesAsTsv,
  uploadProgress,
  // Batch
  batchMode,
  setBatchMode,
  batchVcfFiles,
  batchFastaFiles,
  batchReferenceFasta,
  batchSamples,
  batchSubmitting,
  isBatchDownloadBusy,
  isSessionDownloadBusy,
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
  downloadAllSessionArtifacts,
  resetBatch,
}) {
  // These controls only affect database charts, not mutation browsing or profiling.
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);
  const [reportFrameHeight, setReportFrameHeight] = useState(900);
  const analyzeSubmodeRowRef = useRef(null);
  const [analyzeSubmodeRowWidth, setAnalyzeSubmodeRowWidth] = useState(0);

  const {
    summaryTile,
    ic50Sections,
    detailSections,
    phenotypeMode,
    binSize,
  } = useMemo(
    () => buildDatabasePlots(
      rules,
      formulaRules,
      mutationPlotMeta,
      requestedPhenotypeMode,
      requestedBinSize,
    ),
    [
      rules,
      formulaRules,
      mutationPlotMeta,
      requestedPhenotypeMode,
      requestedBinSize,
    ]
  );
  const activePhenotypeMode = phenotypeMode.activeMode;
  const selectedReportOption = useMemo(
    () => reportOptions.find((option) => option.path === selectedProfileReportPath) || null,
    [reportOptions, selectedProfileReportPath]
  );
  const isAnalyzeScopeLocked = isProfileBusy || isRegenerateBusy || batchSubmitting;

  const databaseInfoEntries = useMemo(() => {
    if (!selectedDatabase) {
      return [];
    }

    const metadata = selectedDatabase.metadata || {};
    const entries = [
      { key: 'display_name', label: 'Database name', value: selectedDatabase.display_name },
      { key: 'uuid', label: 'UUID', value: selectedDatabase.uuid },
      { key: 'created_at', label: 'Created at', value: selectedDatabase.created_at },
      { key: 'schema_version', label: 'Schema version', value: selectedDatabase.schema_version },
      { key: 'maintainers', label: 'Maintainers', value: metadata.maintainers },
      { key: 'contact', label: 'Contact', value: metadata.contact },
      { key: 'publication_pmid', label: 'Publication PMID', value: metadata.publication_pmid },
      { key: 'publication_doi', label: 'Publication DOI', value: metadata.publication_doi },
      { key: 'website', label: 'Website', value: metadata.website },
      { key: 'description', label: 'Description', value: metadata.description },
      { key: 'maintainer_update', label: 'Maintainer update', value: metadata.maintainer_update },
      { key: 'license', label: 'License', value: metadata.license },
    ];

    return entries.filter((entry) => _isPopulated(entry.value));
  }, [selectedDatabase]);

  useEffect(() => {
    // Keep mode selection valid when only one annotation source is available.
    if (phenotypeMode.hasPhenotype && !phenotypeMode.hasClinical) {
      setRequestedPhenotypeMode('phenotype');
      return;
    }
    if (phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('clinical');
      return;
    }
    if (!phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('auto');
    }
  }, [phenotypeMode.hasClinical, phenotypeMode.hasPhenotype]);

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
    <main className="dashboard-shell">
      {/* Left rail only switches visible mode; all data lives in shared hook state. */}
      <aside className="sidebar-rail" aria-label="Dashboard modes">
        <nav className="sidebar-rail-nav">
          {MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              className={`sidebar-rail-link ${activeMode === mode.id ? 'active' : ''} ${mode.id === 'about' ? 'about-tab' : ''}`}
              onClick={() => setActiveMode(mode.id)}
              aria-label={mode.label}
            >
              <span className="sidebar-icon-mask" style={{ '--icon-src': `url(${mode.iconSrc})` }} aria-hidden="true" />
              <span className="sidebar-rail-text">{mode.label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <div className="dashboard-main">
        <div className="top-bar">
          <div className="top-bar-brand-block">
            <div className="brand-logo-wrap" aria-label="ResistanceProfiler dashboard">
              <img className="brand-logo" src={logoSrc} alt="ResistanceProfiler logo" />
            </div>
            <DatabaseSelectorBar
              databases={databases}
              selectedDatabase={selectedDatabase}
              selectedDatabaseId={selectedDatabaseId}
              onDatabaseChange={setSelectedDatabaseId}
              selectId="topbar-db-select"
              className="topbar-db-bar"
            />
          </div>
          <div className="page-links" aria-label="Project links">
            <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer" title="ResistanceProfiler on GitHub" aria-label="ResistanceProfiler on GitHub">
              <img className="page-link-icon" src={githubIconSrc} alt="" aria-hidden="true" />
            </a>
            <a href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer" title="Jonas Fuchs Team website" aria-label="Jonas Fuchs Team website">
              <img className="page-link-icon website-link-icon" src={websiteIconSrc} alt="" aria-hidden="true" />
            </a>
          </div>
        </div>

        <section className="panel-stack">
          {/* ANALYZE TAB: single sample, batch, and regenerate in one shared shell */}
          {activeMode === 'analyze' ? (
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
                          <span className="input-label-row">Results JSON <button type="button" className="input-info-btn" aria-label="Results JSON help" title="Upload a prior results JSON exported by ResistanceProfiler."><img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" /></button></span>
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
                        <div className="profile-upload-row">
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
                                <td>{sample.reportHtmlPath ? <a href={buildArtifactUrl(sample.reportHtmlPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
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
          ) : null}

          {/* SESSION RESULTS TAB: all outputs generated in this session */}
          {activeMode === 'results' ? (
            <article className="card full-width-tile tab-primary-tile">
              <div className="workspace-output-header section-header">
                <div>
                  <h2>Session results</h2>
                  <p>All analysis outputs from this session. Results are cleared on page reload.</p>
                </div>
              </div>
              {sessionResults.length === 0 ? (
                <p className="status">No results yet. Run an analysis to see results here.</p>
              ) : (
                <>
                <div className="table-wrap mutation-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Mode</th>
                        <th>Sample</th>
                        <th>Reference</th>
                        <th>Database</th>
                        <th>Timestamp</th>
                        <th>HTML</th>
                        <th>PDF</th>
                        <th>JSON</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...sessionResults].reverse().map((result, index) => (
                        <tr key={result.run_id || index}>
                          <td>
                            <span className={`job-status-badge mode-badge-${String(result.mode || '').replace(/[^a-z]/g, '')}`}>
                              {result.mode || '—'}
                            </span>
                          </td>
                          <td>{result.sample_name || '—'}</td>
                          <td>{result.reference_name || '—'}</td>
                          <td>{result.database_id || '—'}</td>
                          <td>{result.created_at ? new Date(result.created_at).toLocaleString() : '—'}</td>
                          <td>{result.report_html_path ? <a href={buildReportUrl(result.report_html_path)} target="_blank" rel="noreferrer">View</a> : '—'}</td>
                          <td>{result.report_pdf_path ? <a href={buildArtifactUrl(result.report_pdf_path)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                          <td>{result.report_json_path ? <a href={buildArtifactUrl(result.report_json_path)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="profile-analyze-row">
                  <button
                    type="button"
                    className="analyze-primary"
                    onClick={() => downloadAllSessionArtifacts()}
                    disabled={isSessionDownloadBusy}
                  >
                    {isSessionDownloadBusy ? (
                      <><Spinner /> Preparing...</>
                    ) : (
                      'Download all'
                    )}
                  </button>
                </div>
                </>
              )}
            </article>
          ) : null}

          {/* MUTATION TAB: interactive table with filter/sort and TSV export */}
          {activeMode === 'mutations' ? (
            <>
              <article className="card full-width-tile tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Browse mutations</h2>
                  </div>
                </div>
                <section className="mutation-merged-section">
                  <div className="workspace-output-header section-header">
                    <div>
                      <h3>Single mutations</h3>
                      <p>{displayedRules.length} visible row(s)</p>
                    </div>
                  </div>
                  <div className="mutation-toolbar">
                    <label className="mutation-search" htmlFor="mutation-rules-search">
                      <img src={searchIconSrc} alt="" aria-hidden="true" />
                      <input
                        id="mutation-rules-search"
                        className="mutation-search-input"
                        type="search"
                        placeholder="search rules"
                        value={mutationFilter}
                        onChange={(event) => setMutationFilter(event.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="mutation-reset-button"
                      aria-label="Reset filter"
                      title="Reset filter"
                      onClick={() => {
                        setMutationFilter('');
                        setMutationFilterColumn('-1');
                      }}
                    >
                      <img src={resetFilterIconSrc} alt="" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="mutation-download-button"
                      onClick={downloadMutationsAsTsv}
                    >
                      Download as TSV
                    </button>
                  </div>
                  <div className="table-wrap mutation-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {mutationColumns.map((column, index) => (
                            <th
                              key={column.key}
                              className="sortable-col"
                              onClick={() => {
                                if (mutationSortColumn === index) {
                                  setMutationSortAsc(!mutationSortAsc);
                                } else {
                                  setMutationSortColumn(index);
                                  setMutationSortAsc(true);
                                }
                              }}
                            >
                              {column.label}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {displayedRules.map((rule, index) => (
                          <tr key={`${rule.id || 'rule'}-${index}`}>
                            {mutationColumns.map((column) => (
                              <td key={`${column.key}-${index}`}>{column.accessor(rule)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {mutationsLoaded && rules.length === 0 ? (
                    <p className="status">No single-mutation rules were found for the selected database/filter.</p>
                  ) : null}
                  {mutationsLoaded && rules.length > 0 && displayedRules.length === 0 ? (
                    <p className="status">No mutations match the current filter.</p>
                  ) : null}
                </section>

                <section className="mutation-merged-section">
                  <div className="workspace-output-header section-header">
                    <div>
                      <h3>Combinatorial mutations</h3>
                      <p>{displayedFormulaRules.length} visible row(s)</p>
                    </div>
                  </div>
                  <div className="mutation-toolbar">
                    <label className="mutation-search" htmlFor="formula-rules-search">
                      <img src={searchIconSrc} alt="" aria-hidden="true" />
                      <input
                        id="formula-rules-search"
                        className="mutation-search-input"
                        type="search"
                        placeholder="search rules"
                        value={formulaFilter}
                        onChange={(event) => setFormulaFilter(event.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="mutation-reset-button"
                      aria-label="Reset filter"
                      title="Reset filter"
                      onClick={() => {
                        setFormulaFilter('');
                        setFormulaFilterColumn('-1');
                      }}
                    >
                      <img src={resetFilterIconSrc} alt="" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="mutation-download-button"
                      onClick={downloadFormulaRulesAsTsv}
                    >
                      Download as TSV
                    </button>
                  </div>
                  <div className="table-wrap mutation-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {formulaColumns.map((column) => (
                            <th key={column.key}>{column.label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {displayedFormulaRules.map((rule, index) => (
                          <tr key={`${rule.formula_id || 'formula'}-${index}`}>
                            {formulaColumns.map((column) => (
                              <td key={`${column.key}-${index}`}>{column.accessor(rule)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {mutationsLoaded && formulaRules.length === 0 ? (
                    <p className="status">No formula combinations were found for the selected database/filter.</p>
                  ) : null}
                  {mutationsLoaded && formulaRules.length > 0 && displayedFormulaRules.length === 0 ? (
                    <p className="status">No formula combinations match the current filter.</p>
                  ) : null}
                </section>
              </article>
            </>
          ) : null}

          {/* DATABASE TAB: summary pies + per-reference/per-gene mutation position charts */}
          {activeMode === 'database' ? (
            <>
              <article className="card full-width-tile database-plots-tile tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Database Dashboard</h2>
                    <p>Overview and visual summaries of the active resistance database.</p>
                  </div>
                </div>

                {selectedDatabase ? (
                  <>
                  {databaseInfoEntries.length > 0 ? (
                    <section className="database-meta-panel" aria-label="Database information">
                      {databaseInfoEntries.map((entry) => (
                        <div key={entry.key} className="database-meta-row">
                          <span className="database-meta-label">{entry.label}</span>
                          <span className="database-meta-value">
                            {_renderDatabaseMetaValue(entry)}
                          </span>
                        </div>
                      ))}
                    </section>
                  ) : null}
                  {summaryTile || ic50Sections.length > 0 || detailSections.length > 0 ? (
                    <div className="database-plot-grid">
                      {summaryTile ? <DatabasePieSummaryRow tile={summaryTile} /> : null}
                      {/* Chart controls are global for all gene tiles in this section. */}
                      {ic50Sections.map((section) => (
                        <section
                          key={section.sectionKey}
                          className={[
                            'database-reference-section',
                            section.layout === 'single-column' ? 'database-reference-section-wide' : '',
                            section.layout === 'score-grid' ? 'database-reference-section-score' : '',
                          ].filter(Boolean).join(' ')}
                        >
                          <div className="database-phenotype-switch-row">
                            <h3 className="database-section-heading">{section.sectionHeading}</h3>
                          </div>
                          <div className="database-reference-plot-grid">
                            {section.plots.map((plot) => (
                              <DatabaseDrugDistributionPlot key={plot.key} plot={plot} />
                            ))}
                          </div>
                        </section>
                      ))}
                      {detailSections.length > 0 ? (
                        <>
                          <div className="database-phenotype-switch-row">
                            <h3 className="database-section-heading">Mutations in each gene</h3>
                            <div className="database-phenotype-switch-controls">
                              {phenotypeMode.hasPhenotype && phenotypeMode.hasClinical ? (
                                <div className="database-phenotype-switch" role="group" aria-label="Position annotation mode">
                                  <button
                                    type="button"
                                    className={activePhenotypeMode === 'phenotype' ? 'active' : ''}
                                    onClick={() => setRequestedPhenotypeMode('phenotype')}
                                  >
                                    Phenotype
                                  </button>
                                  <button
                                    type="button"
                                    className={activePhenotypeMode === 'clinical' ? 'active' : ''}
                                    onClick={() => setRequestedPhenotypeMode('clinical')}
                                  >
                                    Clinical phenotype
                                  </button>
                                </div>
                              ) : null}
                              <label className="database-bin-size-control" aria-label="Amino-acid bin size">
                                <span>Bin size</span>
                                <input
                                  type="number"
                                  min="1"
                                  max="100"
                                  step="1"
                                  value={binSize}
                                  onChange={(event) => {
                                    const nextValue = Number(event.target.value);
                                    if (Number.isFinite(nextValue)) {
                                      setRequestedBinSize(nextValue);
                                    }
                                  }}
                                />
                              </label>
                            </div>
                          </div>
                          {detailSections.map((section) => (
                            <section key={section.referenceKey} className="database-reference-section">
                              <div className="database-reference-heading">
                                <h3>{section.referenceHeading}</h3>
                              </div>
                              <div className="database-reference-plot-grid">
                                {section.plots.map((plot) => (
                                  <DatabasePositionPlot key={plot.key} plot={plot} />
                                ))}
                              </div>
                            </section>
                          ))}
                        </>
                      ) : null}
                    </div>
                  ) : (
                    <p className="status">No plot-friendly data is available for the active database.</p>
                  )}
                  </>
                ) : (
                  <p className="status">No active database loaded.</p>
                )}
              </article>
            </>
          ) : null}

          {/* ABOUT TAB: static links and runtime backend endpoint info */}
          {activeMode === 'about' ? (
            <article className="card about-tile">
              <div className="about-header">
                <h2>About ResistanceProfiler</h2>
                <p>
                  ResistanceProfiler is a pathogen-agnostic antiviral resistance framework with a CLI-first core and a web
                  frontend for interactive analysis.
                </p>
              </div>

              <div className="about-notice">
                <strong>Research use only.</strong> This software supports exploratory interpretation and does not replace
                accredited clinical diagnostics.
              </div>
              <div className="about-notice">
                <strong>No database curation.</strong> We do not maintain or curate resistance databases ourselves. We only
                provide up-to-date converted versions of openly available databases and are not responsible for their content 
                or maintenance.
              </div>

              <div className="about-grid">
                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutScopeIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Project Scope and How It Works</h3>
                  </div>
                  <p>
                    References and rules are matched during database creation to ensure internal consistency.
                    Mutations are stored in a project database, and new sequences are compared against internal
                    references to identify resistance patterns. Importantly, the reference is determined automatically
                    from mappy-based CDS matching, and the sequence with the highest identity is selected. The default
                    identity threshold is 90%, which supports robust matching for closely related inputs. Lowering the
                    identity threshold can increase the risk of mismatched references and less reliable resistance calls
                    when sequences are highly divergent from database references.
                  </p>
                  <ul>
                    <li>Input can be consensus FASTA or VCF plus matching reference FASTA and an optional BAM file for coverage analysis.</li>
                    <li>The core pipeline resolves reference context, maps changes codon-aware, and matches rules.</li>
                    <li>
                      Coverage analysis provides insights into sequencing depth and data quality. For FASTA
                      sequences, missing information is treated as coverage gaps.
                    </li>
                    <li>Outputs include structured results and HTML reports for review.</li>
                  </ul>
                </section>

                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutNomenclatureIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Resistance Nomenclature Basics</h3>
                  </div>
                  <p>
                    Rules are amino-acid-centric. A notation such as <strong>A123V</strong> means reference amino acid A
                    at position 123 changes to V.
                  </p>
                  <ul>
                    <li><strong>Substitution:</strong> <strong>A123V</strong> means position 123 changed from A to V.</li>
                    <li><strong>Anchored deletion:</strong> <strong>VG215V</strong> means that the G after position 215 is deleted.</li>
                    <li><strong>Anchored insertion:</strong> <strong>V215VG</strong> means insertion of G after the V at position 215.</li>
                    <li><strong>Frameshift:</strong> <strong>L201Lfsx</strong> indicates a reading-frame shift after the L at position 201.</li>
                    <li><strong>Complex:</strong> <strong>L201complex</strong> indicates a triplet indel within the L codon at position 201.</li>
                    <li><strong>Phenotype</strong> captures in-vitro susceptibility interpretation.</li>
                    <li><strong>Clinical phenotype</strong> captures treatment-oriented interpretation where available.</li>
                    <li>Combination context can matter; interpreted annotations are shown in reports and dashboard plots.</li>
                  </ul>
                </section>

                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutNomenclatureIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Resistance Combinations</h3>
                  </div>
                  <p>
                    Combination rules allow interpretation based on boolean logic across multiple mutation members.
                    They are defined separately from single rules and evaluated with operators such as
                    <strong> and</strong>, <strong>or</strong>, <strong>not</strong>, and <strong>xor</strong>.
                  </p>
                  <ul>
                    <li><strong>Single rules</strong> represent one mutation-to-interpretation mapping.</li>
                    <li><strong>Combination rules</strong> fire only when their formula conditions are satisfied.</li>
                  </ul>
                </section>

                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutCliIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>CLI and Extended Functionality</h3>
                  </div>
                  <p>
                    The CLI is the primary interface and includes project creation, rule curation, profiling, and export. The same
                    functionality as the web app can be achieved through the CLI, enabling direct integration into existing
                    workflows and pipelines.
                  </p>
                  <div className="about-command-block">
                    <code>respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db</code>
                    <code>respro add --project project.db --rules more_rules.tsv</code>
                    <code>respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/ --export json</code>
                    <code>respro fasta --project project.db --fasta sample.fasta --output report/</code>
                  </div>
                  <p>
                    Profiling runs can also emit a JSON dump of the result payload, which can later be used to regenerate report artifacts.
                  </p>
                  <p className="about-mini-heading">Start your own ResPro Explorer by cloning the repository</p>
                  <div className="about-command-block">
                    <code>docker compose -f docker-compose.web.yml up --build</code>
                  </div>
                  <p>Open <strong>{FRONTEND_CONFIG.ui.explorerUrl}</strong> after startup.</p>
                </section>

                <section className="about-section-card about-section-card-contact">
                  <div className="about-section-title">
                    <img src={aboutCommunityIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Contributing and Contact</h3>
                  </div>
                  <p>
                    Contributions are very welcome, especially curated rule datasets, bug reports, reproducible test
                    cases, and code improvements. Open an issue or submit a pull request on{' '}
                    <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer">
                      GitHub
                    </a>{' '}
                    to get in touch. For direct contact, please{' '}
                    <a href="mailto:jonas.fuchs@uniklinik-freiburg.de">email Jonas Fuchs</a>.
                  </p>
                  <p className="about-mini-heading">Data usage</p>
                  <ul>
                    <li>Session-scoped uploads/reports are cleaned up automatically when a browser tab closes.</li>
                    <li>
                      No data is stored on remote servers. Nevertheless, avoid naming your results with sensitive
                      information such as patient identifiers or names.
                    </li>
                  </ul>

                  <p className="about-mini-heading">Licensing</p>
                  <ul>
                    <li>ResistanceProfiler source code is released under the MIT License.</li>
                    <li>External references, rules, and publication-linked datasets may have separate licenses or citation requirements.</li>
                    <li>The databases listed here are openly accessible and have been converted to be compatible with ResPro.</li>
                    <li>Users are responsible for compliant use of third-party data in their own environments.</li>
                  </ul>

                  <div className="about-sponsor">
                    <p className="about-mini-heading">Supported by</p>
                    <a
                      href="https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/"
                      target="_blank"
                      rel="noreferrer"
                      aria-label="Sponsor page"
                    >
                      <img
                        src="https://uni-freiburg.de/med/wp-content/uploads/sites/9/fodek-hans-a-krebs-program-for-medical-scientist.png"
                        alt="Sponsor logo"
                        className="about-sponsor-logo"
                      />
                    </a>
                  </div>
                </section>
              </div>
            </article>
          ) : null}
        </section>
      </div>
    </main>
  );
}
