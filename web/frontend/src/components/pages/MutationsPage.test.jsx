import { useEffect, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { MutationsPage } from './MutationsPage';
import { useMutationBrowser } from '../../hooks/useMutationBrowser';

function makeLogic(overrides = {}) {
  const columns = [
    { key: 'feature', label: 'Feature', accessor: (r) => r.feature },
    { key: 'comment', label: 'Comment', accessor: (r) => r.comment || '' },
  ];
  return {
    rules: [{ id: 1, feature: 'UL23', comment: 'Note A' }],
    formulaRules: [{ formula_id: 10, feature: 'UL30', comment: 'Combo note' }],
    mutationColumns: columns,
    formulaColumns: columns,
    displayedRules: [{ id: 1, feature: 'UL23', comment: 'Note A' }],
    displayedFormulaRules: [{ formula_id: 10, feature: 'UL30', comment: 'Combo note' }],
    mutationFilter: '',
    setMutationFilter: vi.fn(),
    mutationSortColumn: 0,
    setMutationSortColumn: vi.fn(),
    mutationSortAsc: true,
    setMutationSortAsc: vi.fn(),
    formulaFilter: '',
    setFormulaFilter: vi.fn(),
    mutationsLoaded: true,
    isMutationsLoading: false,
    loadedDatabaseId: 'db1',
    statusError: '',
    downloadMutationsAsTsv: vi.fn(),
    downloadFormulaRulesAsTsv: vi.fn(),
    ...overrides,
  };
}

describe('MutationsPage', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders both single and combinatorial rule tables with search, sort, reset, and TSV export', () => {
    const logic = makeLogic();
    render(<MutationsPage logic={logic} />);

    expect(screen.getByText('Single mutations')).toBeInTheDocument();
    expect(screen.getByText('Combinatorial mutations')).toBeInTheDocument();
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.getByText('UL30')).toBeInTheDocument();
    expect(screen.getByText('Note A').closest('td')).toHaveAttribute('title', 'Note A');

    // Sorting single mutations toggles sort direction on active column, selects new column otherwise.
    const [singleFeatureTh, singleCommentTh] = screen.getAllByText(/Feature|Comment/);
    fireEvent.click(singleFeatureTh);
    expect(logic.setMutationSortAsc).toHaveBeenCalledWith(false);
    fireEvent.click(singleCommentTh);
    expect(logic.setMutationSortColumn).toHaveBeenCalledWith(1);
    expect(logic.setMutationSortAsc).toHaveBeenCalledWith(true);

    // Search + reset + download.
    fireEvent.change(document.getElementById('mutation-rules-search'), { target: { value: 'UL' } });
    expect(logic.setMutationFilter).toHaveBeenCalledWith('UL');
    const [resetSingle, resetFormula] = screen.getAllByRole('button', { name: 'Reset filter' });
    fireEvent.click(resetSingle);
    expect(logic.setMutationFilter).toHaveBeenCalledWith('');
    fireEvent.click(resetFormula);
    expect(logic.setFormulaFilter).toHaveBeenCalledWith('');

    const [dlSingle, dlFormula] = screen.getAllByRole('button', { name: 'Download as TSV' });
    expect(dlSingle).toBeEnabled();
    expect(dlFormula).toBeEnabled();
    fireEvent.click(dlSingle);
    expect(logic.downloadMutationsAsTsv).toHaveBeenCalledTimes(1);
    fireEvent.click(dlFormula);
    expect(logic.downloadFormulaRulesAsTsv).toHaveBeenCalledTimes(1);
  });

  it('disables TSV export buttons and hides empty-state copy while mutationsLoaded is false', () => {
    const logic = makeLogic({
      mutationsLoaded: false,
      displayedRules: [],
      displayedFormulaRules: [],
    });
    const { rerender } = render(<MutationsPage logic={logic} />);

    const [dlSingle, dlFormula] = screen.getAllByRole('button', { name: 'Download as TSV' });
    expect(dlSingle).toBeDisabled();
    expect(dlFormula).toBeDisabled();
    fireEvent.click(dlSingle);
    fireEvent.click(dlFormula);
    expect(logic.downloadMutationsAsTsv).not.toHaveBeenCalled();
    expect(logic.downloadFormulaRulesAsTsv).not.toHaveBeenCalled();
    expect(screen.queryByText(/no mutations match/i)).not.toBeInTheDocument();

    rerender(
      <MutationsPage
        logic={makeLogic({
          mutationsLoaded: false,
          rules: [],
          formulaRules: [],
          displayedRules: [],
          displayedFormulaRules: [],
        })}
      />,
    );
    expect(screen.queryByText(/no mutation rules were found/i)).not.toBeInTheDocument();
  });

  it('applies mutations-single-table class when only one table has rules and shows empty state when none', () => {
    const { container, rerender } = render(
      <MutationsPage logic={makeLogic({ formulaRules: [], displayedFormulaRules: [] })} />,
    );
    expect(container.querySelector('article')).toHaveClass('mutations-single-table');

    rerender(
      <MutationsPage
        logic={makeLogic({
          rules: [],
          formulaRules: [],
          displayedRules: [],
          displayedFormulaRules: [],
        })}
      />,
    );
    expect(screen.getByText(/no mutation rules were found/i)).toBeInTheDocument();
  });

  it('renders dark-mode-safe CSS mask icons instead of raw img tags and exposes accessible searchbox labels (WA-027/S5-SVG/WA-053)', () => {
    const { container } = render(<MutationsPage logic={makeLogic()} />);

    expect(container.querySelectorAll('img')).toHaveLength(0);
    const searchIcons = container.querySelectorAll('.mutation-search-icon');
    const resetIcons = container.querySelectorAll('.mutation-reset-icon');
    expect(searchIcons).toHaveLength(2);
    expect(resetIcons).toHaveLength(2);
    for (const icon of [...searchIcons, ...resetIcons]) {
      expect(icon.tagName).toBe('SPAN');
      expect(icon).toHaveAttribute('aria-hidden', 'true');
    }

    const singleSearch = screen.getByRole('searchbox', { name: 'Search single mutations' });
    const formulaSearch = screen.getByRole('searchbox', { name: 'Search combinatorial mutations' });
    expect(singleSearch).toHaveAttribute('id', 'mutation-rules-search');
    expect(formulaSearch).toHaveAttribute('id', 'formula-rules-search');
  });

  it('renders keyboard-accessible sort buttons with aria-sort on single mutations and plain non-sortable headers on combinatorial mutations (WA-053)', () => {
    const logic = makeLogic({ mutationSortColumn: 0, mutationSortAsc: true });
    const { rerender } = render(<MutationsPage logic={logic} />);

    const tables = screen.getAllByRole('table');
    expect(tables).toHaveLength(2);

    // Single mutations table: sortable button inside <th scope="col" aria-sort="...">
    const singleTable = tables[0];
    const singleHeaders = within(singleTable).getAllByRole('columnheader');
    expect(singleHeaders[0]).toHaveAttribute('scope', 'col');
    expect(singleHeaders[0]).toHaveAttribute('aria-sort', 'ascending');
    expect(singleHeaders[1]).toHaveAttribute('scope', 'col');
    expect(singleHeaders[1]).toHaveAttribute('aria-sort', 'none');

    const featureBtn = within(singleHeaders[0]).getByRole('button', { name: 'Feature' });
    const commentBtn = within(singleHeaders[1]).getByRole('button', { name: 'Comment' });
    const indicator = singleHeaders[0].querySelector('.mutation-sort-indicator');
    expect(indicator).toHaveAttribute('aria-hidden', 'true');
    expect(indicator).toHaveTextContent('▲');

    fireEvent.click(featureBtn);
    expect(logic.setMutationSortAsc).toHaveBeenCalledWith(false);
    fireEvent.click(commentBtn);
    expect(logic.setMutationSortColumn).toHaveBeenCalledWith(1);
    expect(logic.setMutationSortAsc).toHaveBeenCalledWith(true);

    // Descending indicator updates aria-sort
    rerender(
      <MutationsPage
        logic={makeLogic({ mutationSortColumn: 1, mutationSortAsc: false })}
      />,
    );
    const updatedSingleHeaders = within(screen.getAllByRole('table')[0]).getAllByRole('columnheader');
    expect(updatedSingleHeaders[0]).toHaveAttribute('aria-sort', 'none');
    expect(updatedSingleHeaders[1]).toHaveAttribute('aria-sort', 'descending');
    expect(updatedSingleHeaders[1].querySelector('.mutation-sort-indicator')).toHaveTextContent('▼');

    // Combinatorial mutations table: non-sortable <th scope="col">, no <button> or aria-sort
    const comboTable = screen.getAllByRole('table')[1];
    const comboHeaders = within(comboTable).getAllByRole('columnheader');
    for (const th of comboHeaders) {
      expect(th).toHaveAttribute('scope', 'col');
      expect(th).not.toHaveAttribute('aria-sort');
      expect(within(th).queryByRole('button')).not.toBeInTheDocument();
    }
  });

  it('announces statusError with role="alert" and --color-error (WA-053/WA-027)', () => {
    const { rerender } = render(<MutationsPage logic={makeLogic({ statusError: '' })} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    rerender(<MutationsPage logic={makeLogic({ statusError: 'Database load failed' })} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Database load failed');
    expect(alert).toHaveStyle({ color: 'var(--color-error)' });
  });
});

