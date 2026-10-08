import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { resetCompareState } from './compareSessionState';

// Mock the API layer so no network calls are made.
vi.mock('../../api', () => ({
  fetchSharedReferences: vi.fn(),
  fetchReferenceAccessions: vi.fn(),
  compareDatabases: vi.fn(),
}));

// Mock the Venn so the tab tests stay isolated from venn.js/d3. The counter
// tracks diagram (re)creations via an effect mirroring the real component's
// render effect dependencies [data, onRegionClick] — a new inline callback
// identity on re-render would re-run it (the original expand-jump bug).
let vennEffectCount = 0;
vi.mock('../ComparisonVenn', () => ({
  ComparisonVenn: ({ data, onRegionClick }) => {
    useEffect(() => {
      vennEffectCount += 1;
    }, [data, onRegionClick]);
    return <div data-testid="venn-stub">{data.databases.map((db) => db.name).join(', ')}</div>;
  },
}));

import { fetchSharedReferences, fetchReferenceAccessions, compareDatabases } from '../../api';
import { ComparePage } from './ComparePage';

const DATABASES = [
  { id: 'db1', display_name: 'Alpha' },
  { id: 'db2', display_name: 'Beta' },
  { id: 'db3', display_name: 'Gamma' },
  { id: 'db4', display_name: 'Delta' },
];

// db1 shares ACC1 with db2 and ACC2 with db3; db4 shares nothing.
const ACCESSIONS = {
  db1: { ACC1: 'Ref A', ACC2: 'Ref B' },
  db2: { ACC1: 'Ref A' },
  db3: { ACC2: 'Ref B' },
  db4: { ACC9: 'Ref Z' },
};

const SHARED_REFS = [
  {
    accession: 'ACC1',
    name: 'Ref A',
    organism: 'Monkeypox virus',
    present_in: ['db1', 'db2'],
  },
];

const COMPARE_RESPONSE = {
  reference: { accession: 'ACC1', name: 'Ref A' },
  databases: ['db1', 'db2'],
  metadata_fields: ['drug', 'phenotype', 'ic50', 'fold_ic50', 'publication'],
  venn: [
    { region: ['db1'], count: 1 },
    { region: ['db2'], count: 1 },
    { region: ['db1', 'db2'], count: 1 },
  ],
  rows: [
    {
      feature: 'UL23',
      position: 50,
      reference: 'A',
      mutation: 'T',
      drug: 'DrugA',
      region: ['db1', 'db2'],
      per_db: {
        db1: { drug: 'DrugA', phenotype: 'Resistant', ic50: 1.5, fold_ic50: 2, score: 0.9, publication: 'PMID:1' },
        db2: { drug: 'DrugA', phenotype: 'Intermediate', ic50: 3, fold_ic50: 4, score: 0.7, publication: 'PMID:2' },
      },
    },
    {
      feature: 'UL24',
      position: 0,
      reference: 'G',
      mutation: 'C',
      drug: 'DrugC',
      region: ['db1'],
      per_db: {
        db1: { drug: 'DrugC', phenotype: 'Low', ic50: 0.5, fold_ic50: 1, score: 0.5, publication: 'PMID:3' },
        db2: null,
      },
    },
  ],
  counts: { total_unique: 3, intersection: 1, per_db: { db1: 2, db2: 1 } },
};

function selectDatabases(ids) {
  ids.forEach((id, index) => {
    fireEvent.change(screen.getByLabelText(`Database ${index + 1}`), { target: { value: id } });
  });
}

function dropdownOptions(label) {
  return [...screen.getByLabelText(label).options].map((option) => option.value);
}

// Wait until the shared-reference select has auto-selected the single ref,
// which is the signal that the fetch effect has resolved and accession is set.
async function waitForReady() {
  await waitFor(() =>
    expect(screen.getByRole('combobox', { name: /shared reference/i })).toHaveValue('ACC1'),
  );
}

