import { useEffect, useMemo, useRef, useState } from 'react';

import logoSrc from '../assets/logo.svg';
import aboutIconSrc from '../assets/icon-about.svg';
import analyzeIconSrc from '../assets/icon-analyze.svg';
import aboutScopeIconSrc from '../assets/icon-scope.svg';
import aboutAlignIconSrc from '../assets/icon-align.svg';
import uploadIconSrc from '../assets/upload.svg';
import okListIconSrc from '../assets/ok_list.svg';
import shieldIconSrc from '../assets/shield.svg';
import noSignIconSrc from '../assets/no_sign.svg';
import networkIconSrc from '../assets/network.svg';
import contactIconSrc from '../assets/contact.svg';
import licenseIconSrc from '../assets/license.svg';
import cliIconSrc from '../assets/icon-cli.svg';
import aboutIllustrationSrc from '../assets/about.png';
import databaseIconSrc from '../assets/icon-database.svg';
import githubIconSrc from '../assets/icon-github.svg';
import websiteIconSrc from '../assets/website.svg';
import mutationsIconSrc from '../assets/search.svg';
import batchIconSrc from '../assets/batch.svg';
import logicIconSrc from '../assets/logic.svg';
import { FRONTEND_CONFIG } from '../config';
import { buildDatabasePlots } from './database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from './database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from './database-plots/DatabasePositionPlot';
import { DatabaseDrugDistributionPlot } from './database-plots/DatabaseDrugDistributionPlot';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { Spinner } from './Spinner';
import { ComparisonHeatmap } from './ComparisonHeatmap';
import regenerateIconSrc from '../assets/icon-regenerate.svg';
import homeIconSrc from '../assets/home.svg';
import reportIconSrc from '../assets/reports.svg';
import infoIconSrc from '../assets/info.svg';
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

const ABOUT_CLI_COMMANDS = [
  'respro databases --download db_name --output my_folder/',
  'respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db',
  'respro add --project project.db --rules more_rules.tsv --formula-rules combinatorial_rules.tsv',
  'respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/ --export json',
  'respro fasta --project project.db --fasta sample.fasta --output report/',
  'respro regenerate --project project.db --json report/sample.results.json --output report/',
];

const ABOUT_DOCKER_COMMAND = 'docker compose -f docker-compose.web.yml up --build';

const ABOUT_WORKFLOW_STEPS = [
  {
    title: 'Input',
    text: 'Consensus FASTA or VCF plus matching reference FASTA; optional BAM for coverage.',
    iconSrc: uploadIconSrc,
  },
  {
    title: 'Reference matching',
    text: 'Automatic sequence feature matching using minimap.',
    iconSrc: aboutAlignIconSrc,
  },
  {
    title: 'Mutation detection',
    text: 'Nucleotide and amino-acid changes are identified.',
    iconSrc: mutationsIconSrc,
  },
  {
    title: 'Rule evaluation',
    text: 'Single and combination rules are matched.',
    iconSrc: okListIconSrc,
  },
  {
    title: 'Report generation',
    text: 'Structured results, JSON exports, plots, and HTML reports.',
    iconSrc: reportIconSrc,
  },
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

function _formatAlgorithmThresholds(thresholds) {
  if (!thresholds || typeof thresholds !== 'object') {
    return 'Not configured';
  }

  const keys = Object.keys(thresholds).sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) {
    return 'Not configured';
  }

  const values = keys.map((key) => {
    const value = thresholds[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const nestedKeys = Object.keys(value).sort((a, b) => a.localeCompare(b));
      const nestedText = nestedKeys
        .map((nestedKey) => `${nestedKey}=${value[nestedKey]}`)
        .join(', ');
      return `${key}: ${nestedText}`;
    }
    return `${key}=${value}`;
  });
  return values.join('; ');
}

