import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { TourProvider, useTour, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';

// Reproduces the PRODUCTION wiring: the provider is given the real steps (as App does
// after the fix), and the overlay uses the provider's steps (no steps prop). This guards
// against the regression where main.jsx passed steps={[]} and the tour died after Next.
function ProdLikeApp({ steps }) {
  return (
    <TourProvider steps={steps}>
      <div>
        <div className="topbar-db-bar" />
        <div className="sidebar-rail" />
      </div>
      <TourOverlay />
    </TourProvider>
  );
}

const REAL_STEPS = [
  { id: 'a', targetSelector: '.topbar-db-bar', title: 'A', body: 'aa' },
  { id: 'b', targetSelector: '.sidebar-rail', title: 'B', body: 'bb' },
  { id: 'c', targetSelector: '.sidebar-rail', title: 'C', body: 'cc' },
];

describe('production wiring (provider owns real steps, overlay uses provider steps)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the start prompt on first visit (does not auto-start the tour)', () => {
    render(<ProdLikeApp steps={REAL_STEPS} />);
    expect(screen.queryByText('A')).toBeNull();
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /no thanks|decline|skip/i })).toBeInTheDocument();
  });

  it('accepting the prompt starts the tour at step 0', () => {
    render(<ProdLikeApp steps={REAL_STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText(/1\s*\/\s*3/)).toBeInTheDocument();
  });

  it('declining the prompt writes the version token and shows no tour', () => {
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /no thanks|decline|skip/i }));
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    expect(container.querySelector('.tour-overlay')).toBeNull();
    expect(container.querySelector('.tour-prompt')).toBeNull();
  });

  it('Next advances through all steps and does not die after the first Next (the regression)', () => {
    render(<ProdLikeApp steps={REAL_STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(screen.getByText('A')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('B')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('C')).toBeInTheDocument();
    expect(screen.getByText(/3\s*\/\s*3/)).toBeInTheDocument();
  });

  it('ArrowRight advances steps in the production wiring', () => {
    render(<ProdLikeApp steps={REAL_STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(screen.getByText('B')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'ArrowRight' });
    expect(screen.getByText('C')).toBeInTheDocument();
  });

  it('Finish on the last step persists dismissal in the production wiring', () => {
    render(<ProdLikeApp steps={REAL_STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    fireEvent.click(screen.getByRole('button', { name: /finish/i }));
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
  });

  it('does not show the prompt when the current version is already dismissed', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    const { container } = render(<ProdLikeApp steps={REAL_STEPS} />);
    expect(container.querySelector('.tour-prompt')).toBeNull();
    expect(container.querySelector('.tour-overlay')).toBeNull();
  });

  it('bumping the version re-shows the prompt on next mount', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, 'v0');
    render(<ProdLikeApp steps={REAL_STEPS} />);
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
  });
});
