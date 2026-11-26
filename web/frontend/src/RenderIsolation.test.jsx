import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

const renderCounts = {
  appShell: 0,
  analyzePage: 0,
  singleAnalyzePanel: 0,
  batchAnalyzePanel: 0,
  mutationsPage: 0,
  databasePage: 0,
  resultsPage: 0,
  comparePage: 0,
  homePage: 0,
  appRoutes: 0,
};

function resetRenderCounts() {
  for (const key of Object.keys(renderCounts)) {
    renderCounts[key] = 0;
  }
}

function snapshotRenderCounts() {
  return { ...renderCounts };
}

vi.mock('./DashboardContext', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useOptionalShellLogic: () => {
      renderCounts.appShell += 1;
      return actual.useOptionalShellLogic();
    },
    useOptionalAnalyzePageLogic: () => {
      renderCounts.analyzePage += 1;
      return actual.useOptionalAnalyzePageLogic();
    },
    useOptionalSingleAnalyzeLogic: () => {
      renderCounts.singleAnalyzePanel += 1;
      return actual.useOptionalSingleAnalyzeLogic();
    },
    useOptionalBatchAnalyzeLogic: () => {
      renderCounts.batchAnalyzePanel += 1;
      return actual.useOptionalBatchAnalyzeLogic();
    },
    useOptionalMutationsLogic: () => {
      renderCounts.mutationsPage += 1;
      return actual.useOptionalMutationsLogic();
    },
    useOptionalDatabasePageLogic: () => {
      renderCounts.databasePage += 1;
      return actual.useOptionalDatabasePageLogic();
    },
    useOptionalResultsLogic: () => {
      renderCounts.resultsPage += 1;
      return actual.useOptionalResultsLogic();
    },
    useOptionalCompareLogic: () => {
      renderCounts.comparePage += 1;
      return actual.useOptionalCompareLogic();
    },
    useOptionalHomeLogic: () => {
      renderCounts.homePage += 1;
      return actual.useOptionalHomeLogic();
    },
  };
});

vi.mock('./hooks/usePageTitle', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    usePageTitle: (title) => {
      renderCounts.appRoutes += 1;
      return actual.usePageTitle(title);
    },
  };
});

vi.mock('./components/ComparisonVenn', () => ({
  ComparisonVenn: () => <div data-testid="venn-stub" />,
}));

import { DashboardProvider, useCoreActions, useOptionalAnalyzePageLogic, useOptionalDatabasePageLogic, useOptionalHomeLogic, useOptionalResultsLogic, useOptionalCompareLogic } from './DashboardContext';
import { AppShell } from './components/AppShell';
import { AppRoutes } from './AppRoutes';
import { AnalyzePage } from './components/pages/AnalyzePage';
import { MutationsPage } from './components/pages/MutationsPage';
import { ComparePage } from './components/pages/ComparePage';
import { TourProvider, TOUR_STORAGE_KEY, TOUR_VERSION } from './components/tour/TourContext';
import { buildTourSteps } from './components/tour/steps';
import { resetCompareState } from './components/pages/compareSessionState';
import { useMemo } from 'react';

function DatabasePageSpy() {
  const logic = useOptionalDatabasePageLogic();
  return <span data-testid="db-spy" data-rules-count={logic?.rules?.length ?? 0} />;
}

function ResultsPageSpy() {
  useOptionalResultsLogic();
  return null;
}

function ComparePageSpy() {
  useOptionalCompareLogic();
  return null;
}

function HomePageSpy() {
  useOptionalHomeLogic();
  return null;
}

function AnalyzePageSpy() {
  useOptionalAnalyzePageLogic();
  return null;
}

function ProductionComposition({ initialPath = '/analysis', tourStepsOverride = null, children }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <DashboardProvider>
        <CompositionInsideDashboard tourStepsOverride={tourStepsOverride}>
          {children}
        </CompositionInsideDashboard>
      </DashboardProvider>
    </MemoryRouter>
  );
}

