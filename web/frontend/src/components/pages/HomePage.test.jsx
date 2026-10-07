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

  it('offers session reports, mutation browsing, and database comparison without a Report and share card', () => {
    renderHome();
    expect(screen.getByRole('link', { name: 'Session reports' })).toHaveAttribute('href', '/analysis/reports');
    expect(screen.getByRole('link', { name: 'Browse mutations' })).toHaveAttribute('href', '/databases/mutations');
    expect(screen.getByRole('link', { name: 'Compare databases' })).toHaveAttribute('href', '/databases/compare');
    expect(screen.queryByRole('heading', { name: 'Report and share' })).toBeNull();
  });

  it('Take a tour calls onStartTour', () => {
    const onStartTour = vi.fn();
    renderHome({ onStartTour });
    fireEvent.click(screen.getByRole('button', { name: /take a tour/i }));
    expect(onStartTour).toHaveBeenCalledTimes(1);
  });
});

describe('HomePage example run', () => {
  it('does not offer a hardcoded example run', () => {
    renderHome();
    expect(screen.queryByRole('button', { name: /example/i })).toBeNull();
  });
});

describe('HomePage context', () => {
  it('explains the resistance problem and the standardization goal', () => {
    renderHome();
    const context = screen.getByRole('region', { name: /why resistanceprofiler/i });
    expect(context.querySelectorAll('p')).toHaveLength(1);
    expect(context).toHaveTextContent(/antiviral resistance is a significant health concern/i);
    expect(context).toHaveTextContent(/standardize how resistance databases are organized and how viral sequence data are analyzed/i);
    expect(screen.getByRole('link', { name: /stanford hiv drug resistance database/i })).toHaveAttribute(
      'href',
      'https://hivdb.stanford.edu/',
    );
  });

  it('leaves workflow detail to About instead of repeating it on Home', () => {
    renderHome();
    expect(screen.queryByRole('heading', { name: /how it works/i })).toBeNull();
  });
});

describe('HomePage database showcase', () => {
  it('opens the selected database from its tile', () => {
    const setSelectedDatabaseId = vi.fn();
    renderHome({ setSelectedDatabaseId });

    const link = screen.getByRole('link', { name: /open herpes drg in databases/i });
    expect(link).toHaveAttribute('href', '/databases');
    fireEvent.click(link);
    expect(setSelectedDatabaseId).toHaveBeenCalledWith('db1');
  });

  it('does not show a separate database dashboard link', () => {
    renderHome();
    expect(screen.queryByRole('link', { name: /open the database dashboard/i })).toBeNull();
  });

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

    const card = screen.getByRole('heading', { name: 'Herpes DRG' }).closest('a');
    expect(card.querySelectorAll('.home-db-organisms li')).toHaveLength(2);
    expect(card.querySelector('.home-db-meta').tagName).toBe('FOOTER');
    expect(card.querySelector('.home-db-meta')).toHaveTextContent('2026-09-01');
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
