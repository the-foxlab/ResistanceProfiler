import { useEffect, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useSessionResults } from '../../hooks/useSessionResults';
import { useComparisonManager } from '../../hooks/useComparisonManager';
import { ResultsPage } from './ResultsPage';

vi.mock('../ComparisonHeatmap', () => ({
  ComparisonHeatmap: ({ data, isBusy }) => (
    <div data-testid="heatmap-stub" data-busy={String(Boolean(isBusy))}>
      {data?.samples?.join(', ')}
    </div>
  ),
}));

const RESULTS = [
  {
    run_id: 'run-1',
    mode: 'vcf',
    sample_name: 'SampleA',
    reference_name: 'REF1',
    database_id: 'db1',
    created_at: '2026-01-01T00:00:00Z',
    report_html_path: 'a-html',
    report_json_path: 'a-json',
    artifact_ids: ['a-html', 'a-json'],
  },
  {
    run_id: 'run-2',
    mode: 'fasta',
    sample_name: 'SampleB',
    reference_name: 'REF1',
    database_id: 'db1',
    created_at: '2026-01-01T00:01:00Z',
    report_html_path: 'b-html',
    report_json_path: 'b-json',
    artifact_ids: ['b-html', 'b-json'],
  },
  {
    run_id: 'run-3',
    mode: 'vcf',
    sample_name: 'SampleC',
    reference_name: 'REF2',
    database_id: 'db2',
    created_at: '2026-01-01T00:02:00Z',
    report_html_path: 'c-html',
    report_json_path: 'c-json',
    artifact_ids: ['c-html', 'c-json'],
  },
];

function makeLogic(overrides = {}) {
  return {
    sessionResults: RESULTS,
    statusError: '',
    buildReportUrl: (id) => `/api/report?artifact_id=${id}`,
    buildArtifactUrl: (id) => `/api/artifact?artifact_id=${id}`,
    downloadSelectedArtifacts: vi.fn(),
    isSessionDownloadBusy: false,
    selectedResultIndices: new Set(),
    setSelectedResultIndices: vi.fn(),
    comparisonDbId: null,
    comparisonRefName: null,
    selectAllComparable: vi.fn(),
    comparisonData: null,
    isComparisonBusy: false,
    nonSynonymousOnly: false,
    setNonSynonymousOnly: vi.fn(),
    dbHitsOnly: false,
    setDbHitsOnly: vi.fn(),
    fetchComparisonData: vi.fn(),
    clearComparison: vi.fn(),
    ...overrides,
  };
}

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function RealHookHarness({ initialResults = RESULTS }) {
  const [statusError, setStatusError] = useState('');
  const session = useSessionResults();
  const { setSessionResults } = session;
  useEffect(() => {
    setSessionResults(initialResults);
  }, [initialResults, setSessionResults]);
  const comparison = useComparisonManager({
    sessionResults: session.sessionResults,
    selectedResultIndices: session.selectedResultIndices,
    setSelectedResultIndices: session.setSelectedResultIndices,
    setStatusError,
  });

  const logic = {
    ...session,
    ...comparison,
    statusError,
    buildReportUrl: (id) => `/api/report?artifact_id=${id}`,
    buildArtifactUrl: (id) => `/api/artifact?artifact_id=${id}`,
    downloadSelectedArtifacts: () => session.downloadSelectedArtifacts(setStatusError),
  };

  return <ResultsPage logic={logic} />;
}