function _groupEffectRules(rules) {
  if (!Array.isArray(rules) || rules.length === 0) {
    return [];
  }

  const grouped = new Map();
  for (const rule of rules) {
    const feature = String(rule.feature || '').trim();
    const reference = String(rule.reference || '').trim();
    const drug = String(rule.drug || '').trim();
    const effects = Array.isArray(rule.effect) ? rule.effect.map((e) => String(e).trim()).filter(Boolean) : [];
    const key = `${feature}|||${reference}`;
    if (!grouped.has(key)) {
      grouped.set(key, { feature, reference, effects: new Set(), drugs: new Set() });
    }
    for (const eff of effects) {
      grouped.get(key).effects.add(eff);
    }
    if (drug) {
      grouped.get(key).drugs.add(drug);
    }
  }

  return [...grouped.values()]
    .map((row) => ({
      feature: row.feature,
      reference: row.reference,
      effects: [...row.effects].sort((a, b) => a.localeCompare(b)),
      drugs: [...row.drugs].sort((a, b) => a.localeCompare(b)),
    }))
    .sort((a, b) => {
      const featureOrder = a.feature.localeCompare(b.feature);
      if (featureOrder !== 0) {
        return featureOrder;
      }
      return a.reference.localeCompare(b.reference);
    });
}

