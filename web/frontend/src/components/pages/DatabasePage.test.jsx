import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { DatabasePage } from './DatabasePage';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
    Plots: { resize: vi.fn() },
  },
}));

const sampleDatabase = {
  id: 'db1',
  display_name: 'Herpes DRG',
  uuid: 'uuid-123',
  created_at: '2026-01-01',
  schema_version: '4',
  metadata: {
    maintainers: 'Fuchs Lab',
    contact: 'lab@example.org',
    publication_pmid: '12345, 67890',
    publication_doi: 'https://doi.org/10.1000/abc',
    website: 'https://example.org',
    description: 'Curated resistance database',
    license: 'MIT',
  },
  algorithms: {
    drug_interpretation: [
      {
        method: 'score',
        thresholds: { resistant: 60, intermediate: 30 },
        drug_thresholds: [
          { reference: 'ref1', drug: 'DrugA', thresholds: { resistant: 50, intermediate: 20 } },
        ],
      },
    ],
    effect_as_resistant: {
      rules: [
        { feature: 'UL23', reference: 'ref1', drug: 'DrugA', effect: ['frameshift', 'stop_gained'] },
      ],
    },
  },
};

const sampleRules = [
  { reference_name: 'ref1', feature: 'UL23', position: 0, mutation: 'M1V', drug: 'DrugA', phenotype: 'resistant', clinical_phenotype: 'susceptible', score: '30' },
  { reference_name: 'ref1', feature: 'UL23', position: 15, mutation: 'A16T', drug: 'DrugA', phenotype: 'sensitive', clinical_phenotype: 'resistant', score: '60' },
];

const samplePlotMeta = {
  references: [{ reference_name: 'ref1', reference_display_name: 'Reference 1', reference_organism: 'HSV-1' }],
  features: [{ reference_name: 'ref1', feature_name: 'UL23', aa_length: 100 }],
};

describe('DatabasePage', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders empty state when no active database is loaded', () => {
    render(<DatabasePage logic={{ rules: [], formulaRules: [], mutationPlotMeta: null, selectedDatabase: null }} />);
    expect(screen.getByText(/no active database loaded/i)).toBeInTheDocument();
  });

  it('renders metadata identity, formatted links, algorithms tables, and plot controls', () => {
    render(
      <DatabasePage
        logic={{
          rules: sampleRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: sampleDatabase,
        }}
      />,
    );

    expect(screen.getByText('Herpes DRG')).toBeInTheDocument();
    expect(screen.getByText('v4')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'PMID:12345' })).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/12345/');
    expect(screen.getByRole('link', { name: 'PMID:67890' })).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/67890/');
    expect(screen.getByRole('link', { name: 'https://doi.org/10.1000/abc' })).toHaveAttribute('href', 'https://doi.org/10.1000/abc');
    expect(screen.getByRole('link', { name: 'lab@example.org' })).toHaveAttribute('href', 'mailto:lab@example.org');

    expect(screen.getByText('Supplied Interpretation Algorithms')).toBeInTheDocument();
    expect(screen.getByText('drug_interpretation')).toBeInTheDocument();
    expect(screen.getByText('drug_interpretation overrides (score)')).toBeInTheDocument();
    expect(screen.getByText('effect_as_resistant')).toBeInTheDocument();
    expect(screen.getByText('frameshift, stop_gained')).toBeInTheDocument();

    // Both phenotype and clinical_phenotype exist → mode toggle is shown.
    const clinicalBtn = screen.getByRole('button', { name: 'Clinical phenotype' });
    fireEvent.click(clinicalBtn);
    expect(clinicalBtn).toHaveClass('active');

    const binInput = screen.getByLabelText(/amino-acid bin size/i).querySelector('input')
      || screen.getByRole('spinbutton');
    fireEvent.change(binInput, { target: { value: '20' } });
    expect(binInput).toHaveValue(20);
  });

  it('shows empty plot notice when active database has no plot-friendly data', () => {
    render(
      <DatabasePage
        logic={{
          rules: [],
          formulaRules: [],
          mutationPlotMeta: { references: [], features: [] },
          selectedDatabase: sampleDatabase,
        }}
      />,
    );
    expect(screen.getByText('No plot-friendly data is available for the active database.')).toBeInTheDocument();
  });

  it('auto-selects phenotype mode for phenotype-only, clinical-only, neither, and dual-to-single DB switch', () => {
    const phenoOnlyRules = [
      { reference_name: 'ref1', feature: 'UL23', position: 0, mutation: 'M1V', drug: 'DrugA', phenotype: 'resistant' },
    ];
    const clinicalOnlyRules = [
      { reference_name: 'ref1', feature: 'UL23', position: 0, mutation: 'M1V', drug: 'DrugA', clinical_phenotype: 'resistant' },
    ];
    const noPhenoRules = [
      { reference_name: 'ref1', feature: 'UL23', position: 0, mutation: 'M1V', drug: 'DrugA', ic50: 1.5, score: '30' },
    ];

    const { rerender } = render(
      <DatabasePage
        logic={{
          rules: phenoOnlyRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: sampleDatabase,
        }}
      />,
    );
    // Phenotype-only: switch buttons are not rendered (only 1 mode).
    expect(screen.queryByRole('group', { name: 'Position annotation mode' })).not.toBeInTheDocument();
    expect(document.querySelector('.database-summary-pie-card')).toBeInTheDocument();

    // Switch to dual-mode DB -> clinical mode selected -> switch to phenotype-only DB.
    rerender(
      <DatabasePage
        logic={{
          rules: sampleRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: sampleDatabase,
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Clinical phenotype' }));
    expect(screen.getByRole('button', { name: 'Clinical phenotype' })).toHaveClass('active');

    rerender(
      <DatabasePage
        logic={{
          rules: phenoOnlyRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: { ...sampleDatabase, id: 'db2' },
        }}
      />,
    );
    expect(screen.queryByRole('group', { name: 'Position annotation mode' })).not.toBeInTheDocument();

    // Clinical-only rules
    rerender(
      <DatabasePage
        logic={{
          rules: clinicalOnlyRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: { ...sampleDatabase, id: 'db3' },
        }}
      />,
    );
    expect(screen.queryByRole('group', { name: 'Position annotation mode' })).not.toBeInTheDocument();

    // Neither phenotype nor clinical_phenotype, with ic50/score -> renders summary + ic50/score sections
    rerender(
      <DatabasePage
        logic={{
          rules: noPhenoRules,
          formulaRules: [],
          mutationPlotMeta: samplePlotMeta,
          selectedDatabase: { ...sampleDatabase, id: 'db4' },
        }}
      />,
    );
    expect(document.querySelector('.database-summary-pie-card')).toBeInTheDocument();
    expect(document.querySelector('.database-reference-section-score')).toBeInTheDocument();
  });
});
