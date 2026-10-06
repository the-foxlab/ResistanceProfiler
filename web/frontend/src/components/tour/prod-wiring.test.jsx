import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { TourProvider, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';

// Reproduces the PRODUCTION wiring: the provider is given the real steps (as main.jsx
// does), and the overlay uses the provider's steps (no steps prop). This guards
// against the regression where main.jsx passed steps={[]} and the tour died after Next.
function ProdLikeApp({ steps, initialPath = '/analysis' }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <TourProvider steps={steps}>
        <div>
          <div className="topbar-db-bar" />
          <div className="top-bar-nav" />
        </div>
        <TourOverlay />
      </TourProvider>
    </MemoryRouter>
  );
}

const REAL_STEPS = [
  { id: 'a', targetSelector: '.topbar-db-bar', title: 'A', body: 'aa' },
  { id: 'b', targetSelector: '.top-bar-nav', title: 'B', body: 'bb' },
  { id: 'c', targetSelector: '.top-bar-nav', title: 'C', body: 'cc' },
];

describe('production wiring (provider owns real steps, overlay uses provider steps)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows no tour UI on non-analysis routes', () => {
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} initialPath="/" />);
    expect(container.querySelector('.tour-coach-card')).toBeNull();
    expect(container.querySelector('.tour-overlay')).toBeNull();
  });

  it('shows the non-blocking coach card on first /analysis visit (does not auto-start the tour)', () => {
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    expect(container.querySelector('.tour-coach-card')).not.toBeNull();
    expect(screen.queryByText('A')).toBeNull();
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /no thanks/i })).toBeInTheDocument();
  });

  it('accepting the coach card starts the tour at step 0', () => {
    render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText(/1\s*\/\s*3/)).toBeInTheDocument();
  });

  it('declining the coach card writes the version token and shows no tour', () => {
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /no thanks/i }));
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    expect(container.querySelector('.tour-overlay')).toBeNull();
    expect(container.querySelector('.tour-coach-card')).toBeNull();
  });

  it('Next advances through all steps and does not die after the first Next (the regression)', () => {
    render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(screen.getByText('A')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('B')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('C')).toBeInTheDocument();
    expect(screen.getByText(/3\s*\/\s*3/)).toBeInTheDocument();
  });

  it('ArrowRight advances steps in the production wiring', () => {
    render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(screen.getByText('B')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(screen.getByText('C')).toBeInTheDocument();
  });

  it('Finish on the last step persists dismissal in the production wiring', () => {
    render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    fireEvent.click(screen.getByRole('button', { name: /finish/i }));
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
  });

  it('does not show the coach card when the current version is already dismissed', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    expect(container.querySelector('.tour-coach-card')).toBeNull();
    expect(container.querySelector('.tour-overlay')).toBeNull();
  });

  it('bumping the version re-shows the coach card on next /analysis visit', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, 'v0');
    render(<ProdLikeApp steps={REAL_STEPS} initialPath="/analysis" />);
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
  });
});