describe('ResultsPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders statusError with role="alert" both when sessionResults is empty and when populated', () => {
    const { rerender } = render(
      <ResultsPage logic={makeLogic({ sessionResults: [], statusError: 'Download failed' })} />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Download failed');
    expect(screen.getByText(/no results yet/i)).toBeInTheDocument();

    rerender(<ResultsPage logic={makeLogic({ sessionResults: RESULTS, statusError: 'Compare failed' })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Compare failed');
  });

  it('disables download when nothing selected and shows spinner when isSessionDownloadBusy is true', () => {
    const { rerender } = render(<ResultsPage logic={makeLogic()} />);
    const downloadBtn = screen.getByRole('button', { name: 'Download' });
    expect(downloadBtn).toBeDisabled();

    rerender(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0]),
          isSessionDownloadBusy: true,
        })}
      />,
    );
    const busyBtn = screen.getByRole('button', { name: /preparing/i });
    expect(busyBtn).toBeDisabled();
  });

  it('keeps checked incompatible rows enabled so they can be unchecked, while disabling unselected incompatible rows', () => {
    const { rerender } = render(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
        })}
      />,
    );

    const rowB = screen.getByText('SampleB').closest('tr');
    const checkboxB = within(rowB).getByRole('checkbox');
    expect(checkboxB).not.toBeChecked();
    expect(checkboxB).toBeEnabled();

    const rowC = screen.getByText('SampleC').closest('tr');
    const checkboxC = within(rowC).getByRole('checkbox');
    expect(checkboxC).not.toBeChecked();
    expect(checkboxC).toBeDisabled();

    rerender(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 1, 2]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
        })}
      />,
    );

    const checkedRowC = screen.getByText('SampleC').closest('tr');
    const checkedCheckboxC = within(checkedRowC).getByRole('checkbox');
    expect(checkedCheckboxC).toBeChecked();
    expect(checkedCheckboxC).toBeEnabled();
  });

  it('matches rows with missing/empty reference_name when comparisonRefName is null', () => {
    const emptyRefResults = [
      { ...RESULTS[0], reference_name: '' },
      { ...RESULTS[1], reference_name: null },
    ];
    render(
      <ResultsPage
        logic={makeLogic({
          sessionResults: emptyRefResults,
          selectedResultIndices: new Set([0]),
          comparisonDbId: 'db1',
          comparisonRefName: null,
        })}
      />,
    );

    const rowB = screen.getByText('SampleB').closest('tr');
    expect(within(rowB).getByRole('checkbox')).toBeEnabled();
  });

  it('enables Clear comparison during isComparisonBusy and gates filter refetches on isSelectionComparable', () => {
    const fetchComparisonData = vi.fn();
    const setNonSynonymousOnly = vi.fn();
    const setDbHitsOnly = vi.fn();

    const { rerender } = render(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 1]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
          isComparisonBusy: true,
          comparisonData: null,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: /clear comparison/i })).toBeEnabled();

    // Heterogeneous selection with stale comparisonData: toggling filters must NOT refetch.
    rerender(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 2]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
          isComparisonBusy: false,
          comparisonData: { samples: ['SampleA', 'SampleB'], mutations: ['M1'], references: ['REF1'] },
          fetchComparisonData,
          setNonSynonymousOnly,
          setDbHitsOnly,
        })}
      />,
    );

    fireEvent.click(screen.getByLabelText(/non-synonymous only/i));
    fireEvent.click(screen.getByLabelText(/db hits only/i));
    expect(fetchComparisonData).not.toHaveBeenCalled();

    // Homogeneous selection: toggling filters DOES refetch.
    rerender(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 1]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
          isComparisonBusy: false,
          comparisonData: { samples: ['SampleA', 'SampleB'], mutations: ['M1'], references: ['REF1'] },
          fetchComparisonData,
          setNonSynonymousOnly,
          setDbHitsOnly,
        })}
      />,
    );

    fireEvent.click(screen.getByLabelText(/non-synonymous only/i));
    fireEvent.click(screen.getByLabelText(/db hits only/i));
    expect(fetchComparisonData).toHaveBeenCalledTimes(2);
  });

  it('integrates real useSessionResults and useComparisonManager hooks for heterogeneous uncheck, compare, clear abort, and download error', async () => {
    const compareDeferred = makeDeferred();
    let compareSignal;
    vi.stubGlobal('fetch', vi.fn((url, options) => {
      if (String(url).includes('/api/compare')) {
        compareSignal = options?.signal;
        return compareDeferred.promise;
      }
      if (String(url).includes('/api/artifact-bundle')) {
        return Promise.reject(new Error('Zip bundle failed'));
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }));

    render(<RealHookHarness />);
    await screen.findByText('SampleA');

    // Select all -> heterogeneous selection keeps Compare disabled; row SampleC remains enabled so user can uncheck.
    fireEvent.click(screen.getByLabelText('Select all results'));
    const compareBtn = screen.getByRole('button', { name: 'Compare selected' });
    expect(compareBtn).toBeDisabled();

    const rowC = screen.getByText('SampleC').closest('tr');
    const checkboxC = within(rowC).getByRole('checkbox');
    expect(checkboxC).toBeEnabled();
    fireEvent.click(checkboxC);

    expect(compareBtn).toBeEnabled();

    // Trigger compare -> busy -> Clear comparison aborts in-flight /api/compare.
    fireEvent.click(compareBtn);
    expect(screen.getByRole('button', { name: /comparing/i })).toBeDisabled();
    const clearBtn = screen.getByRole('button', { name: 'Clear comparison' });
    expect(clearBtn).toBeEnabled();
    expect(compareSignal.aborted).toBe(false);

    fireEvent.click(clearBtn);
    expect(compareSignal.aborted).toBe(true);
    expect(clearBtn).toBeDisabled();

    await act(async () => {
      const abortErr = new Error('Aborted');
      abortErr.name = 'AbortError';
      compareDeferred.reject(abortErr);
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    // Select SampleA and click Download -> error surfaces via role="alert".
    const rowA = screen.getByText('SampleA').closest('tr');
    fireEvent.click(within(rowA).getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Zip bundle failed');
    });
    expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
  });

  it('exposes descriptive aria-labels on row checkboxes, scope="col" on headers, and --color-error on alerts (WA-053/WA-027)', () => {
    const customResults = [
      ...RESULTS,
      { run_id: 'run-4', mode: 'vcf', sample_name: '', reference_name: 'REF1', database_id: 'db1' },
      { run_id: '', mode: 'vcf', sample_name: '', reference_name: 'REF1', database_id: 'db1' },
    ];
    render(
      <ResultsPage
        logic={makeLogic({
          sessionResults: customResults,
          statusError: 'Error loading results',
        })}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Error loading results');
    expect(alert).toHaveStyle({ color: 'var(--color-error)' });

    const headers = screen.getAllByRole('columnheader');
    expect(headers).toHaveLength(10);
    for (const th of headers) {
      expect(th).toHaveAttribute('scope', 'col');
    }

    expect(screen.getByRole('checkbox', { name: 'Select all results' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select result SampleA' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select result SampleB' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select result SampleC' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select result run-4' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select result 5' })).toBeInTheDocument();
  });

  it('renders comparisonData summary, sample_disambiguation_note InfoButton, heatmap busy state, and filter args', () => {
    const fetchComparisonData = vi.fn();
    const setNonSynonymousOnly = vi.fn();
    const setDbHitsOnly = vi.fn();

    const { rerender } = render(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 1]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
          isComparisonBusy: false,
          nonSynonymousOnly: true,
          dbHitsOnly: false,
          fetchComparisonData,
          setNonSynonymousOnly,
          setDbHitsOnly,
          comparisonData: {
            samples: ['SampleA', 'SampleB'],
            mutations: ['M1', 'M2', 'M3'],
            references: ['REF1'],
            sample_disambiguation_note: 'Duplicate sample names were disambiguated.',
          },
        })}
      />,
    );

    expect(screen.getByRole('button', { name: 'Sample naming info' })).toBeInTheDocument();
    expect(screen.getByText('2 samples × 3 mutations — Reference: REF1')).toBeInTheDocument();
    expect(screen.getByTestId('heatmap-stub')).toHaveAttribute('data-busy', 'false');

    const nonSynBox = screen.getByLabelText(/non-synonymous only/i);
    const dbHitsBox = screen.getByLabelText(/db hits only/i);
    expect(nonSynBox).toBeChecked();
    expect(dbHitsBox).not.toBeChecked();

    fireEvent.click(nonSynBox);
    expect(setNonSynonymousOnly).toHaveBeenCalledWith(false);
    expect(fetchComparisonData).toHaveBeenCalledWith(false);

    fireEvent.click(dbHitsBox);
    expect(setDbHitsOnly).toHaveBeenCalledWith(true);
    expect(fetchComparisonData).toHaveBeenCalledWith(undefined, true);

    // While isComparisonBusy is true, toggling filters updates state without calling fetchComparisonData,
    // and summary omits Reference when references is empty.
    fetchComparisonData.mockClear();
    rerender(
      <ResultsPage
        logic={makeLogic({
          selectedResultIndices: new Set([0, 1]),
          comparisonDbId: 'db1',
          comparisonRefName: 'REF1',
          isComparisonBusy: true,
          nonSynonymousOnly: false,
          dbHitsOnly: true,
          fetchComparisonData,
          setNonSynonymousOnly,
          setDbHitsOnly,
          comparisonData: {
            samples: ['SampleA', 'SampleB'],
            mutations: ['M1'],
            references: [],
          },
        })}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Sample naming info' })).not.toBeInTheDocument();
    expect(screen.getByText('2 samples × 1 mutations')).toBeInTheDocument();
    expect(screen.getByTestId('heatmap-stub')).toHaveAttribute('data-busy', 'true');
    fireEvent.click(screen.getByLabelText(/non-synonymous only/i));
    fireEvent.click(screen.getByLabelText(/db hits only/i));
    expect(setNonSynonymousOnly).toHaveBeenCalledWith(true);
    expect(setDbHitsOnly).toHaveBeenCalledWith(false);
    expect(fetchComparisonData).not.toHaveBeenCalled();
  });

  it('wires Select all comparable, Select all results uncheck, reverse chronological row order, artifact links, and missing-field fallbacks', () => {
    const selectAllComparable = vi.fn();
    const setSelectedResultIndices = vi.fn();
    const customResults = [
      ...RESULTS,
      { run_id: '', mode: '', sample_name: '', reference_name: '', database_id: '', created_at: '' },
    ];

    const { rerender } = render(
      <ResultsPage
        logic={makeLogic({
          sessionResults: customResults,
          selectedResultIndices: new Set(),
          selectAllComparable,
          setSelectedResultIndices,
        })}
      />,
    );

    const selectAllCompBtn = screen.getByRole('button', { name: 'Select all comparable' });
    expect(selectAllCompBtn).toBeDisabled();

    // Verify reverse chronological row order (index 3 first, index 0 last) and missing-field '—' fallbacks.
    const bodyRows = screen.getAllByRole('row').slice(1);
    expect(bodyRows).toHaveLength(4);
    expect(within(bodyRows[0]).getAllByText('—').length).toBeGreaterThanOrEqual(8);
    expect(bodyRows[1]).toHaveTextContent('SampleC');
    expect(bodyRows[3]).toHaveTextContent('SampleA');

    // View link on SampleA routes to buildReportUrl.
    expect(within(bodyRows[3]).getByRole('link', { name: 'View' })).toHaveAttribute(
      'href',
      '/api/report?artifact_id=a-html',
    );

    rerender(
      <ResultsPage
        logic={makeLogic({
          sessionResults: customResults,
          selectedResultIndices: new Set([0, 1, 2, 3]),
          selectAllComparable,
          setSelectedResultIndices,
        })}
      />,
    );

    const enabledCompBtn = screen.getByRole('button', { name: 'Select all comparable' });
    expect(enabledCompBtn).toBeEnabled();
    fireEvent.click(enabledCompBtn);
    expect(selectAllComparable).toHaveBeenCalledTimes(1);

    // Unchecking "Select all results" passes an empty Set.
    const selectAllCheckbox = screen.getByRole('checkbox', { name: 'Select all results' });
    expect(selectAllCheckbox).toBeChecked();
    fireEvent.click(selectAllCheckbox);
    expect(setSelectedResultIndices).toHaveBeenCalledWith(new Set());
  });
});