function CompositionInsideDashboard({ tourStepsOverride, children }) {
  const { navigate, setActiveProfileMode, setAnalyzeSubMode } = useCoreActions();
  const tourSteps = useMemo(
    () => tourStepsOverride ?? buildTourSteps({ navigate, setActiveProfileMode, setAnalyzeSubMode }),
    [tourStepsOverride, navigate, setActiveProfileMode, setAnalyzeSubMode],
  );
  return (
    <TourProvider steps={tourSteps}>
      <AppShell>
        {children}
      </AppShell>
    </TourProvider>
  );
}

function setupStartupFetch({
  databases = [{ id: 'db1', display_name: 'DB One', has_example: true }],
  rules = [
    { id: 'r1', reference_name: 'REF1', feature: 'geneA', position: 10, reference: 'A', mutation: 'V', drug: 'DrugX', phenotype: 'resistant' },
    { id: 'r2', reference_name: 'REF1', feature: 'geneB', position: 25, reference: 'G', mutation: 'D', drug: 'DrugY', phenotype: 'susceptible' },
  ],
  formulaRules = [
    { formula_id: 'f1', reference_name: 'REF1', drug: 'DrugZ', label: 'A11V + G26D', normalized_expression: 'r1 & r2', member_count: 2, phenotype: 'resistant' },
  ],
} = {}) {
  globalThis.fetch = vi.fn((url) => {
    const u = String(url);
    if (u.includes('/api/ui/config')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          data: { batch_max_samples: 25, sample_limit_per_minute: 25, cli_version: '0.1.0' },
        }),
      });
    }
    if (u.includes('/api/ui/legal')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ data: { enabled: false } }) });
    }
    if (u.includes('/api/ui/contact')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ data: { enabled: false } }) });
    }
    if (u.includes('/api/databases/reference-accessions')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ data: { items: { db1: { ACC1: 'Ref A' }, db2: { ACC1: 'Ref A' } }, count: 2 } }),
      });
    }
    if (u.includes('/api/databases/shared-references')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            items: [{ accession: 'ACC1', name: 'Ref A', organism: 'Test', present_in: ['db1', 'db2'] }],
          },
        }),
      });
    }
    if (u.includes('/api/databases/compare')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            reference: { accession: 'ACC1', name: 'Ref A' },
            databases: ['db1', 'db2'],
            metadata_fields: ['drug', 'phenotype'],
            venn: [
              { region: ['db1'], count: 1 },
              { region: ['db1', 'db2'], count: 1 },
            ],
            counts: { total_unique: 2, intersection: 1, per_db: { db1: 2, db2: 1 } },
            rows: [
              {
                feature: 'geneA',
                position: 10,
                reference: 'A',
                mutation: 'V',
                drug: 'DrugX',
                region: ['db1', 'db2'],
                per_db: {
                  db1: { drug: 'DrugX', phenotype: 'resistant' },
                  db2: { drug: 'DrugX', phenotype: 'resistant' },
                },
              },
              {
                feature: 'geneB',
                position: 25,
                reference: 'G',
                mutation: 'D',
                drug: 'DrugY',
                region: ['db1'],
                per_db: {
                  db1: { drug: 'DrugY', phenotype: 'susceptible' },
                  db2: null,
                },
              },
            ],
          },
        }),
      });
    }
    if (u.includes('/api/databases')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ data: { items: databases } }) });
    }
    if (u.includes('/api/mutations')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          data: {
            items: rules,
            formula_items: formulaRules,
            plot_meta: { references: ['REF1'], features: ['geneA', 'geneB'] },
          },
        }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ data: {} }) });
  });
}

