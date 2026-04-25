import { useEffect, useMemo, useState } from 'react';

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
import mutationsIconSrc from '../assets/icon-mutations.svg';
import { FRONTEND_CONFIG } from '../config';
import { buildDatabasePlots } from './database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from './database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from './database-plots/DatabasePositionPlot';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { Spinner } from './Spinner';
import regenerateIconSrc from '../assets/icon-regenerate.svg';

const MODES = [
  { id: 'profile', label: 'Analyze', iconSrc: analyzeIconSrc },
  { id: 'regenerate', label: 'Regenerate', iconSrc: regenerateIconSrc },
  { id: 'database', label: 'Database', iconSrc: databaseIconSrc },
  { id: 'mutations', label: 'Browse mutations', iconSrc: mutationsIconSrc },
  { id: 'about', label: 'About', iconSrc: aboutIconSrc },
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
  status,
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
  inlineReportPath,
  inlineReportLabel,
  mutationColumns,
  formulaColumns,
  mutationPlotMeta,
  displayedRules,
  displayedFormulaRules,
  reportOptions,
  isProfileBusy,
  runSelectedProfile,
  openSelectedReportInline,
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
  uploadProgress,
}) {
  // These controls only affect database charts, not mutation browsing or profiling.
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);

  const { summaryTile, detailSections, phenotypeMode, binSize } = useMemo(
    () => buildDatabasePlots(rules, formulaRules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize),
    [rules, formulaRules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize]
  );
  const activePhenotypeMode = phenotypeMode.activeMode;
  const selectedReportOption = useMemo(
    () => reportOptions.find((option) => option.path === selectedProfileReportPath) || null,
    [reportOptions, selectedProfileReportPath]
  );

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
      { key: 'tsv_checksum', label: 'TSV checksum', value: metadata.tsv_checksum },
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
              title={mode.label}
            >
              <span className="sidebar-icon-mask" style={{ '--icon-src': `url(${mode.iconSrc})` }} aria-hidden="true" />
            </button>
          ))}
        </nav>
      </aside>

      <div className="dashboard-main">
        <div className="top-bar">
          <div className="brand-logo-wrap" aria-label="ResistanceProfiler dashboard">
            <img className="brand-logo" src={logoSrc} alt="ResistanceProfiler logo" />
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
          {/* PROFILE TAB: upload input files and render generated report */}
          {activeMode === 'profile' ? (
            <>
              <article className="card profile-input-card tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Profile resistances</h2>
                    <p>Compare mutations to vcf or fasta files</p>
                  </div>
                  <DatabaseSelectorBar
                    databases={databases}
                    selectedDatabase={selectedDatabase}
                    selectedDatabaseId={selectedDatabaseId}
                    onDatabaseChange={setSelectedDatabaseId}
                    selectId="header-db-select"
                    className="profile-db-bar"
                  />
                </div>
                <div className="profile-input-subtile section-subtile">
                  {activeProfileMode === 'vcf' ? (
                    <>
                      <div className="profile-mode-row">
                        <label className="inline-mode-label" aria-label="Profiling mode">
                          <span>Mode</span>
                          <select
                            value={activeProfileMode}
                            disabled={isProfileBusy}
                            onChange={(event) => setActiveProfileMode(event.target.value)}
                          >
                            {PROFILE_MODES.map((mode) => (
                              <option key={mode.id} value={mode.id}>{mode.label}</option>
                            ))}
                          </select>
                        </label>
                        <div className="upload-progress" aria-label="Upload progress">
                          <div className="upload-progress-head">
                            <span>Upload progress</span>
                            <span>{uploadProgress.percent}%</span>
                          </div>
                          <div className="upload-progress-track" aria-hidden="true">
                            <div className="upload-progress-fill" style={{ width: `${uploadProgress.percent}%` }} />
                          </div>
                          <p className="upload-progress-file" title={uploadProgress.fileName || 'No upload yet'}>
                            {uploadProgress.fileName || 'No upload yet'}
                          </p>
                        </div>
                      </div>

                      <div className="profile-upload-row profile-upload-row-vcf">
                        <label>
                          VCF file
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
                          Reference FASTA
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
                          <span className="label-text">BAM file <span className="field-optional">(optional)</span></span>
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
                          <span className="label-text">Frequency cutoff <span className="field-optional">(min AF)</span></span>
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
                          <span className="label-text">Coverage cutoff <span className="field-optional">(min depth)</span></span>
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
                    </>
                  ) : (
                    <>
                      <div className="profile-mode-row">
                        <label className="inline-mode-label" aria-label="Profiling mode">
                          <span>Mode</span>
                          <select
                            value={activeProfileMode}
                            disabled={isProfileBusy}
                            onChange={(event) => setActiveProfileMode(event.target.value)}
                          >
                            {PROFILE_MODES.map((mode) => (
                              <option key={mode.id} value={mode.id}>{mode.label}</option>
                            ))}
                          </select>
                        </label>
                        <div className="upload-progress" aria-label="Upload progress">
                          <div className="upload-progress-head">
                            <span>Upload progress</span>
                            <span>{uploadProgress.percent}%</span>
                          </div>
                          <div className="upload-progress-track" aria-hidden="true">
                            <div className="upload-progress-fill" style={{ width: `${uploadProgress.percent}%` }} />
                          </div>
                          <p className="upload-progress-file" title={uploadProgress.fileName || 'No upload yet'}>
                            {uploadProgress.fileName || 'No upload yet'}
                          </p>
                        </div>
                      </div>

                      <div className="profile-upload-row profile-upload-row-fasta">
                        <label>
                          FASTA file
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
                    </>
                  )}

                  <div className="profile-analyze-row">
                    <p className="status analyze-status-inline">{status}</p>
                    <button
                      type="button"
                      className="analyze-primary"
                      onClick={() => runSelectedProfile()}
                      disabled={isProfileBusy}
                    >
                      {isProfileBusy ? (
                        <>
                          <Spinner /> Analyze
                        </>
                      ) : (
                        'Analyze'
                      )}
                    </button>
                  </div>
                </div>
              </article>

            </>
          ) : null}

          {/* REGENERATE TAB: upload results JSON and regenerate report artifacts */}
          {activeMode === 'regenerate' ? (
            <>
              <article className="card profile-input-card tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Regenerate from JSON</h2>
                    <p>Upload a results JSON and regenerate report artifacts with the active database.</p>
                  </div>
                  <DatabaseSelectorBar
                    databases={databases}
                    selectedDatabase={selectedDatabase}
                    selectedDatabaseId={selectedDatabaseId}
                    onDatabaseChange={setSelectedDatabaseId}
                    selectId="regenerate-db-select"
                    className="profile-db-bar"
                  />
                </div>
                <div className="profile-input-subtile section-subtile">
                  <div className="profile-upload-row profile-upload-row-fasta">
                    <label>
                      Results JSON
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
                    <p className="status">
                      If the JSON UUID does not match the selected database UUID, regeneration is blocked.
                      Database updates currently do not allow regeneration of reports from older database versions.
                    </p>
                  </div>

                  <div className="profile-analyze-row">
                    <p className="status analyze-status-inline">{status}</p>
                    <button
                      type="button"
                      className="analyze-primary"
                      onClick={() => runRegenerateFromJson()}
                      disabled={isRegenerateBusy || !jsonInputPath}
                    >
                      {isRegenerateBusy ? (
                        <>
                          <Spinner /> Regenerate
                        </>
                      ) : (
                        'Regenerate'
                      )}
                    </button>
                  </div>
                </div>
              </article>
            </>
          ) : null}

          {(activeMode === 'profile' || activeMode === 'regenerate') ? (
            <>
              <article className="card workspace-output-tile full-width-tile">
                <div className="workspace-output-header">
                  <h2>Report</h2>
                  <p>{inlineReportLabel || 'No report selected yet'}</p>
                </div>
                <div className="inline-actions report-actions">
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
                    onClick={openSelectedReportInline}
                    disabled={!selectedProfileReportPath}
                  >
                    Open below
                  </button>
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
                      if (selectedReportOption?.tabularPath) {
                        window.open(buildArtifactUrl(selectedReportOption.tabularPath), '_blank', 'noopener,noreferrer');
                      }
                    }}
                    disabled={!selectedReportOption?.tabularPath}
                  >
                    Download tabular
                  </button>
                </div>
                {inlineReportPath ? (
                  <iframe
                    title="ResistanceProfiler report"
                    src={buildReportUrl(inlineReportPath)}
                    className="workspace-frame"
                  />
                ) : (
                  <p className="status">Run profiling or regenerate from JSON and the report will open here.</p>
                )}
              </article>
            </>
          ) : null}

          {/* MUTATION TAB: interactive table with filter/sort and TSV export */}
          {activeMode === 'mutations' ? (
            <>
              <article className="card full-width-tile tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Mutation browser</h2>
                    <p>
                      {displayedRules.length} visible mutation row(s), {formulaRules.length} formula row(s)
                    </p>
                  </div>
                  <DatabaseSelectorBar
                    databases={databases}
                    selectedDatabase={selectedDatabase}
                    selectedDatabaseId={selectedDatabaseId}
                    onDatabaseChange={setSelectedDatabaseId}
                    selectId="mutation-db-select"
                    className="mutation-db-bar"
                  />
                </div>
                <div className="table-controls-container section-subtile">
                  <div className="table-controls">
                    <label>Filter:</label>
                    <select
                      value={mutationFilterColumn}
                      onChange={(event) => setMutationFilterColumn(event.target.value)}
                    >
                      <option value="-1">All columns</option>
                      {mutationColumns.map((column, index) => (
                        <option key={column.key} value={String(index)}>{column.label}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      placeholder="contains..."
                      value={mutationFilter}
                      onChange={(event) => setMutationFilter(event.target.value)}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setMutationFilter('');
                        setMutationFilterColumn('-1');
                      }}
                    >
                      Reset
                    </button>
                    <button
                      type="button"
                      className="download-tsv-btn"
                      onClick={downloadMutationsAsTsv}
                    >
                      Download as TSV
                    </button>
                  </div>
                </div>
              </article>

              <article className="card full-width-tile mutation-table-tile">
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
              </article>

              <article className="card full-width-tile mutation-table-tile formula-table-tile">
                <div className="workspace-output-header section-header">
                  <div>
                    <h3>Formula combinations</h3>
                    <p>{displayedFormulaRules.length} visible row(s)</p>
                  </div>
                </div>
                <div className="table-controls-container section-subtile">
                  <div className="table-controls">
                    <label>Filter:</label>
                    <select
                      value={formulaFilterColumn}
                      onChange={(event) => setFormulaFilterColumn(event.target.value)}
                    >
                      <option value="-1">All columns</option>
                      {formulaColumns.map((column, index) => (
                        <option key={column.key} value={String(index)}>{column.label}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      placeholder="contains..."
                      value={formulaFilter}
                      onChange={(event) => setFormulaFilter(event.target.value)}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setFormulaFilter('');
                        setFormulaFilterColumn('-1');
                      }}
                    >
                      Reset
                    </button>
                  </div>
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
                  <DatabaseSelectorBar
                    databases={databases}
                    selectedDatabase={selectedDatabase}
                    selectedDatabaseId={selectedDatabaseId}
                    onDatabaseChange={setSelectedDatabaseId}
                    selectId="database-tab-select"
                    className="mutation-db-bar"
                  />
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
                  {summaryTile || detailSections.length > 0 ? (
                    <div className="database-plot-grid">
                      {summaryTile ? <DatabasePieSummaryRow tile={summaryTile} /> : null}
                      {/* Chart controls are global for all gene tiles in this section. */}
                      <div className="database-phenotype-switch-row">
                        <span className="database-phenotype-switch-label">Mutations in each gene</span>
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
                    by pairwise mapping, and the sequence with the highest identity is selected. Currently, the tool
                    requires a sequence identity of at least 80% and coverage of at least 90% of either the reference
                    or the query sequence. This allows flexible use across a wide range of viruses and gene targets
                    without strict input-format requirements, but results can become unreliable when the input is
                    highly divergent from the reference sequences in the database.
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
