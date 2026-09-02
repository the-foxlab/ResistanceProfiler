import { useEffect, useState } from 'react';
import logoSrc from '../assets/logo.svg';
import aboutIconSrc from '../assets/icon-about.svg';
import databaseIconSrc from '../assets/icon-database.svg';
import githubIconSrc from '../assets/icon-github.svg';
import websiteIconSrc from '../assets/website.svg';
import mutationsIconSrc from '../assets/search.svg';
import homeIconSrc from '../assets/home.svg';
import reportIconSrc from '../assets/reports.svg';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { AnalyzeTab } from './tabs/AnalyzeTab';
import { ResultsTab } from './tabs/ResultsTab';
import { MutationsTab } from './tabs/MutationsTab';
import { DatabaseTab } from './tabs/DatabaseTab';
import { AboutTab } from './tabs/AboutTab';
import { AppFooter } from './AppFooter';
import { useTour } from './tour/TourContext';
import { TourOverlay } from './tour/TourOverlay';

const MODES = [
  { id: 'analyze', label: 'Analysis', iconSrc: homeIconSrc },
  { id: 'results', label: 'Reports', iconSrc: reportIconSrc },
  { id: 'database', label: 'Database Dashboard', iconSrc: databaseIconSrc },
  { id: 'mutations', label: 'Browse Mutations', iconSrc: mutationsIconSrc },
  { id: 'about', label: 'About', iconSrc: aboutIconSrc },
];



