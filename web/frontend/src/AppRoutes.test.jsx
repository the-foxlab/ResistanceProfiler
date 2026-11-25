import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AppRoutes } from './AppRoutes';
import { AppShell } from './components/AppShell';
import { TourProvider } from './components/tour/TourContext';

// The route pages receive the dashboard logic as props; stubs suffice to check
// which page renders at which path.
function renderAt(path, props = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TourProvider steps={[]}>
        <AppShell logic={props.logic}>
          <AppRoutes {...props} />
        </AppShell>
      </TourProvider>
    </MemoryRouter>,
  );
}

const baseProps = {
  logic: {
    databases: [],
    selectedDatabase: null,
    selectedDatabaseId: '',
    setSelectedDatabaseId: () => {},
    statusError: '',
    legalLink: null,
    contactEmail: null,
    cliVersion: null,
    webVersion: '0.0.0',
    sessionResults: [],
    reportOptions: [],
    runExampleProfile: vi.fn(),
    activeProfileMode: 'vcf',
    setActiveProfileMode: () => {},
    analyzeSubMode: 'single',
    setAnalyzeSubMode: () => {},
    rules: [],
    formulaRules: [],
    mutationPlotMeta: { references: [], features: [] },
    PROFILE_MODES: [],
    isProfileBusy: false,
    isRegenerateBusy: false,
    canCancelJob: false,
    isCancelingJob: false,
    cancelActiveJob: () => {},
    runSelectedProfile: () => {},
    runRegenerateFromJson: () => {},
    uploadFastaFile: () => {},
    uploadVcfFile: () => {},
    uploadReferenceFile: () => {},
    uploadBamFile: () => {},
    uploadJsonFile: () => {},
    cancelUpload: () => {},
    uploadProgress: { percent: 0, fileName: '' },
    isUploading: false,
    vcfInput: {
      vcf_id: '',
      input_display_name: '',
      reference_id: '',
      bam_id: null,
      sample: '',
      min_af: 0.01,
      min_depth: 10,
    },
    setVcfInput: () => {},
    fastaInput: {
      fasta_id: '',
      input_display_name: '',
      sample: '',
    },
    setFastaInput: () => {},
    jsonInputId: '',
    batchMode: 'vcf',
    setBatchMode: () => {},
    batchVcfFiles: [],
    batchFastaFiles: [],
    batchJsonFiles: [],
    batchReferenceFasta: null,
    batchSamples: [],
    batchSubmitting: false,
    isBatchPolling: false,
    isBatchDownloadBusy: false,
    isBatchUploading: false,
    batchError: null,
    batchRateLimitCooldown: 0,
    setBatchRateLimitCooldown: () => {},
    batchSubmitted: false,
    batchMaxSamples: 25,
    batchVcfCutoffs: { min_af: 0.01, min_depth: 10 },
    setBatchVcfCutoffs: () => {},
    addBatchVcfFiles: () => {},
    addBatchFastaFiles: () => {},
    addBatchJsonFiles: () => {},
    addBatchBamFiles: () => {},
    attachBatchBam: () => {},
    removeBatchFile: () => {},
    uploadBatchReferenceFasta: () => {},
    submitBatch: () => {},
    stopBatchPolling: () => {},
    cancelBatchUpload: () => {},
    downloadAllBatchArtifacts: () => {},
    resetBatch: () => {},
    mutationFilter: '',
    setMutationFilter: () => {},
    mutationSortColumn: null,
    setMutationSortColumn: () => {},
    mutationSortAsc: true,
    setMutationSortAsc: () => {},
    formulaFilter: '',
    setFormulaFilter: () => {},
    mutationColumns: [],
    formulaColumns: [],
    displayedRules: [],
    displayedFormulaRules: [],
    mutationsLoaded: false,
    downloadMutationsAsTsv: () => {},
    downloadFormulaRulesAsTsv: () => {},
    inlineReportPath: '',
    selectedProfileReportPath: '',
    setSelectedProfileReportPath: () => {},
    selectedResultIndices: new Set(),
    setSelectedResultIndices: () => {},
    comparisonDbId: null,
    comparisonRefName: null,
    selectAllComparable: () => {},
    comparisonData: null,
    isComparisonBusy: false,
    nonSynonymousOnly: false,
    setNonSynonymousOnly: () => {},
    dbHitsOnly: false,
    setDbHitsOnly: () => {},
    fetchComparisonData: () => {},
    clearComparison: () => {},
    downloadSelectedArtifacts: () => {},
    isSessionDownloadBusy: false,
    buildReportUrl: () => '',
    buildArtifactUrl: () => '',
  },
};

