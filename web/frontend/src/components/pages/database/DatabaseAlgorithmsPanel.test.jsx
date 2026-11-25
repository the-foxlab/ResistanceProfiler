import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { DatabaseAlgorithmsPanel } from './DatabaseAlgorithmsPanel';

describe('DatabaseAlgorithmsPanel', () => {
  afterEach(() => {
    cleanup();
  });

  it('returns null when algorithms is null or contains neither section', () => {
    const { container, rerender } = render(<DatabaseAlgorithmsPanel algorithms={null} />);
    expect(container.firstChild).toBeNull();

    rerender(<DatabaseAlgorithmsPanel algorithms={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders effect_as_resistant-only algorithms sorted and grouped cleanly', () => {
    render(
      <DatabaseAlgorithmsPanel
        algorithms={{
          effect_as_resistant: {
            rules: [
              { feature: 'UL54', reference: 'ref1', drug: 'Foscarnet', effect: ['stop_gained'] },
              { feature: 'UL23', reference: 'ref1', drug: 'Aciclovir', effect: ['frameshift'] },
              { feature: 'UL23', reference: 'ref1', drug: 'Ganciclovir', effect: ['stop_lost'] },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText('effect_as_resistant')).toBeInTheDocument();
    expect(screen.queryByText('drug_interpretation')).not.toBeInTheDocument();
    expect(screen.getByText('frameshift, stop_lost')).toBeInTheDocument();
    expect(screen.getByText('Aciclovir, Ganciclovir')).toBeInTheDocument();
  });

  it('falls back to "Not configured" when drug_interpretation entry omits method', () => {
    render(
      <DatabaseAlgorithmsPanel
        algorithms={{
          drug_interpretation: [
            {
              method: '',
              thresholds: { resistant: 50 },
              drug_thresholds: [
                { reference: 'ref1', drug: 'DrugA', thresholds: { resistant: 40 } },
              ],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText('drug_interpretation overrides (0)')).toBeInTheDocument();
  });
});