describe('ComparePage selectors', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({ items: ACCESSIONS, count: 4 });
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  it('renders three database dropdowns', () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    expect(screen.getByLabelText('Database 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Database 2')).toBeInTheDocument();
    expect(screen.getByLabelText('Database 3')).toBeInTheDocument();
  });

  it('loads reference accessions for all databases on mount', () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    expect(fetchReferenceAccessions).toHaveBeenCalledWith(['db1', 'db2', 'db3', 'db4']);
  });

  it('offers all databases in the first dropdown', () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    expect(dropdownOptions('Database 1')).toEqual(['', 'db1', 'db2', 'db3', 'db4']);
  });

  it('restricts the second dropdown to databases sharing a reference', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    fireEvent.change(screen.getByLabelText('Database 1'), { target: { value: 'db1' } });
    await waitFor(() => expect(dropdownOptions('Database 2')).toEqual(['', 'db2', 'db3']));
  });

  it('restricts the third dropdown to databases sharing references with both selections', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    // db3 shares ACC2 with db1 but nothing with db2 → excluded.
    await waitFor(() => expect(dropdownOptions('Database 3')).toEqual(['']));
  });

  it('clears downstream slots when an earlier selection changes', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.change(screen.getByLabelText('Database 1'), { target: { value: 'db4' } });
    expect(screen.getByLabelText('Database 2')).toHaveValue('');
    expect(screen.getByLabelText('Database 3')).toHaveValue('');
    // db4 shares no accession → second dropdown offers nothing comparable.
    expect(dropdownOptions('Database 2')).toEqual(['']);
  });

  it('resets the comparison result when the selection changes', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    expect(await screen.findByTestId('venn-stub')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Database 1'), { target: { value: 'db3' } });
    expect(screen.queryByTestId('venn-stub')).not.toBeInTheDocument();
  });

  it('fetches shared references when 2 databases are selected', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitFor(() => expect(fetchSharedReferences).toHaveBeenCalledWith(['db1', 'db2']));
    // Exactly one shared reference auto-selects.
    await waitForReady();
  });

  it('does not fetch shared references for a single selection', () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1']);
    expect(fetchSharedReferences).not.toHaveBeenCalled();
  });

  it('shows the reference as ID (Species) in the dropdown', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    const option = screen.getByRole('option', { name: 'ACC1 (Monkeypox virus)' });
    expect(option).toHaveTextContent('ACC1 (Monkeypox virus)');
    // No duplicate accession or reference name inside the brackets.
    expect(option).not.toHaveTextContent('ACC1 (ACC1');
    expect(option).not.toHaveTextContent('Ref A (');
  });
});

describe('ComparePage compare flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  it('calls the compare endpoint and stores the response', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    await waitFor(() => expect(compareDatabases).toHaveBeenCalledWith(['db1', 'db2'], 'ACC1'));
    // The mutation table renders the union rows.
    expect(await screen.findByText('UL23')).toBeInTheDocument();
    expect(screen.getByText('UL24')).toBeInTheDocument();
    expect(screen.getByTestId('venn-stub')).toHaveTextContent('Alpha, Beta');
    expect(screen.getByText(/Alpha:/)).toBeInTheDocument();
  });

  it('shows an error when the compare request fails', async () => {
    compareDatabases.mockRejectedValue(new Error('accession missing from one database'));
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/accession missing/i);
  });
});