describe('AppRoutes', () => {
  it('sends the resolved app theme to the embedded report', async () => {
    renderAt('/analysis', {
      ...baseProps,
      logic: {
        ...baseProps.logic,
        inlineReportPath: 'report-1',
        buildReportUrl: () => '/api/report?artifact_id=report-1',
        batchRateLimitCooldown: 0,
        setBatchRateLimitCooldown: vi.fn(),
      },
    });
    await waitFor(() => {
      expect(document.querySelector('.workspace-frame')).not.toBeNull();
    });
    const frame = document.querySelector('.workspace-frame');
    const postMessage = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});

    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    fireEvent.load(frame);

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        { type: 'respro:report-theme', theme: 'dark' },
        '*',
      );
    });
  });

  it('renders the landing page at /', async () => {
    renderAt('/', baseProps);
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('renders the analyze page at /analysis', async () => {
    renderAt('/analysis', baseProps);
    await waitFor(() => {
      expect(document.querySelector('.profile-input-card')).not.toBeNull();
    });
  });

  it('renders the reports page at /analysis/reports', async () => {
    renderAt('/analysis/reports', baseProps);
    expect(await screen.findByText(/No results yet/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Session results' })).toBeInTheDocument();
  });

  it('renders page descriptions outside the primary tile and keeps the results empty state inside', async () => {
    const { unmount } = renderAt('/analysis', baseProps);
    await waitFor(() => {
      expect(document.querySelector('.profile-input-card')).not.toBeNull();
    });
    const analyzeDescription = screen.getByText(/Profile VCF files, consensus FASTA sequences/);
    expect(analyzeDescription.closest('.tab-primary-tile')).toBeNull();

    unmount();
    renderAt('/analysis/reports', baseProps);
    const emptyState = await screen.findByText(/No results yet/);
    const resultsDescription = screen.getByText(/All analysis outputs from this session/);
    expect(resultsDescription.closest('.tab-primary-tile')).toBeNull();
    expect(emptyState.closest('.tab-primary-tile')).not.toBeNull();

    unmount();
    renderAt('/databases', baseProps);
    expect(await screen.findByText(/No active database loaded\./)).toBeInTheDocument();
    const databaseDescription = screen.getByText(/Overview and visual summaries of the active resistance database/);
    expect(databaseDescription.closest('.tab-primary-tile')).toBeNull();
  });

  it('renders the database dashboard at /databases', async () => {
    renderAt('/databases', baseProps);
    expect(await screen.findByText(/No active database loaded\./)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Database Dashboard' })).toBeInTheDocument();
  });

  it('renders browse mutations at /databases/mutations', async () => {
    renderAt('/databases/mutations', baseProps);
    await waitFor(() => {
      expect(document.querySelector('.tab-primary-tile')).not.toBeNull();
    });
    expect(screen.getByRole('heading', { level: 1, name: 'Browse mutations' })).toBeInTheDocument();
  });

  it('renders compare at /databases/compare', async () => {
    renderAt('/databases/compare', baseProps);
    expect(await screen.findByRole('heading', { level: 2, name: 'Rule overlap' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Compare databases' })).toBeInTheDocument();
  });

  it('renders about at /about', async () => {
    renderAt('/about', baseProps);
    expect(await screen.findByText(/about resistanceprofiler/i)).toBeInTheDocument();
  });

  it('renders a 404 page for unknown routes', async () => {
    renderAt('/does-not-exist', baseProps);
    expect(await screen.findByText(/page not found/i)).toBeInTheDocument();
  });
});