function MutationsBrowserHarness({ databaseId = 'db1', setStatusError = vi.fn() }) {
  const logic = useMutationBrowser({ setStatusError });
  useEffect(() => {
    logic.loadMutations(databaseId);
  }, [databaseId, logic.loadMutations]);
  return <MutationsPage logic={logic} />;
}

describe('MutationsPage + useMutationBrowser integration', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('resets single and combinatorial mutation filters without throwing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({
        data: {
          items: [
            { id: 1, feature: 'UL23', position: 0, mutation: 'M1V', drug: 'ACV' },
            { id: 2, feature: 'UL30', position: 5, mutation: 'A6T', drug: 'FOS' },
          ],
          formula_items: [
            { formula_id: 10, label: 'Combo1', drug: 'ACV' },
            { formula_id: 11, label: 'Combo2', drug: 'GCV' },
          ],
          plot_meta: { references: [], features: [] },
        },
      }),
    });

    render(<MutationsBrowserHarness />);

    await waitFor(() => {
      expect(screen.getByText('UL23')).toBeInTheDocument();
      expect(screen.getByText('Combo1')).toBeInTheDocument();
    });
    expect(screen.getAllByText('2 visible row(s)')).toHaveLength(2);

    const singleSearch = document.getElementById('mutation-rules-search');
    const formulaSearch = document.getElementById('formula-rules-search');
    const [resetSingle, resetFormula] = screen.getAllByRole('button', { name: 'Reset filter' });

    fireEvent.change(singleSearch, { target: { value: 'UL23' } });
    expect(screen.queryByText('UL30')).not.toBeInTheDocument();
    expect(screen.getByText('1 visible row(s)')).toBeInTheDocument();

    fireEvent.click(resetSingle);
    expect(singleSearch.value).toBe('');
    expect(screen.getByText('UL30')).toBeInTheDocument();
    expect(screen.getAllByText('2 visible row(s)')).toHaveLength(2);

    fireEvent.change(formulaSearch, { target: { value: 'Combo2' } });
    expect(screen.queryByText('Combo1')).not.toBeInTheDocument();
    expect(screen.getByText('1 visible row(s)')).toBeInTheDocument();

    fireEvent.click(resetFormula);
    expect(formulaSearch.value).toBe('');
    expect(screen.getByText('Combo1')).toBeInTheDocument();
    expect(screen.getAllByText('2 visible row(s)')).toHaveLength(2);
  });

  it('clears DB A rows and reports statusError when switching to a failing DB B', async () => {
    const setStatusError = vi.fn();
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          data: {
            items: [{ id: 1, feature: 'UL23_DBA', position: 0, mutation: 'M1V', drug: 'ACV' }],
            formula_items: [{ formula_id: 10, label: 'Combo_DBA', drug: 'ACV' }],
            plot_meta: { references: [], features: [] },
          },
        }),
      })
      .mockRejectedValueOnce(new Error('DB B unavailable'));

    const { rerender } = render(
      <MutationsBrowserHarness databaseId="dbA" setStatusError={setStatusError} />,
    );

    await waitFor(() => {
      expect(screen.getByText('UL23_DBA')).toBeInTheDocument();
      expect(screen.getByText('Combo_DBA')).toBeInTheDocument();
    });

    rerender(<MutationsBrowserHarness databaseId="dbB" setStatusError={setStatusError} />);

    await waitFor(() => {
      expect(setStatusError).toHaveBeenCalledWith('Error loading mutations: DB B unavailable');
    });
    expect(screen.queryByText('UL23_DBA')).not.toBeInTheDocument();
    expect(screen.queryByText('Combo_DBA')).not.toBeInTheDocument();
    expect(screen.queryByText(/no mutation rules were found/i)).not.toBeInTheDocument();
  });

  it('renders DB B rows rather than stale DB A rows when completions arrive out of order', async () => {
    let resolveA;
    let resolveB;
    const promiseA = new Promise((res) => { resolveA = res; });
    const promiseB = new Promise((res) => { resolveB = res; });

    vi.spyOn(globalThis, 'fetch').mockImplementation((url) => (
      String(url).includes('database_id=dbA') ? promiseA : promiseB
    ));

    const { rerender } = render(<MutationsBrowserHarness databaseId="dbA" />);
    rerender(<MutationsBrowserHarness databaseId="dbB" />);

    resolveB({
      ok: true,
      json: async () => ({
        data: {
          items: [{ id: 2, feature: 'UL30_DBB', position: 1, mutation: 'A2T', drug: 'FOS' }],
          formula_items: [],
          plot_meta: { references: [], features: [] },
        },
      }),
    });

    await waitFor(() => {
      expect(screen.getByText('UL30_DBB')).toBeInTheDocument();
    });

    resolveA({
      ok: true,
      json: async () => ({
        data: {
          items: [{ id: 1, feature: 'UL23_STALE_DBA', position: 0, mutation: 'M1V', drug: 'ACV' }],
          formula_items: [],
          plot_meta: { references: [], features: [] },
        },
      }),
    });

    await waitFor(() => {
      expect(screen.getByText('UL30_DBB')).toBeInTheDocument();
    });
    expect(screen.queryByText('UL23_STALE_DBA')).not.toBeInTheDocument();
  });

  it('surfaces real hook statusError as a role="alert" banner when /api/mutations fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Mutations endpoint offline'));

    function RealStatusHarness() {
      const [statusError, setStatusError] = useState('');
      const logic = useMutationBrowser({ setStatusError });
      useEffect(() => {
        logic.loadMutations('db1');
      }, [logic.loadMutations]);
      return <MutationsPage logic={{ ...logic, statusError }} />;
    }

    render(<RealStatusHarness />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Error loading mutations: Mutations endpoint offline');
    expect(alert).toHaveStyle({ color: 'var(--color-error)' });
  });
});
