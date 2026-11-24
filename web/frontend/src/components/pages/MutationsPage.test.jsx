import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { MutationsPage } from './MutationsPage';

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
    setMutationFilterColumn: vi.fn(),
    mutationSortColumn: 0,
    setMutationSortColumn: vi.fn(),
    mutationSortAsc: true,
    setMutationSortAsc: vi.fn(),
    formulaFilter: '',
    setFormulaFilter: vi.fn(),
    setFormulaFilterColumn: vi.fn(),
    mutationsLoaded: true,
    downloadMutationsAsTsv: vi.fn(),
    downloadFormulaRulesAsTsv: vi.fn(),
    ...overrides,
  };
}

describe('MutationsPage', () => {
  afterEach(() => {
    cleanup();
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
    expect(logic.setMutationFilterColumn).toHaveBeenCalledWith('-1');
    fireEvent.click(resetFormula);
    expect(logic.setFormulaFilter).toHaveBeenCalledWith('');
    expect(logic.setFormulaFilterColumn).toHaveBeenCalledWith('-1');

    const [dlSingle, dlFormula] = screen.getAllByRole('button', { name: 'Download as TSV' });
    fireEvent.click(dlSingle);
    expect(logic.downloadMutationsAsTsv).toHaveBeenCalledTimes(1);
    fireEvent.click(dlFormula);
    expect(logic.downloadFormulaRulesAsTsv).toHaveBeenCalledTimes(1);
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
});
