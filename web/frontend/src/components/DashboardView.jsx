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
import mutationsIconSrc from '../assets/icon-mutations.svg';
import { buildDatabasePlots } from './database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from './database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from './database-plots/DatabasePositionPlot';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { Spinner } from './Spinner';

const MODES = [
  { id: 'profile', label: 'Analyze', iconSrc: analyzeIconSrc },
  { id: 'database', label: 'Database', iconSrc: databaseIconSrc },
  { id: 'mutations', label: 'Browse mutations', iconSrc: mutationsIconSrc },
  { id: 'about', label: 'About', iconSrc: aboutIconSrc },
];

function _isPopulated(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function _displayValue(value, fallback = 'n/a') {
  return _isPopulated(value) ? String(value) : fallback;
}

function _listValue(values) {
  if (!Array.isArray(values) || values.length === 0) {
    return 'n/a';
  }
  return values.join(', ');
}

export function DashboardView({
  API_BASE,
  PROFILE_MODES,
  vcfInput,
  setVcfInput,
  fastaInput,
  setFastaInput,
  rules,
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
  mutationsLoaded,
  activeMode,
  setActiveMode,
  activeProfileMode,
  setActiveProfileMode,
  inlineReportPath,
  inlineReportLabel,
  mutationColumns,
  mutationPlotMeta,
  displayedRules,
  reportOptions,
  isProfileBusy,
  runSelectedProfile,
  openSelectedReportInline,
  buildReportUrl,
  uploadFastaFile,
  uploadVcfFile,
  uploadReferenceFile,
  uploadBamFile,
  downloadMutationsAsTsv,
  uploadProgress,
}) {
  // These controls only affect database charts, not mutation browsing or profiling.
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);

  const { summaryTile, detailSections, phenotypeMode, binSize } = useMemo(
    () => buildDatabasePlots(rules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize),
    [rules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize]
  );
  const activePhenotypeMode = phenotypeMode.activeMode;

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
                            onChange={(event) => setVcfInput({ ...vcfInput, sample: event.target.value })}
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
                    className="button-link"
                    onClick={openSelectedReportInline}
                    disabled={!selectedProfileReportPath}
                  >
                    Open below
                  </button>
                  <button
                    type="button"
                    className="button-link"
                    onClick={() => {
                      if (selectedProfileReportPath) {
                        window.open(buildReportUrl(selectedProfileReportPath), '_blank', 'noopener,noreferrer');
                      }
                    }}
                    disabled={!selectedProfileReportPath}
                  >
                    Open in new tab
                  </button>
                </div>
                {inlineReportPath ? (
                  <iframe
                    title="ResistanceProfiler report"
                    src={buildReportUrl(inlineReportPath)}
                    className="workspace-frame"
                  />
                ) : (
                  <p className="status">Run profiling and the report will open here.</p>
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
                    <p>{displayedRules.length} visible row(s)</p>
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
                  <p className="status">No mutations were found for the selected database/filter.</p>
                ) : null}
                {mutationsLoaded && rules.length > 0 && displayedRules.length === 0 ? (
                  <p className="status">No mutations match the current filter.</p>
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
                  ResistanceProfiler is a codon-aware antiviral resistance framework with a CLI-first core and a web
                  explorer for interactive analysis.
                </p>
              </div>

              <div className="about-notice">
                <strong>Research use only.</strong> This software supports exploratory interpretation and does not replace
                accredited clinical diagnostics.
              </div>

              <div className="about-grid">
                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutScopeIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Project Scope and How It Works</h3>
                  </div>
                  <p>
                    The project is pathogen-agnostic: references and rules are stored in a curated project database and
                    interpreted at amino-acid level.
                  </p>
                  <ul>
                    <li>Input can be consensus FASTA or VCF plus matching reference FASTA.</li>
                    <li>The core pipeline resolves reference context, maps changes codon-aware, and matches rules.</li>
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
                    <li><strong>Anchored deletion:</strong> <strong>V215del</strong> means residue V at anchor position 215 is deleted.</li>
                    <li><strong>Anchored insertion:</strong> <strong>215_216insG</strong> means insertion of G between anchor positions 215 and 216.</li>
                    <li><strong>Frameshift:</strong> <strong>L201fs</strong> indicates a reading-frame shift starting at anchor position 201.</li>
                    <li><strong>Phenotype</strong> captures in-vitro susceptibility interpretation.</li>
                    <li><strong>Clinical phenotype</strong> captures treatment-oriented interpretation where available.</li>
                    <li>Combination context can matter; interpreted annotations are shown in reports and dashboard plots.</li>
                  </ul>
                </section>

                <section className="about-section-card">
                  <div className="about-section-title">
                    <img src={aboutCliIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>CLI and Extended Functionality</h3>
                  </div>
                  <p>
                    The CLI is the primary interface and includes project creation, rule curation, profiling, and export.
                  </p>
                  <div className="about-command-block">
                    <code>respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db</code>
                    <code>respro add --project project.db --rules more_rules.tsv</code>
                    <code>respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/</code>
                  </div>
                  <p className="about-mini-heading">Start your own ResPro Explorer</p>
                  <div className="about-command-block">
                    <code>docker compose -f docker-compose.web.yml up --build</code>
                  </div>
                  <p>Open <strong>http://127.0.0.1:8000/app</strong> after startup.</p>
                </section>

                <section className="about-section-card about-section-card-contact">
                  <div className="about-section-title">
                    <img src={aboutCommunityIconSrc} alt="" aria-hidden="true" className="about-section-icon" />
                    <h3>Contributing and Contact</h3>
                  </div>
                  <p>
                    Contributions are very welcome, especially curated rule datasets, bug reports, reproducible test
                    cases, and code improvements.
                  </p>
                  <div className="about-contact-list">
                    <a href="mailto:jonas.fuchs@uniklinik-freiburg.de">jonas.fuchs@uniklinik-freiburg.de</a>
                    <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer">
                      github.com/jonas-fuchs/ResistanceProfiler
                    </a>
                    <a href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer">
                      Jonas Fuchs Team website
                    </a>
                  </div>

                  <p className="about-mini-heading">Data usage</p>
                  <ul>
                    <li>Uploaded inputs and generated reports are stored in the configured local data directory.</li>
                    <li>Session-scoped uploads/reports are cleaned up automatically when a browser tab closes.</li>
                    <li>As a self-hosted deployment, data governance and retention remain under the operator's control.</li>
                  </ul>

                  <p className="about-mini-heading">Licensing</p>
                  <ul>
                    <li>ResistanceProfiler source code is released under the MIT License.</li>
                    <li>External references, rules, and publication-linked datasets may have separate licenses or citation requirements.</li>
                    <li>Users are responsible for compliant use of third-party data in their own environments.</li>
                  </ul>

                  <p className="about-backend-note">Current backend endpoint: {API_BASE || 'same-origin API'}</p>
                </section>
              </div>
            </article>
          ) : null}
        </section>
      </div>
    </main>
  );
}