describe('ComparePage table', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  async function renderCompared() {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    await screen.findAllByText('UL23');
  }

  it('shows all union rows by default', async () => {
    await renderCompared();
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.getByText('UL24')).toBeInTheDocument();
  });

  it('renders same-position rules for different drugs as separate rows', async () => {
    const secondDrugRow = {
      ...COMPARE_RESPONSE.rows[0],
      drug: 'DrugB',
      region: ['db1'],
      per_db: {
        db1: { ...COMPARE_RESPONSE.rows[0].per_db.db1, drug: 'DrugB' },
        db2: null,
      },
    };
    compareDatabases.mockResolvedValue({
      ...COMPARE_RESPONSE,
      rows: [COMPARE_RESPONSE.rows[0], secondDrugRow],
    });
    await renderCompared();
    const mutationRows = screen.getAllByText('UL23').map((cell) => cell.closest('tr'));
    expect(mutationRows).toHaveLength(2);
    expect(mutationRows.map((row) => row.cells[5].textContent)).toEqual(['DrugA', 'DrugB']);
  });

  it('expands a row to show per-database metadata with "—" for absent DBs', async () => {
    await renderCompared();
    // Expand the UL24 row (present only in db1).
    const ul24Row = screen.getByText('UL24').closest('tr');
    expect(ul24Row).toHaveTextContent('1');
    fireEvent.click(ul24Row.querySelector('.compare-expand-btn'));
    // db1 metadata is shown.
    expect(screen.getAllByText('DrugC').length).toBeGreaterThanOrEqual(2);
    // db2 lacks the key → "—" placeholder.
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);
  });

  it('shows populated metadata fields and omits fields empty across the selection', async () => {
    await renderCompared();
    const ul23Row = screen.getByText('UL23').closest('tr');
    fireEvent.click(ul23Row.querySelector('.compare-expand-btn'));
    expect(screen.getAllByText('Publication').length).toBeGreaterThan(0);
    expect(screen.queryByText('Score')).not.toBeInTheDocument();
    expect(screen.getByText('PMID:1')).toBeInTheDocument();
  });

  it('narrows visible rows with the region filter', async () => {
    await renderCompared();
    const filter = screen.getByRole('combobox', { name: /region/i });
    fireEvent.change(filter, { target: { value: 'db1' } });
    expect(screen.getByText('UL24')).toBeInTheDocument();
    expect(screen.queryByText('UL23')).not.toBeInTheDocument();
  });

  it('expanding a row does not re-render the Venn diagram', async () => {
    await renderCompared();
    vennEffectCount = 0;
    const ul24Row = screen.getByText('UL24').closest('tr');
    fireEvent.click(ul24Row.querySelector('.compare-expand-btn'));
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1);
    fireEvent.click(ul24Row.querySelector('.compare-expand-btn'));
    // Expanding/collapsing metadata must not recreate the diagram (view reset).
    expect(vennEffectCount).toBe(0);
  });

  it('exports TSV with one line per (mutation, database) pair', async () => {
    const blobs = [];
    const originalCreate = URL.createObjectURL;
    URL.createObjectURL = (blob) => {
      blobs.push(blob);
      return 'blob:mock';
    };
    try {
      await renderCompared();
      fireEvent.click(screen.getByRole('button', { name: /export tsv/i }));
      expect(blobs).toHaveLength(1);
      const text = await blobs[0].text();
      const lines = text.split('\n').filter(Boolean);
      // Header + 4 (mutation, database) pairs: both mutations across both DBs.
      expect(lines).toHaveLength(5);
      expect(lines[0]).toBe('feature\tref\tpos\tmut\tdatabase\tDrug\tPhenotype\tIC50\tFold-IC50\tPublication');
      expect(lines[1].startsWith('UL23\tA\t51\tT\tAlpha\t')).toBe(true);
      expect(lines.some((line) => line.startsWith('UL23\tA\t51\tT\tBeta\t'))).toBe(true);
      expect(lines.some((line) => line.startsWith('UL24\tG\t1\tC\tAlpha\t'))).toBe(true);
      expect(lines).toContain('UL24\tG\t1\tC\tBeta\t\t\t\t\t');
    } finally {
      URL.createObjectURL = originalCreate;
    }
  });
});

describe('ComparePage auto-select bug regression', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({ items: ACCESSIONS, count: 4 });
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  it('Compare button is enabled immediately after auto-select of a single shared reference', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    // Auto-select fires when the shared-references fetch resolves.
    await waitForReady();
    const compareBtn = screen.getByRole('button', { name: /compare/i });
    expect(compareBtn).toBeEnabled();
    fireEvent.click(compareBtn);
    await waitFor(() => expect(compareDatabases).toHaveBeenCalledWith(['db1', 'db2'], 'ACC1'));
  });

  it('auto-selected accession survives without further interaction', async () => {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    // No additional change events; the preloaded reference must be usable.
    expect(screen.getByRole('combobox', { name: /shared reference/i })).toHaveValue('ACC1');
    expect(screen.getByRole('button', { name: /compare/i })).toBeEnabled();
  });

  it('pre-selects the first shared reference when several are shared', async () => {
    fetchSharedReferences.mockResolvedValue({
      items: [
        SHARED_REFS[0],
        { accession: 'ACC2', name: 'Ref B', present_in: ['db1', 'db2'] },
      ],
      count: 2,
    });
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    // The select displays the first reference; state must match so Compare works.
    const refSelect = await screen.findByRole('combobox', { name: /shared reference/i });
    await waitFor(() => expect(refSelect).toHaveValue('ACC1'));
    const compareBtn = screen.getByRole('button', { name: /compare/i });
    expect(compareBtn).toBeEnabled();
    fireEvent.click(compareBtn);
    await waitFor(() => expect(compareDatabases).toHaveBeenCalledWith(['db1', 'db2'], 'ACC1'));
  });
});

