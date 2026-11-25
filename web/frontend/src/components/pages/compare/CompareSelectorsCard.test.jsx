import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CompareSelectorsCard } from './CompareSelectorsCard';

const DATABASES = [
  { id: 'db1', display_name: 'Alpha' },
  { id: 'db2', display_name: 'Beta' },
  { id: 'db3', display_name: 'Gamma' },
  { id: 'db4', display_name: 'Delta' },
];

function makeProps(overrides = {}) {
  return {
    slots: ['', '', ''],
    selectedIds: [],
    optionsForSlot: (index) => (index === 0 ? DATABASES : []),
    handleSlotChange: vi.fn(),
    sharedRefs: [],
    sharedRefsLoading: false,
    accession: '',
    setAccession: vi.fn(),
    canCompare: false,
    isBusy: false,
    runCompare: vi.fn(),
    error: '',
    ...overrides,
  };
}

describe('CompareSelectorsCard', () => {
  it('disables slots 2 and 3 without showing "No comparable databases" when slots are empty', () => {
    render(<CompareSelectorsCard {...makeProps()} />);

    expect(screen.getByLabelText('Database 1')).toBeEnabled();
    expect(screen.getByLabelText('Database 2')).toBeDisabled();
    expect(screen.getByLabelText('Database 3')).toBeDisabled();
    expect(screen.queryByText('No comparable databases')).not.toBeInTheDocument();
  });

  it('disables slot 2 with hint and disables slot 3 without hint when slot 1 has no comparable DBs', () => {
    render(
      <CompareSelectorsCard
        {...makeProps({
          slots: ['db4', '', ''],
          selectedIds: ['db4'],
          optionsForSlot: (index) => (index === 0 ? DATABASES : []),
        })}
      />,
    );

    expect(screen.getByLabelText('Database 1')).toBeEnabled();
    expect(screen.getByLabelText('Database 2')).toBeDisabled();
    expect(screen.getByLabelText('Database 3')).toBeDisabled();
    expect(screen.getAllByText('No comparable databases')).toHaveLength(1);
  });

  it('disables slot 3 with hint when slots 1 and 2 have no comparable 3rd DB', () => {
    render(
      <CompareSelectorsCard
        {...makeProps({
          slots: ['db1', 'db2', ''],
          selectedIds: ['db1', 'db2'],
          optionsForSlot: (index) => {
            if (index === 0) return DATABASES;
            if (index === 1) return [DATABASES[1], DATABASES[2]];
            return [];
          },
        })}
      />,
    );

    expect(screen.getByLabelText('Database 1')).toBeEnabled();
    expect(screen.getByLabelText('Database 2')).toBeEnabled();
    expect(screen.getByLabelText('Database 3')).toBeDisabled();
    expect(screen.getAllByText('No comparable databases')).toHaveLength(1);
  });

  it('disables slots 2 and 3 without hints when databases list is empty', () => {
    render(
      <CompareSelectorsCard
        {...makeProps({
          optionsForSlot: () => [],
        })}
      />,
    );

    expect(screen.getByLabelText('Database 1')).toBeEnabled();
    expect(screen.getByLabelText('Database 2')).toBeDisabled();
    expect(screen.getByLabelText('Database 3')).toBeDisabled();
    expect(screen.queryByText('No comparable databases')).not.toBeInTheDocument();
  });

  it('disables shared reference select while loading and disables Compare button until canCompare', () => {
    const runCompare = vi.fn();
    const { rerender } = render(
      <CompareSelectorsCard
        {...makeProps({
          slots: ['db1', 'db2', ''],
          selectedIds: ['db1', 'db2'],
          sharedRefsLoading: true,
          sharedRefs: [{ accession: 'ACC1', organism: 'MPXV' }],
          accession: 'ACC1',
          canCompare: false,
          runCompare,
        })}
      />,
    );

    expect(screen.getByLabelText('Shared reference')).toBeDisabled();
    expect(screen.getByRole('button', { name: /compare/i })).toBeDisabled();

    rerender(
      <CompareSelectorsCard
        {...makeProps({
          slots: ['db1', 'db2', ''],
          selectedIds: ['db1', 'db2'],
          sharedRefsLoading: false,
          sharedRefs: [{ accession: 'ACC1', organism: 'MPXV' }],
          accession: 'ACC1',
          canCompare: true,
          runCompare,
        })}
      />,
    );

    expect(screen.getByLabelText('Shared reference')).toBeEnabled();
    const compareButton = screen.getByRole('button', { name: /compare/i });
    expect(compareButton).toBeEnabled();
    fireEvent.click(compareButton);
    expect(runCompare).toHaveBeenCalledTimes(1);
  });

  it('renders error with role="alert"', () => {
    render(<CompareSelectorsCard {...makeProps({ error: 'Compare failed' })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Compare failed');
  });
});