function _renderDatabaseAlgorithms(algorithms) {
  if (!algorithms) return null;
  const effectAsResistant = algorithms.effect_as_resistant;
  const drugInterp = algorithms.drug_interpretation;
  const groupedEffectRules = _groupEffectRules(effectAsResistant?.rules);
  if (!effectAsResistant && !drugInterp) return null;

  return (
    <section className="database-meta-panel database-algorithms-panel" aria-label="Configured algorithms">
      <div className="database-meta-row database-meta-row-heading">
        <span className="database-meta-label database-meta-section-heading">
          <span className="database-meta-section-heading-text">Supplied Interpretation Algorithms</span>
          <button
            type="button"
            className="input-info-btn"
            aria-label="Supplied interpretation algorithms info"
            title="These settings are supplied by the active database. See the About section for algorithm details and interpretation behavior."
          >
            <img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" />
          </button>
        </span>
      </div>
      {drugInterp && drugInterp.length > 0 ? (
        <div className="database-meta-row database-meta-row-table">
          <span className="database-meta-label">drug_interpretation</span>
          <span className="database-meta-value">
            <table className="database-algorithm-table">
              <thead>
                <tr>
                  <th>Method</th>
                  <th>Thresholds</th>
                </tr>
              </thead>
              <tbody>
                {drugInterp.map((entry, idx) => (
                  <tr key={entry.method || idx}>
                    <td>{String(entry.method || '').trim() || 'Not configured'}</td>
                    <td>{_formatAlgorithmThresholds(entry.thresholds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </span>
        </div>
      ) : null}
      {groupedEffectRules.length > 0 ? (
        <div className="database-meta-row database-meta-row-table">
          <span className="database-meta-label">effect_as_resistant</span>
          <span className="database-meta-value">
            <table className="database-algorithm-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Effects</th>
                  <th>Reference</th>
                  <th>Drugs</th>
                </tr>
              </thead>
              <tbody>
                {groupedEffectRules.map((rule) => (
                  <tr key={`${rule.feature}-${rule.reference}`}>
                    <td>{rule.feature}</td>
                    <td>{rule.effects.join(', ')}</td>
                    <td>{rule.reference}</td>
                    <td>{rule.drugs.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </span>
        </div>
      ) : null}
    </section>
  );
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
  // Comparison
  selectedResultIndices,
  setSelectedResultIndices,
  comparisonDbId,
  comparisonRefName,
  selectAllComparable,
  comparisonData,
  isComparisonBusy,
  nonSynonymousOnly,
  setNonSynonymousOnly,
  dbHitsOnly,
  setDbHitsOnly,
  fetchComparisonData,
  downloadSelectedArtifacts,
  clearComparison,
  resetBatch,
}) {
  // These controls only affect database charts, not mutation browsing or profiling.
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);
  const [reportFrameHeight, setReportFrameHeight] = useState(900);
  const analyzeSubmodeRowRef = useRef(null);
  const [analyzeSubmodeRowWidth, setAnalyzeSubmodeRowWidth] = useState(0);
  const [copiedCommandKey, setCopiedCommandKey] = useState('');

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

  const copyAboutCommand = async (content, key) => {
    if (!navigator?.clipboard?.writeText) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setCopiedCommandKey(key);
      setTimeout(() => {
        setCopiedCommandKey((current) => (current === key ? '' : current));
      }, 1600);
    } catch {
      setCopiedCommandKey('');
    }
  };

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
                        <th className="checkbox-col"></th>
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
                      {[...sessionResults].reverse().map((result, revIndex) => {
                        const originalIndex = sessionResults.length - 1 - revIndex;
                        const isSelected = selectedResultIndices.has(originalIndex);
                        const resultDbId = result.database_id || '';
                        const resultRefName = result.reference_name || '';
                        const isDisabled = comparisonDbId !== null && (resultDbId !== comparisonDbId || resultRefName !== comparisonRefName);
                        return (
                          <tr key={result.run_id || revIndex}>
                            <td className="checkbox-col">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                disabled={isDisabled}
                                onChange={() => {
                                  setSelectedResultIndices((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(originalIndex)) {
                                      next.delete(originalIndex);
                                    } else {
                                      next.add(originalIndex);
                                    }
                                    return next;
                                  });
                                }}
                              />
                            </td>
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
                        );
                      })}
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
                  <button
                    type="button"
                    className="analyze-primary"
                    onClick={() => downloadSelectedArtifacts()}
                    disabled={selectedResultIndices.size === 0 || isSessionDownloadBusy}
                  >
                    Download selected
                  </button>
                </div>
                <div className="profile-analyze-row">
                  <button
                    type="button"
                    className="analyze-primary"
                    onClick={() => selectAllComparable()}
                    disabled={selectedResultIndices.size === 0}
                  >
                    Select all comparable
                  </button>
                  <button
                    type="button"
                    className="analyze-primary"
                    onClick={() => fetchComparisonData()}
                    disabled={selectedResultIndices.size < 2 || isComparisonBusy}
                  >
                    {isComparisonBusy ? <><Spinner /> Comparing...</> : 'Compare selected'}
                  </button>
                  <button
                    type="button"
                    className="comparison-clear-btn"
                    onClick={() => clearComparison()}
                    disabled={!comparisonData}
                  >
                    Clear comparison
                  </button>
                  <label className="comparison-switch comparison-switch-right" title="Show only non-synonymous mutations">
                    <input
                      type="checkbox"
                      checked={nonSynonymousOnly}
                      onChange={(e) => {
                        setNonSynonymousOnly(e.target.checked);
                        if (selectedResultIndices.size >= 2) {
                          fetchComparisonData(e.target.checked);
                        }
                      }}
                    />
                    <span>Non-synonymous only</span>
                  </label>
                  <label className="comparison-switch" title="Show only database hits">
                    <input
                      type="checkbox"
                      checked={dbHitsOnly}
                      onChange={(e) => {
                        setDbHitsOnly(e.target.checked);
                        if (selectedResultIndices.size >= 2) {
                          fetchComparisonData(undefined, e.target.checked);
                        }
                      }}
                    />
                    <span>DB hits only</span>
                  </label>
                </div>
                {comparisonData && (
                  <section className="comparison-section">
                    <div className="workspace-output-header section-header">
                      <div>
                        <h3>Comparison heatmap</h3>
                        <p>
                          {comparisonData.samples.length} samples × {comparisonData.mutations.length} mutations
                          {comparisonData.references.length > 0 && ` — Reference: ${comparisonData.references[0]}`}
                        </p>
                      </div>
                    </div>
                    <ComparisonHeatmap data={comparisonData} isBusy={isComparisonBusy} />
                  </section>
                )}
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
                      <img src={mutationsIconSrc} alt="" aria-hidden="true" />
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
                      <img src={mutationsIconSrc} alt="" aria-hidden="true" />
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
                  {_renderDatabaseAlgorithms(selectedDatabase?.algorithms)}
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
              <section className="about-hero" tabIndex={0}>
                <div className="about-hero-content">
                  <p className="about-hero-kicker">Pathogen-agnostic antiviral resistance profiling</p>
                  <h2>About ResistanceProfiler</h2>
                  <p>
                    ResistanceProfiler is a pathogen-agnostic antiviral resistance framework with a CLI-first core and a
                    web frontend for interactive analysis.
                  </p>
                  <div className="about-hero-actions">
                    <button type="button" onClick={() => setActiveMode('analyze')}>Start analysis</button>
                    <a
                      className="about-hero-link"
                      href="https://github.com/jonas-fuchs/ResistanceProfiler/tree/master/docs/user"
                      target="_blank"
                      rel="noreferrer"
                    >
                      View documentation
                    </a>
                    <a className="about-hero-link about-hero-link-secondary" href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer">
                      GitHub
                    </a>
                  </div>
                </div>
                <div className="about-hero-visual" aria-hidden="true">
                  <img src={aboutIllustrationSrc} alt="" className="about-hero-image" />
                </div>
              </section>

              <section className="about-notice-grid" aria-label="Important notices">
                <article className="about-notice-card about-notice-card-research" tabIndex={0}>
                  <span className="about-notice-icon" aria-hidden="true">
                    <span className="about-icon-mask" style={{ '--icon-src': `url(${shieldIconSrc})` }} />
                  </span>
                  <div>
                    <h3>Research use only</h3>
                    <p>
                      This software supports exploratory interpretation and does not replace accredited clinical diagnostics.
                    </p>
                  </div>
                </article>
                <article className="about-notice-card about-notice-card-database" tabIndex={0}>
                  <span className="about-notice-icon" aria-hidden="true">
                    <span className="about-icon-mask" style={{ '--icon-src': `url(${noSignIconSrc})` }} />
                  </span>
                  <div>
                    <h3>No database curation</h3>
                    <p>
                      We do not maintain or curate resistance databases ourselves. We only provide up-to-date converted
                      <a href="https://github.com/jonas-fuchs/respro-db" target="_blank" rel="noreferrer"> versions</a> of openly available databases and are not responsible for their content or maintenance.
                    </p>
                  </div>
                </article>
              </section>

              <section className="about-section-card about-section-card-scope" tabIndex={0}>
                <div className="about-section-title">
                  <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${aboutScopeIconSrc})` }} aria-hidden="true" />
                  <h3>Project Scope and How It Works</h3>
                </div>
                <p className="about-section-lead">
                  References and rules are matched during database creation to ensure internal consistency. Mutations are
                  stored in a project database, and new sequences are compared against internal references to identify
                  resistance patterns. The reference is determined automatically from <a href="https://github.com/lh3/minimap2" target="_blank" rel="noreferrer">mappy-based</a> CDS matching, and the sequence with the highest identity is selected.
                </p>
                <div className="about-workflow" role="list" aria-label="ResistanceProfiler workflow">
                  <div className="about-workflow-track" aria-hidden="true">
                    {ABOUT_WORKFLOW_STEPS.map((step, index) => (
                      <span key={step.title} className="about-workflow-point">
                        <span className="about-workflow-number">{index + 1}</span>
                      </span>
                    ))}
                  </div>
                  <div className="about-workflow-cards">
                    {ABOUT_WORKFLOW_STEPS.map((step) => (
                      <article key={step.title} className="about-workflow-step" role="listitem" tabIndex={0}>
                        <span className="about-workflow-icon about-icon-mask" style={{ '--icon-src': `url(${step.iconSrc})` }} aria-hidden="true" />
                        <div className="about-workflow-copy">
                          <h4>{step.title}</h4>
                          <p>{step.text}</p>
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              </section>

              <section className="about-knowledge-grid" aria-label="Resistance interpretation basics">
                <article className="about-section-card" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${okListIconSrc})` }} aria-hidden="true" />
                    <h3>Rule Nomenclature Basics</h3>
                  </div>
                  <p className="about-section-lead">
                    Rules are amino-acid-centric. A notation such as <span className="about-inline-pill">A123V</span> means
                    reference amino acid A at position 123 changes to V.
                  </p>
                  <div className="about-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Type</th>
                          <th>Example</th>
                          <th>Meaning</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Substitution</td>
                          <td><span className="about-inline-pill">A123V</span></td>
                          <td>Position 123 changed from A to V.</td>
                        </tr>
                        <tr>
                          <td>Anchored deletion</td>
                          <td><span className="about-inline-pill">VG215V</span></td>
                          <td>The G after position 215 is deleted.</td>
                        </tr>
                        <tr>
                          <td>Anchored insertion</td>
                          <td><span className="about-inline-pill">V215VG</span></td>
                          <td>Insertion of G after the V at position 215.</td>
                        </tr>
                        <tr>
                          <td>Frameshift</td>
                          <td><span className="about-inline-pill">L201Lfsx</span></td>
                          <td>Reading-frame shift after the L at position 201.</td>
                        </tr>
                        <tr>
                          <td>Phenotype</td>
                          <td><span className="about-inline-pill">sensitive / resistant</span></td>
                          <td>Captures in-vitro susceptibility interpretation.</td>
                        </tr>
                        <tr>
                          <td>Clinical phenotype</td>
                          <td><span className="about-inline-pill">sensitive / resistant</span></td>
                          <td>Captures treatment-oriented interpretation where available.</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </article>

                <article className="about-section-card" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${networkIconSrc})` }} aria-hidden="true" />
                    <h3>Rule Combinations</h3>
                  </div>
                  <p className="about-section-lead">
                    Combination rules allow interpretation based on boolean logic across multiple mutation members. They
                    are defined separately from single rules and evaluated with operators such as and, or, not, and xor.
                  </p>
                  <p className="about-threshold-note">
                    Combination members are evaluated based on a fixed member allele-frequency
                    (<span className="about-inline-pill">AF &gt; 0.75</span> by default).
                  </p>
                  <div className="about-operator-list">
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-and">AND</span>
                      <p>All specified mutations must be present.</p>
                      <code>A AND B</code>
                    </div>
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-or">OR</span>
                      <p>At least one specified mutation must be present.</p>
                      <code>A OR B</code>
                    </div>
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-not">NOT</span>
                      <p>The specified mutation must not be present.</p>
                      <code>A AND NOT B</code>
                    </div>
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-xor">XOR</span>
                      <p>Exactly one specified mutation must be present.</p>
                      <code>A XOR B</code>
                    </div>
                  </div>
                  <p className="about-note-inline">
                    Single rules represent one mutation-to-interpretation mapping. Combination rules fire only when
                    their formula conditions are satisfied.
                  </p>
                </article>

                <article className="about-section-card about-section-card-algorithms" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${logicIconSrc})` }} aria-hidden="true" />
                    <h3>Supported Interpretation Algorithms</h3>
                  </div>
                  <p className="about-section-lead">
                    Interpretation algorithms extend rule evaluation with additional logic. They are configured per
                    project in the metadata JSON at initialisation time.
                  </p>
                  <div className="about-operator-list">
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-and">effect_as_resistant</span>
                      <p>
                        Configured high-impact variant effects (frameshift, stop_gained, stop_lost, start_lost,
                        insertion, deletion) observed in a feature/reference pair are interpreted as
                          <span className="about-inline-pill">phenotype='resistant'</span> for the configured drug.
                          This algorithm does not set <span className="about-inline-pill">clinical_phenotype</span>.
                      </p>
                    </div>
                    <div className="about-operator-row">
                      <span className="about-operator-pill about-operator-pill-or">drug_interpretation</span>
                      <p>
                        Combines matched rules into one overall drug result. Depending on the database, this can be
                        based on phenotype labels, scores, IC50 values, or fold-change cutoffs.
                      </p>
                    </div>
                  </div>
                </article>
              </section>

              <section className="about-section-card about-cli-card" tabIndex={0}>
                <div className="about-section-title">
                  <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${cliIconSrc})` }} aria-hidden="true" />
                  <h3>CLI and Extended Functionality</h3>
                </div>
                <p className="about-section-lead">
                  The CLI is the primary interface and includes project creation, rule curation, profiling, and export.
                  The same functionality as the web app can be achieved through the CLI, enabling direct integration into
                  existing workflows and pipelines.
                </p>
                <div className="about-cli-grid">
                  <article className="about-terminal" tabIndex={0}>
                    <div className="about-terminal-header">
                      <p>respro-cli</p>
                      <button
                        type="button"
                        className="about-copy-btn"
                        onClick={() => copyAboutCommand(ABOUT_CLI_COMMANDS.join('\n'), 'cli')}
                        aria-label="Copy CLI commands"
                      >
                        {copiedCommandKey === 'cli' ? 'Copied' : 'Copy'}
                      </button>
                    </div>
                    <pre>
                      {ABOUT_CLI_COMMANDS.map((line) => (
                        <code key={line}><span className="about-terminal-prompt">$</span> {line}</code>
                      ))}
                    </pre>
                  </article>
                  <article className="about-cli-side" tabIndex={0}>
                    <div className="about-cli-side-card">
                      <h4>Regenerate reports from JSON</h4>
                      <p>
                        Profiling runs can emit a JSON dump of the result payload, which can later be used to regenerate
                        report artifacts.
                      </p>
                    </div>
                    <div className="about-cli-side-card">
                      <h4>Run ResPro WebApp locally</h4>
                      <div className="about-mini-command">
                        <code>{ABOUT_DOCKER_COMMAND}</code>
                        <button
                          type="button"
                          className="about-copy-btn"
                          onClick={() => copyAboutCommand(ABOUT_DOCKER_COMMAND, 'docker')}
                          aria-label="Copy Docker startup command"
                        >
                          {copiedCommandKey === 'docker' ? 'Copied' : 'Copy'}
                        </button>
                      </div>
                      <p>Open <strong>{FRONTEND_CONFIG.ui.explorerUrl}</strong> after startup.</p>
                    </div>
                  </article>
                </div>
              </section>

              <section className="about-bottom-grid" aria-label="Project information and governance">
                <article className="about-section-card" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${contactIconSrc})` }} aria-hidden="true" />
                    <h3>Contributing and Contact</h3>
                  </div>
                  <p>
                    Contributions are very welcome, especially curated rule datasets, bug reports, reproducible test
                    cases, and code improvements. Open an issue or submit a pull request on{' '}
                    <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer">GitHub</a>{' '}
                    to get in touch. For direct contact, please{' '}
                    <a href="mailto:jonas.fuchs@uniklinik-freiburg.de">email Jonas Fuchs</a>.
                  </p>
                </article>
                <article className="about-section-card" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${databaseIconSrc})` }} aria-hidden="true" />
                    <h3>Data usage</h3>
                  </div>
                  <ul>
                    <li>Session-scoped uploads and reports are cleaned up automatically when a browser tab closes.</li>
                    <li>No data is stored on remote servers.</li>
                    <li>Avoid naming results with sensitive information such as patient identifiers or names.</li>
                  </ul>
                </article>
                <article className="about-section-card" tabIndex={0}>
                  <div className="about-section-title">
                    <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${licenseIconSrc})` }} aria-hidden="true" />
                    <h3>Licensing</h3>
                  </div>
                  <ul>
                    <li>ResistanceProfiler source code is released under the MIT License.</li>
                    <li>External references, rules, and publication-linked datasets may have separate licenses or citation requirements.</li>
                    <li>Users are responsible for compliant use of third-party data in their own environments.</li>
                  </ul>
                </article>
              </section>

              <section className="about-supported-strip" tabIndex={0}>
                <p>Supported by</p>
                <div className="about-supported-logos">
                  <a
                    href="https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/"
                    target="_blank"
                    rel="noreferrer"
                    aria-label="Sponsor page"
                    className="about-supported-logo"
                  >
                    <img
                      src="https://uni-freiburg.de/med/wp-content/uploads/sites/9/fodek-hans-a-krebs-program-for-medical-scientist.png"
                      alt="Sponsor logo"
                      className="about-sponsor-logo"
                    />
                  </a>
                </div>
              </section>
            </article>
          ) : null}
        </section>
      </div>
    </main>
  );
}