describe('ComparePage table search', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({ items: ACCESSIONS, count: 4 });
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  async function renderCompared() {
    render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    await screen.findAllByText('UL23');
  }

  it('renders a search input in the mutations table toolbar', async () => {
    await renderCompared();
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
  });

  it('subsets main rows by the search term', async () => {
    await renderCompared();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'UL23' } });
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.queryByText('UL24')).not.toBeInTheDocument();
  });

  it('matches case-insensitively across all main-row columns', async () => {
    await renderCompared();
    // Feature column, case-insensitive.
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'ul24' } });
    expect(screen.getByText('UL24')).toBeInTheDocument();
    expect(screen.queryByText('UL23')).not.toBeInTheDocument();
    // Mutation column.
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'T' } });
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.queryByText('UL24')).not.toBeInTheDocument();
    // Drug column.
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'drugc' } });
    expect(screen.getByText('UL24')).toBeInTheDocument();
    expect(screen.queryByText('UL23')).not.toBeInTheDocument();
  });

  it('searches the displayed (1-based) position', async () => {
    await renderCompared();
    // UL23 is at internal position 50 → displayed 51.
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: '51' } });
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.queryByText('UL24')).not.toBeInTheDocument();
  });

  it('shows an empty state when the term matches no row', async () => {
    await renderCompared();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'nomatch' } });
    expect(screen.getByText(/no mutations/i)).toBeInTheDocument();
  });

  it('exports TSV respecting the search filter', async () => {
    const blobs = [];
    const originalCreate = URL.createObjectURL;
    URL.createObjectURL = (blob) => {
      blobs.push(blob);
      return 'blob:mock';
    };
    try {
      await renderCompared();
      fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'UL24' } });
      fireEvent.click(screen.getByRole('button', { name: /export tsv/i }));
      const text = await blobs[0].text();
      const lines = text.split('\n').filter(Boolean);
      // Header + UL24 pair for both databases.
      expect(lines).toHaveLength(3);
      expect(lines[1].startsWith('UL24\tG\t1\tC\tAlpha\t')).toBe(true);
      expect(lines[2]).toBe('UL24\tG\t1\tC\tBeta\t\t\t\t\t');
    } finally {
      URL.createObjectURL = originalCreate;
    }
  });

  it('clearing the search restores all rows', async () => {
    await renderCompared();
    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'UL23' } });
    fireEvent.change(searchInput, { target: { value: '' } });
    expect(screen.getByText('UL23')).toBeInTheDocument();
    expect(screen.getByText('UL24')).toBeInTheDocument();
  });

  it('does not search inside expanded metadata rows', async () => {
    await renderCompared();
    // Expand UL23 (metadata contains PMID:1, Resistant, etc.).
    const ul23Row = screen.getByText('UL23').closest('tr');
    fireEvent.click(ul23Row.querySelector('.compare-expand-btn'));
    // Searching for metadata-only content must not match the expanded row.
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'Resistant' } });
    expect(screen.queryByText('UL23')).not.toBeInTheDocument();
  });

  it('persists the search term across remounts with the result', async () => {
    const { unmount } = render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    await screen.findByTestId('venn-stub');
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'UL24' } });
    unmount();

    render(<ComparePage logic={{ databases: DATABASES }} />);
    expect(screen.getByPlaceholderText(/search/i)).toHaveValue('UL24');
    expect(screen.getByText('UL24')).toBeInTheDocument();
    expect(screen.queryByText('UL23')).not.toBeInTheDocument();
  });
});

describe('ComparePage session persistence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({ items: ACCESSIONS, count: 4 });
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESPONSE);
  });

  it('restores the selection and result when the tab remounts', async () => {
    const { unmount } = render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    fireEvent.click(screen.getByRole('button', { name: /compare/i }));
    await screen.findByTestId('venn-stub');
    unmount();

    render(<ComparePage logic={{ databases: DATABASES }} />);
    // Selection, reference and result are restored without re-fetch/re-run.
    expect(screen.getByLabelText('Database 1')).toHaveValue('db1');
    expect(screen.getByLabelText('Database 2')).toHaveValue('db2');
    expect(screen.getByRole('combobox', { name: /shared reference/i })).toHaveValue('ACC1');
    expect(screen.getByTestId('venn-stub')).toBeInTheDocument();
    expect(screen.getByText('UL23')).toBeInTheDocument();
  });

  it('does not refetch shared references on remount when state was persisted', async () => {
    const { unmount } = render(<ComparePage logic={{ databases: DATABASES }} />);
    selectDatabases(['db1', 'db2']);
    await waitForReady();
    unmount();

    fetchSharedReferences.mockClear();
    render(<ComparePage logic={{ databases: DATABASES }} />);
    expect(screen.getByLabelText('Database 1')).toHaveValue('db1');
    expect(fetchSharedReferences).not.toHaveBeenCalled();
  });
});
