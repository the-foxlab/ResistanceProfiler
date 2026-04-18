import { useEffect, useMemo, useState } from 'react';

import logoSrc from '../assets/logo.svg';
import websiteIconSrc from '../assets/website.svg';
import aboutIconSrc from '../assets/icon-about.svg';
import analyzeIconSrc from '../assets/icon-analyze.svg';
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
}) {
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);

  const { summaryTile, detailSections, phenotypeMode, binSize } = useMemo(
    () => buildDatabasePlots(rules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize),
    [rules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize]
  );

  useEffect(() => {
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
          <a className="brand-logo-wrap" href="/app" aria-label="ResistanceProfiler dashboard">
            <img className="brand-logo" src={logoSrc} alt="ResistanceProfiler logo" />
          </a>
          <div className="page-links" aria-label="Project links">
            <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer" title="ResistanceProfiler on GitHub" aria-label="ResistanceProfiler on GitHub">
              <img className="page-link-icon" src={githubIconSrc} alt="" aria-hidden="true" />
            </a>
            <a href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer" title="Fuchs & Team website" aria-label="Fuchs & Team website">
              <img className="page-link-icon website-link-icon" src={websiteIconSrc} alt="" aria-hidden="true" />
            </a>
          </div>
        </div>

        <section className="panel-stack">
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

          {activeMode === 'database' ? (
            <>
              <article className="card full-width-tile database-plots-tile tab-primary-tile">
                <div className="workspace-output-header workspace-output-header-with-db section-header">
                  <div>
                    <h2>Database analysis</h2>
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
                      <div className="database-phenotype-switch-row">
                        <span className="database-phenotype-switch-label">Mutations in each gene</span>
                        <div className="database-phenotype-switch-controls">
                          {phenotypeMode.hasPhenotype && phenotypeMode.hasClinical ? (
                            <div className="database-phenotype-switch" role="group" aria-label="Position annotation mode">
                              <button
                                type="button"
                                className={requestedPhenotypeMode === 'phenotype' ? 'active' : ''}
                                onClick={() => setRequestedPhenotypeMode('phenotype')}
                              >
                                Phenotype
                              </button>
                              <button
                                type="button"
                                className={requestedPhenotypeMode === 'clinical' ? 'active' : ''}
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

          {activeMode === 'about' ? (
            <article className="card about-tile">
              <p className="status">ResistanceProfiler provides codon-aware resistance interpretation for VCF and FASTA workflows.</p>
              <div className="database-meta">
                <p><strong>Repository:</strong> <a href="https://github.com/jonas-fuchs/ResistanceProfiler" target="_blank" rel="noreferrer">github.com/jonas-fuchs/ResistanceProfiler</a></p>
                <p><strong>Research group:</strong> <a href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer">Jonas Fuchs Team</a></p>
                <p><strong>Current backend:</strong> {API_BASE || 'same-origin API'}</p>
              </div>
            </article>
          ) : null}
        </section>
      </div>
    </main>
  );
}
