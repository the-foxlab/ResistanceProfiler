import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { HomePage } from './HomePage';
import { TourProvider } from '../tour/TourContext';

const DATABASES = [
  {
    id: 'db1',
    display_name: 'Herpes DRG',
    supported_organisms: ['Herpes simplex virus 1', 'Herpes simplex virus 2'],
    mutation_count: 120,
    metadata: { maintainer_update: '2026-09-01' },
  },
  {
    id: 'db2',
    display_name: 'HIV DRG',
    supported_organisms: ['HIV-1'],
    mutation_count: 80,
    metadata: { maintainer_update: '2026-08-15' },
  },
];

function renderHome(logicOverrides = {}) {
  const logic = {
    databases: DATABASES,
    runExampleProfile: vi.fn(),
    ...logicOverrides,
  };
  return render(
    <MemoryRouter initialEntries={['/']}>
      <TourProvider steps={[]}>
        <HomePage logic={logic} />
      </TourProvider>
    </MemoryRouter>,
  );
}

describe('HomePage hero', () => {
  it('renders the value proposition headline', () => {
    renderHome();
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('Launch analysis navigates to /analysis', () => {
    renderHome();
    const link = screen.getByRole('link', { name: /launch analysis/i });
    expect(link.getAttribute('href')).toBe('/analysis');
  });

  it('Explore databases navigates to /databases', () => {
    renderHome();
    const link = screen.getByRole('link', { name: /explore databases/i });
    expect(link.getAttribute('href')).toBe('/databases');
  });

  it('Take a tour calls onStartTour', () => {
    const onStartTour = vi.fn();
    renderHome({ onStartTour });
    fireEvent.click(screen.getByRole('button', { name: /take a tour/i }));
    expect(onStartTour).toHaveBeenCalledTimes(1);
  });
});

describe('HomePage example run', () => {
  it('Run an example triggers runExampleProfile', () => {
    const runExampleProfile = vi.fn();
    renderHome({ runExampleProfile });
    fireEvent.click(screen.getByRole('button', { name: /example/i }));
    expect(runExampleProfile).toHaveBeenCalledTimes(1);
  });
});

describe('HomePage database showcase', () => {
  it('stat strip totals equal the sums over the databases list', () => {
    renderHome();
    const strip = screen.getByLabelText(/database stats/i);
    expect(strip.textContent).toContain('2');
    expect(strip.textContent).toContain('200');
    // Pathogens: 3 distinct organisms across the two databases.
    expect(strip.textContent).toContain('3');
  });

  it('renders one card per database with rule count and organisms', () => {
    renderHome();
    expect(screen.getByText('Herpes DRG')).toBeInTheDocument();
    expect(screen.getByText('HIV DRG')).toBeInTheDocument();
    expect(screen.getByText(/120/)).toBeInTheDocument();
  });

  it('renders without crashing when the database list is empty', () => {
    renderHome({ databases: [] });
    expect(screen.getByText(/no databases/i)).toBeInTheDocument();
  });
});

describe('HomePage trust notices', () => {
  it('shows research-use-only and no-curation notices', () => {
    renderHome();
    expect(screen.getByText(/research use only/i)).toBeInTheDocument();
    expect(screen.getByText(/no database curation/i)).toBeInTheDocument();
  });
});
