import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AppRoutes } from './AppRoutes';
import { TourProvider } from './components/tour/TourContext';

// The route pages receive the dashboard logic as props; stubs suffice to check
// which page renders at which path.
function renderAt(path, props = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TourProvider steps={[]}>
        <AppRoutes {...props} />
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
    expect(screen.getByText(/session results/i)).toBeInTheDocument();
  });

  it('renders the database dashboard at /databases', () => {
    renderAt('/databases', baseProps);
    expect(screen.getByText(/database dashboard/i)).toBeInTheDocument();
  });

  it('renders browse mutations at /databases/mutations', () => {
    renderAt('/databases/mutations', baseProps);
    expect(screen.getByText(/browse mutations/i)).toBeInTheDocument();
  });

  it('renders compare at /databases/compare', () => {
    renderAt('/databases/compare', baseProps);
    expect(screen.getByText(/compare databases/i)).toBeInTheDocument();
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