describe('Render isolation across DashboardProvider, AppShell, and routes (WA-054)', () => {
  beforeEach(() => {
    resetRenderCounts();
    resetCompareState();
    localStorage.clear();
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('isolates XHR uploadProgress ticks to UploadProgressIndicator without re-rendering AppShell, AnalyzePage, panels, or unrelated routes', async () => {
    setupStartupFetch();

    let latestXhr = null;
    class MockXHR {
      constructor() {
        this.upload = {};
        this.headers = {};
        latestXhr = this;
      }
      open() {}
      setRequestHeader(k, v) { this.headers[k] = v; }
      abort() {}
      send() {}
      triggerProgress(loaded, total) {
        this.upload.onprogress?.({ lengthComputable: true, loaded, total });
      }
      triggerDone(uploadId = 'up-1') {
        this.status = 200;
        this.responseText = JSON.stringify({ data: { upload_id: uploadId } });
        this.onload?.();
      }
    }
    vi.stubGlobal('XMLHttpRequest', MockXHR);

    const { container } = render(
      <ProductionComposition initialPath="/analysis">
        <AnalyzePage />
        <DatabasePageSpy />
        <ResultsPageSpy />
        <ComparePageSpy />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Example' })).toBeInTheDocument();
      expect(screen.getByTestId('db-spy')).toHaveAttribute('data-rules-count', '2');
    });

    const fileInput = container.querySelector('[data-tour-target="vcf-file"] input[type="file"]');
    const vcfFile = new File(['##fileformat=VCFv4.2\n'], 'sample.vcf', { type: 'text/plain' });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [vcfFile] } });
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    const before = snapshotRenderCounts();
    const ticks = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95];
    for (const pct of ticks) {
      act(() => {
        latestXhr.triggerProgress(pct, 100);
      });
    }

    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuenow', '95');

    expect(renderCounts.appShell - before.appShell).toBe(0);
    expect(renderCounts.analyzePage - before.analyzePage).toBe(0);
    expect(renderCounts.singleAnalyzePanel - before.singleAnalyzePanel).toBe(0);
    expect(renderCounts.databasePage - before.databasePage).toBe(0);
    expect(renderCounts.resultsPage - before.resultsPage).toBe(0);
    expect(renderCounts.comparePage - before.comparePage).toBe(0);

    await act(async () => {
      latestXhr.triggerDone('up-vcf-1');
    });
  });

  it('isolates MutationsPage search keystrokes and column sort without re-rendering AppShell or unrelated route spies', async () => {
    setupStartupFetch();

    render(
      <ProductionComposition initialPath="/databases/mutations">
        <MutationsPage />
        <DatabasePageSpy />
        <ResultsPageSpy />
        <AnalyzePageSpy />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(screen.getByText('geneA')).toBeInTheDocument();
    });

    const before = snapshotRenderCounts();
    const singleSearch = screen.getByLabelText('Search single mutations');
    for (const q of ['g', 'ge', 'gen', 'gene', 'geneA']) {
      fireEvent.change(singleSearch, { target: { value: q } });
    }
    const formulaSearch = screen.getByLabelText('Search combinatorial mutations');
    for (const q of ['D', 'Dr', 'DrugZ']) {
      fireEvent.change(formulaSearch, { target: { value: q } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Drug' }));

    expect(screen.getByText('geneA')).toBeInTheDocument();
    expect(screen.queryByText('geneB')).not.toBeInTheDocument();
    expect(renderCounts.mutationsPage - before.mutationsPage).toBeGreaterThan(0);
    expect(renderCounts.appShell - before.appShell).toBe(0);
    expect(renderCounts.databasePage - before.databasePage).toBe(0);
    expect(renderCounts.resultsPage - before.resultsPage).toBe(0);
    expect(renderCounts.analyzePage - before.analyzePage).toBe(0);
  });

  it('isolates ComparePage tableSearch keystrokes without re-rendering AppShell or unrelated route spies', async () => {
    setupStartupFetch({
      databases: [
        { id: 'db1', display_name: 'DB One' },
        { id: 'db2', display_name: 'DB Two' },
      ],
    });

    render(
      <ProductionComposition initialPath="/databases/compare">
        <ComparePage />
        <DatabasePageSpy />
        <ResultsPageSpy />
        <AnalyzePageSpy />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('db-spy')).toHaveAttribute('data-rules-count', '2');
      expect(screen.getByLabelText('Database 1')).toHaveValue('');
    });

    fireEvent.change(screen.getByLabelText('Database 1'), { target: { value: 'db1' } });
    fireEvent.change(screen.getByLabelText('Database 2'), { target: { value: 'db2' } });
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /shared reference/i })).toHaveValue('ACC1');
      expect(screen.getByRole('button', { name: 'Compare' })).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search mutations')).toBeInTheDocument();
    });

    const before = snapshotRenderCounts();
    const searchInput = screen.getByPlaceholderText('Search mutations');
    for (const q of ['g', 'ge', 'gen', 'gene', 'geneA']) {
      fireEvent.change(searchInput, { target: { value: q } });
    }

    expect(screen.getByText(/geneA/i)).toBeInTheDocument();
    expect(screen.queryByText(/geneB/i)).not.toBeInTheDocument();
    expect(renderCounts.appShell - before.appShell).toBe(0);
    expect(renderCounts.databasePage - before.databasePage).toBe(0);
    expect(renderCounts.resultsPage - before.resultsPage).toBe(0);
    expect(renderCounts.analyzePage - before.analyzePage).toBe(0);
  });

  it('isolates SingleAnalyzePanel sample-name keystrokes and BatchAnalyzePanel 1Hz cooldown ticks', async () => {
    setupStartupFetch();

    const { container } = render(
      <ProductionComposition initialPath="/analysis">
        <AnalyzePage />
        <DatabasePageSpy />
        <ResultsPageSpy />
        <ComparePageSpy />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(container.querySelector('.sample-name-input')).toBeInTheDocument();
      expect(screen.getByTestId('db-spy')).toHaveAttribute('data-rules-count', '2');
    });

    const beforeSingle = snapshotRenderCounts();
    const sampleInput = container.querySelector('.sample-name-input');
    for (const s of ['S', 'Sa', 'Sam', 'Samp', 'Sample1']) {
      fireEvent.change(sampleInput, { target: { value: s } });
    }
    expect(sampleInput).toHaveValue('Sample1');
    expect(renderCounts.singleAnalyzePanel - beforeSingle.singleAnalyzePanel).toBeGreaterThan(0);
    expect(renderCounts.appShell - beforeSingle.appShell).toBe(0);
    expect(renderCounts.analyzePage - beforeSingle.analyzePage).toBe(0);
    expect(renderCounts.databasePage - beforeSingle.databasePage).toBe(0);
    expect(renderCounts.resultsPage - beforeSingle.resultsPage).toBe(0);
    expect(renderCounts.comparePage - beforeSingle.comparePage).toBe(0);

    // Switch to Multiple Samples -> Batch FASTA mode and trigger 429 cooldown
    fireEvent.click(screen.getByRole('button', { name: /multiple samples/i }));
    fireEvent.click(screen.getByRole('button', { name: 'FASTA' }));

    let latestXhr = null;
    class MockBatchXHR {
      constructor() {
        this.upload = {};
        latestXhr = this;
      }
      open() {}
      setRequestHeader() {}
      abort() {}
      send() {
        this.status = 200;
        this.responseText = JSON.stringify({ data: { upload_id: 'up-fasta-1' } });
        this.onload?.();
      }
    }
    vi.stubGlobal('XMLHttpRequest', MockBatchXHR);

    const batchInput = container.querySelector('input[type="file"][multiple]');
    const fastaFile = new File(['>seq\nACGT\n'], 's1.fasta', { type: 'text/plain' });
    await act(async () => {
      fireEvent.change(batchInput, { target: { files: [fastaFile] } });
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Submit batch' })).not.toBeDisabled();
    });

    vi.useFakeTimers();
    const origFetch = globalThis.fetch;
    globalThis.fetch = vi.fn((url, init) => {
      if (String(url).includes('/api/profile/batch/fasta')) {
        return Promise.resolve({
          ok: false,
          status: 429,
          headers: new Headers({ 'Retry-After': '5' }),
          json: async () => ({ detail: 'Rate limit' }),
        });
      }
      return origFetch(url, init);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Submit batch' }));
    });
    expect(screen.getByText('Rate limit reached. Try again in 5s.')).toBeInTheDocument();

    const beforeCooldown = snapshotRenderCounts();
    for (let i = 0; i < 3; i += 1) {
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    }
    expect(screen.getByText('Rate limit reached. Try again in 2s.')).toBeInTheDocument();
    expect(renderCounts.batchAnalyzePanel - beforeCooldown.batchAnalyzePanel).toBe(3);
    expect(renderCounts.appShell - beforeCooldown.appShell).toBe(0);
    expect(renderCounts.analyzePage - beforeCooldown.analyzePage).toBe(0);
    expect(renderCounts.databasePage - beforeCooldown.databasePage).toBe(0);
    expect(renderCounts.resultsPage - beforeCooldown.resultsPage).toBe(0);
    expect(renderCounts.comparePage - beforeCooldown.comparePage).toBe(0);
    expect(latestXhr).not.toBeNull();
  });

  it('isolates Tour step transitions without re-rendering AppShell or HomePage', async () => {
    setupStartupFetch();
    const steps = [
      { id: 's1', targetSelector: '.top-bar', title: 'Step One', body: 'First' },
      { id: 's2', targetSelector: '.top-bar-nav', title: 'Step Two', body: 'Second' },
      { id: 's3', targetSelector: '.top-bar-actions', title: 'Step Three', body: 'Third' },
    ];

    render(
      <ProductionComposition initialPath="/" tourStepsOverride={steps}>
        <AppRoutes />
        <HomePageSpy />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Take a tour' })).toBeInTheDocument();
    });
    await act(async () => {
      await Promise.resolve();
    });

    const before = snapshotRenderCounts();
    fireEvent.click(screen.getByRole('button', { name: 'Take a tour' }));
    expect(screen.getByText('Step One')).toBeInTheDocument();
    expect(screen.getByText('Step 1 / 3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Step Two')).toBeInTheDocument();
    expect(screen.getByText('Step 2 / 3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Step Three')).toBeInTheDocument();
    expect(screen.getByText('Step 3 / 3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    expect(screen.queryByText('Step Three')).not.toBeInTheDocument();

    expect(renderCounts.appShell - before.appShell).toBe(0);
    expect(renderCounts.appRoutes - before.appRoutes).toBe(0);
    expect(renderCounts.homePage - before.homePage).toBe(0);
  });

  it('scopes profiling errors to Analyze and does not show them in Session Results or Mutations', async () => {
    setupStartupFetch();
    const origFetch = global.fetch;
    global.fetch = vi.fn((url, opts) => {
      if (String(url).includes('/api/profile/fasta')) {
        return Promise.resolve({
          ok: false,
          status: 400,
          headers: { get: () => 'application/json' },
          json: async () => ({ detail: 'No reference found' }),
        });
      }
      return origFetch(url, opts);
    });

    function ResultsAndMutationsProbe() {
      const resultsLogic = useOptionalResultsLogic();
      return (
        <div data-testid="results-status-error">{resultsLogic?.statusError || ''}</div>
      );
    }

    render(
      <ProductionComposition initialPath="/analysis">
        <AppRoutes />
        <ResultsAndMutationsProbe />
      </ProductionComposition>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Example' })).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Example' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('No reference found');
    });
    expect(screen.getByTestId('results-status-error')).toHaveTextContent('');
  });
});