export function DashboardView({
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
  legalLink,
  contactEmail,
  cliVersion,
  webVersion,
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
  runExampleProfile,
  buildReportUrl,
  buildArtifactUrl,
  uploadFastaFile,
  uploadVcfFile,
  uploadReferenceFile,
  uploadBamFile,
  uploadJsonFile,
  jsonInputId,
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
  addBatchBamFiles,
  attachBatchBam,
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
  const isAnalyzeScopeLocked = isProfileBusy || isRegenerateBusy || batchSubmitting;
  const { startTour } = useTour();
  // Off-canvas sidebar state for mobile. Toggled by the hamburger button;
  // auto-closed whenever the active mode changes so navigating away from a
  // mode never leaves the drawer open over the new content.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [showScrollTop, setShowScrollTop] = useState(false);

  useEffect(() => {
    const updateScrollTopVisibility = () => {
      setShowScrollTop(window.scrollY > 240);
    };

    window.addEventListener('scroll', updateScrollTopVisibility, { passive: true });
    updateScrollTopVisibility();
    return () => window.removeEventListener('scroll', updateScrollTopVisibility);
  }, []);

  // Close the off-canvas drawer on Escape while it is open. Bound only when
  // the drawer is open so it never swallows Escape intended for other widgets
  // (e.g. the plot modal in AnalyzeTab manages its own Escape listener).
  useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setMobileNavOpen(false);
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [mobileNavOpen]);

  const handleSelectMode = (modeId) => {
    setActiveMode(modeId);
    setMobileNavOpen(false);
  };

  return (
    <main className="dashboard-shell">
      {/* Hamburger toggle for the off-canvas sidebar. Hidden on desktop via
          CSS (.mobile-menu-btn is display:none above --mobile-breakpoint). */}
      <button
        type="button"
        className={`mobile-menu-btn ${mobileNavOpen ? 'open' : ''}`}
        aria-label="Toggle navigation"
        aria-expanded={mobileNavOpen}
        aria-controls="sidebar-rail"
        onClick={() => setMobileNavOpen((v) => !v)}
      >
        <span className="mobile-menu-bar" aria-hidden="true" />
        <span className="mobile-menu-bar" aria-hidden="true" />
        <span className="mobile-menu-bar" aria-hidden="true" />
      </button>
      {/* Left rail only switches visible mode; all data lives in shared hook state. */}
      <aside
        id="sidebar-rail"
        className={`sidebar-rail ${mobileNavOpen ? 'open' : ''}`}
        aria-label="Dashboard modes"
      >
        <nav className="sidebar-rail-nav">
          {MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              className={`sidebar-rail-link ${activeMode === mode.id ? 'active' : ''} ${mode.id === 'about' ? 'about-tab' : ''}`}
              onClick={() => handleSelectMode(mode.id)}
              aria-label={mode.label}
              data-tour-target={`sidebar-${mode.id}`}
            >
              <span className="sidebar-icon-mask" style={{ '--icon-src': `url(${mode.iconSrc})` }} aria-hidden="true" />
              <span className="sidebar-rail-text">{mode.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      {/* Scrim behind the off-canvas drawer on mobile. Hidden on desktop and
          whenever the drawer is closed via CSS (.is-open). Clicking it
          dismisses the drawer, mirroring a modal overlay. */}
      <div
        className={`mobile-nav-backdrop ${mobileNavOpen ? 'is-open' : ''}`}
        aria-hidden="true"
        onClick={() => setMobileNavOpen(false)}
      />

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
            <a href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer" title="ResistanceProfiler on GitHub" aria-label="ResistanceProfiler on GitHub">
              <img className="page-link-icon" src={githubIconSrc} alt="" aria-hidden="true" />
            </a>
            <a href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer" title="Jonas Fuchs Team website" aria-label="Jonas Fuchs Team website">
              <img className="page-link-icon website-link-icon" src={websiteIconSrc} alt="" aria-hidden="true" />
            </a>
          </div>
        </div>

        <section className="panel-stack">
          {activeMode === 'analyze' && (
            <AnalyzeTab
              selectedDatabase={selectedDatabase}
              vcfInput={vcfInput}
              setVcfInput={setVcfInput}
              fastaInput={fastaInput}
              setFastaInput={setFastaInput}
              jsonInputId={jsonInputId}
              isRegenerateBusy={isRegenerateBusy}
              runRegenerateFromJson={runRegenerateFromJson}
              uploadFastaFile={uploadFastaFile}
              uploadVcfFile={uploadVcfFile}
              uploadReferenceFile={uploadReferenceFile}
              uploadBamFile={uploadBamFile}
              uploadJsonFile={uploadJsonFile}
              uploadProgress={uploadProgress}
              activeProfileMode={activeProfileMode}
              setActiveProfileMode={setActiveProfileMode}
              analyzeSubMode={analyzeSubMode}
              setAnalyzeSubMode={setAnalyzeSubMode}
              isProfileBusy={isProfileBusy}
              canCancelJob={canCancelJob}
              isCancelingJob={isCancelingJob}
              cancelActiveJob={cancelActiveJob}
              runSelectedProfile={runSelectedProfile}
              runExampleProfile={runExampleProfile}
              statusError={statusError}
              selectedProfileReportPath={selectedProfileReportPath}
              setSelectedProfileReportPath={setSelectedProfileReportPath}
              reportOptions={reportOptions}
              buildReportUrl={buildReportUrl}
              buildArtifactUrl={buildArtifactUrl}
              batchMode={batchMode}
              setBatchMode={setBatchMode}
              batchVcfFiles={batchVcfFiles}
              batchFastaFiles={batchFastaFiles}
              batchReferenceFasta={batchReferenceFasta}
              batchSamples={batchSamples}
              batchSubmitting={batchSubmitting}
              isBatchDownloadBusy={isBatchDownloadBusy}
              batchError={batchError}
              batchRateLimitCooldown={batchRateLimitCooldown}
              setBatchRateLimitCooldown={setBatchRateLimitCooldown}
              batchSubmitted={batchSubmitted}
              batchMaxSamples={batchMaxSamples}
              sampleLimitPerMinute={sampleLimitPerMinute}
              batchVcfCutoffs={batchVcfCutoffs}
              setBatchVcfCutoffs={setBatchVcfCutoffs}
              addBatchVcfFiles={addBatchVcfFiles}
              addBatchFastaFiles={addBatchFastaFiles}
              addBatchBamFiles={addBatchBamFiles}
              attachBatchBam={attachBatchBam}
              removeBatchFile={removeBatchFile}
              uploadBatchReferenceFasta={uploadBatchReferenceFasta}
              submitBatch={submitBatch}
              downloadAllBatchArtifacts={downloadAllBatchArtifacts}
              resetBatch={resetBatch}
              inlineReportPath={inlineReportPath}
              isAnalyzeScopeLocked={isAnalyzeScopeLocked}
              PROFILE_MODES={PROFILE_MODES}
            />
          )}
          {activeMode === 'results' && (
            <ResultsTab
              sessionResults={sessionResults}
              selectedProfileReportPath={selectedProfileReportPath}
              setSelectedProfileReportPath={setSelectedProfileReportPath}
              inlineReportPath={inlineReportPath}
              inlineReportLabel={inlineReportLabel}
              reportOptions={reportOptions}
              buildReportUrl={buildReportUrl}
              buildArtifactUrl={buildArtifactUrl}
              downloadAllSessionArtifacts={downloadAllSessionArtifacts}
              downloadSelectedArtifacts={downloadSelectedArtifacts}
              isSessionDownloadBusy={isSessionDownloadBusy}
              selectedResultIndices={selectedResultIndices}
              setSelectedResultIndices={setSelectedResultIndices}
              comparisonDbId={comparisonDbId}
              comparisonRefName={comparisonRefName}
              selectAllComparable={selectAllComparable}
              comparisonData={comparisonData}
              isComparisonBusy={isComparisonBusy}
              nonSynonymousOnly={nonSynonymousOnly}
              setNonSynonymousOnly={setNonSynonymousOnly}
              dbHitsOnly={dbHitsOnly}
              setDbHitsOnly={setDbHitsOnly}
              fetchComparisonData={fetchComparisonData}
              clearComparison={clearComparison}
            />
          )}
          {activeMode === 'mutations' && (
            <MutationsTab
              rules={rules}
              formulaRules={formulaRules}
              mutationColumns={mutationColumns}
              formulaColumns={formulaColumns}
              displayedRules={displayedRules}
              displayedFormulaRules={displayedFormulaRules}
              mutationFilter={mutationFilter}
              setMutationFilter={setMutationFilter}
              mutationFilterColumn={mutationFilterColumn}
              setMutationFilterColumn={setMutationFilterColumn}
              mutationSortColumn={mutationSortColumn}
              setMutationSortColumn={setMutationSortColumn}
              mutationSortAsc={mutationSortAsc}
              setMutationSortAsc={setMutationSortAsc}
              formulaFilter={formulaFilter}
              setFormulaFilter={setFormulaFilter}
              formulaFilterColumn={formulaFilterColumn}
              setFormulaFilterColumn={setFormulaFilterColumn}
              mutationPlotMeta={mutationPlotMeta}
              mutationsLoaded={mutationsLoaded}
              downloadMutationsAsTsv={downloadMutationsAsTsv}
              downloadFormulaRulesAsTsv={downloadFormulaRulesAsTsv}
              databases={databases}
              selectedDatabaseId={selectedDatabaseId}
            />
          )}
          {activeMode === 'database' && (
            <DatabaseTab
              rules={rules}
              formulaRules={formulaRules}
              mutationPlotMeta={mutationPlotMeta}
              selectedDatabase={selectedDatabase}
            />
          )}
          {activeMode === 'about' && (
            <AboutTab
              setActiveMode={setActiveMode}
              onStartTour={startTour}
              contactEmail={contactEmail}
            />
          )}
        </section>
        <AppFooter
          legalLink={legalLink}
          contactEmail={contactEmail}
          cliVersion={cliVersion}
          webVersion={webVersion}
        />
      </div>
      <button
        type="button"
        className={`scroll-top-button ${showScrollTop ? 'is-visible' : ''}`}
        title="Scroll to top"
        aria-label="Scroll to top"
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      >
        ↑
      </button>
      <TourOverlay />
    </main>
  );
}
