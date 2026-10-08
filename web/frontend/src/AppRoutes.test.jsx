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
    mutationPlotMeta: [],
    PROFILE_MODES: [],
    isProfileBusy: false,
    isRegenerateBusy: false,
    uploadProgress: { percent: 0, name: '' },
    isUploading: false,
    vcfInput: { sample: '', minAf: 0.01, minDepth: 10 },
    fastaInput: { sample: '' },
    mutationColumns: [],
    formulaColumns: [],
    displayedRules: [],
    displayedFormulaRules: [],
    mutationsLoaded: false,
    inlineReportPath: null,
    inlineReportLabel: '',
    selectedProfileReportPath: null,
    selectedResultIndices: new Set(),
    comparisonDbId: null,
    comparisonRefName: null,
    comparisonData: null,
    isComparisonBusy: false,
    nonSynonymousOnly: false,
    setNonSynonymousOnly: () => {},
    dbHitsOnly: false,
    setDbHitsOnly: () => {},
    fetchComparisonData: () => {},
    clearComparison: () => {},
    downloadAllSessionArtifacts: () => {},
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
    const frame = document.querySelector('.workspace-frame');
    const postMessage = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});

    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    fireEvent.load(frame);

    await waitFor(() => {
      expect(postMessage).toHaveBeenCalledWith(
        { type: 'respro:report-theme', theme: 'dark' },
        window.location.origin,
      );
    });
  });

  it('renders the landing page at /', () => {
    renderAt('/', baseProps);
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('renders the analyze page at /analysis', () => {
    renderAt('/analysis', baseProps);
    expect(document.querySelector('.profile-input-card')).not.toBeNull();
  });

  it('renders the reports page at /analysis/reports', () => {
    renderAt('/analysis/reports', baseProps);
    expect(screen.getByRole('heading', { level: 1, name: 'Session results' })).toBeInTheDocument();
  });

  it('renders page descriptions outside the primary tile and keeps the results empty state inside', () => {
    const { unmount } = renderAt('/analysis', baseProps);
    const analyzeDescription = screen.getByText(/Profile VCF files, consensus FASTA sequences/);
    expect(analyzeDescription.closest('.tab-primary-tile')).toBeNull();

    unmount();
    renderAt('/analysis/reports', baseProps);
    const resultsDescription = screen.getByText(/All analysis outputs from this session/);
    const emptyState = screen.getByText(/No results yet/);
    expect(resultsDescription.closest('.tab-primary-tile')).toBeNull();
    expect(emptyState.closest('.tab-primary-tile')).not.toBeNull();

    unmount();
    renderAt('/databases', baseProps);
    const databaseDescription = screen.getByText(/Overview and visual summaries of the active resistance database/);
    expect(databaseDescription.closest('.tab-primary-tile')).toBeNull();
  });

  it('renders the database dashboard at /databases', () => {
    renderAt('/databases', baseProps);
    expect(screen.getByRole('heading', { level: 1, name: 'Database Dashboard' })).toBeInTheDocument();
  });

  it('renders browse mutations at /databases/mutations', () => {
    renderAt('/databases/mutations', baseProps);
    expect(screen.getByRole('heading', { level: 1, name: 'Browse mutations' })).toBeInTheDocument();
  });

  it('renders compare at /databases/compare', () => {
    renderAt('/databases/compare', baseProps);
    expect(screen.getByRole('heading', { level: 1, name: 'Compare databases' })).toBeInTheDocument();
  });

  it('renders about at /about', () => {
    renderAt('/about', baseProps);
    expect(screen.getByText(/about resistanceprofiler/i)).toBeInTheDocument();
  });

  it('renders a 404 page for unknown routes', () => {
    renderAt('/does-not-exist', baseProps);
    expect(screen.getByText(/page not found/i)).toBeInTheDocument();
  });
});
