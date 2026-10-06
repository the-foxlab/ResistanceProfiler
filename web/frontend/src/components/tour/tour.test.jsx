import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { TourProvider, useTour, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';
import { buildTourSteps } from './steps';

// A small app that wires the tour to real navigation setters (spies) and renders
// targets the steps point at, so we can test provider + overlay + steps together.
function TourApp({ steps, initialPath = '/' }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <TourProvider steps={steps}>
        <div>
          <div className="topbar-db-bar" data-testid="target-db" />
          <div data-tour-target="vcf-file" data-testid="target-vcf-file" />
          <div data-tour-target="vcf-reference" data-testid="target-vcf-ref" />
          <div data-tour-target="vcf-bam" data-testid="target-vcf-bam" />
          <div data-tour-target="vcf-sample-name" data-testid="target-vcf-sample" />
          <div data-tour-target="vcf-frequency-cutoff" data-testid="target-vcf-freq" />
          <div data-tour-target="vcf-coverage-cutoff" data-testid="target-vcf-cov" />
          <div className="profile-upload-row-fasta" data-testid="target-fasta" />
          <div className="profile-upload-row-regenerate" data-testid="target-regen" />
          <div className="profile-input-card">
            <div className="profile-analyze-row" data-testid="target-analyze" />
          </div>
          <div className="analyze-report-actions" data-testid="target-reports" />
          <div className="analyze-submode-row" data-testid="target-submode" />
          <div data-tour-target="sidebar-results" data-testid="target-sidebar-results" />
          <div data-tour-target="sidebar-database" data-testid="target-sidebar-database" />
          <div data-tour-target="sidebar-mutations" data-testid="target-sidebar-mutations" />
          <div className="top-bar-nav" data-testid="target-topnav" />
        </div>
        <TourOverlay steps={steps} />
      </TourProvider>
    </MemoryRouter>
  );
}

const STEPS = buildTourSteps({
  navigate: () => {},
  setActiveProfileMode: () => {},
  setAnalyzeSubMode: () => {},
});

describe('guided tour integration', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows NO tour UI on first visit to a non-analysis route (no modal prompt)', () => {
    const { container } = render(<TourApp steps={STEPS} initialPath="/" />);
    expect(container.querySelector('.tour-prompt')).toBeNull();
    expect(container.querySelector('.tour-overlay')).toBeNull();
    expect(container.querySelector('.tour-coach-card')).toBeNull();
  });

  it('shows the coach card (non-blocking) when entering /analysis for the first time', () => {
    const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
    const card = container.querySelector('.tour-coach-card');
    expect(card).not.toBeNull();
    // Non-blocking: not a modal dialog.
    expect(card.getAttribute('aria-modal')).toBeNull();
    expect(container.querySelector('.tour-backdrop')).toBeNull();
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /no thanks/i })).toBeInTheDocument();
  });

  it('does NOT show the coach card when the current version is already dismissed', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
    expect(container.querySelector('.tour-coach-card')).toBeNull();
    expect(container.querySelector('.tour-overlay')).toBeNull();
  });

  it('bumping the version re-triggers the coach card on next /analysis visit', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, 'v0');
    const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
    expect(container.querySelector('.tour-coach-card')).not.toBeNull();
  });

  it('the coach card does NOT appear on Home, About, or Databases routes', () => {
    for (const path of ['/', '/about', '/databases']) {
      const { container, unmount } = render(<TourApp steps={STEPS} initialPath={path} />);
      expect(container.querySelector('.tour-coach-card')).toBeNull();
      unmount();
    }
  });

  describe('coach card acceptance / decline', () => {
    it('accepting the coach card activates the tour at step 0', () => {
      render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      expect(screen.getByText(STEPS[0].title)).toBeInTheDocument();
    });

    it('declining the coach card (No thanks) writes the version token and hides the overlay', () => {
      const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.click(screen.getByRole('button', { name: /no thanks/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
      expect(container.querySelector('.tour-overlay')).toBeNull();
      expect(container.querySelector('.tour-coach-card')).toBeNull();
    });

    it('Esc on the coach card declines it (persists dismissal)', () => {
      const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
      expect(container.querySelector('.tour-coach-card')).toBeNull();
    });
  });

  describe('every exit path writes the version token', () => {
    it('Skip button persists dismissal', () => {
      const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      fireEvent.click(screen.getByRole('button', { name: /skip/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
      expect(container.querySelector('.tour-overlay')).toBeNull();
    });

    it('Esc key persists dismissal during the active tour', () => {
      render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('Finish on the last step persists dismissal', () => {
      render(<TourApp steps={STEPS} initialPath="/analysis" />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      for (let i = 1; i < STEPS.length; i += 1) {
        fireEvent.keyDown(document, { key: 'ArrowRight' });
      }
      fireEvent.click(screen.getByRole('button', { name: /finish/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });
  });

  it('the "Take a tour" button starts the tour regardless of the dismissed flag', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    function Starter() {
      const tour = useTour();
      return (
        <button type="button" onClick={() => tour.startTour()}>
          take a tour
        </button>
      );
    }
    const { container } = render(
      <MemoryRouter initialEntries={['/analysis']}>
        <TourProvider steps={STEPS}>
          <Starter />
          <TourOverlay steps={STEPS} />
        </TourProvider>
      </MemoryRouter>,
    );
    expect(container.querySelector('.tour-overlay')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /take a tour/i }));
    expect(container.querySelector('.tour-overlay')).not.toBeNull();
  });

  it('nextStep/prevStep move the step index within bounds and clamp at the ends', () => {
    render(<TourApp steps={STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(screen.getByText(/1\s*\/\s*\d+/)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'ArrowLeft' });
    expect(screen.getByText(/1\s*\/\s*\d+/)).toBeInTheDocument();
    for (let i = 1; i < STEPS.length + 2; i += 1) {
      fireEvent.keyDown(document, { key: 'ArrowRight' });
    }
    expect(screen.getByText(new RegExp(STEPS.length + '\\s*/\\s*' + STEPS.length))).toBeInTheDocument();
  });

  it('a before hook spy is called when its step becomes active', () => {
    const beforeSpy = vi.fn();
    const steps = [
      { id: 'x', targetSelector: '.top-bar-nav', title: 'X', body: 'x' },
      { id: 'y', targetSelector: '.top-bar-nav', title: 'Y', body: 'y', before: beforeSpy },
    ];
    render(<TourApp steps={steps} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(beforeSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(beforeSpy).toHaveBeenCalledTimes(1);
  });

  it('the tooltip aria-describedby matches the spotlight element', () => {
    const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    const spotlight = container.querySelector('.tour-spotlight');
    const describedBy = spotlight.getAttribute('aria-describedby');
    expect(describedBy).toBe('tour-tooltip');
    expect(container.querySelector('#tour-tooltip')).not.toBeNull();
  });

  it('a simulated localStorage.setItem throw is caught and does not block startTour', () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('private mode');
    });
    const { container } = render(<TourApp steps={STEPS} initialPath="/analysis" />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(container.querySelector('.tour-tooltip')).not.toBeNull();
    expect(() => fireEvent.keyDown(document, { key: 'Escape' })).not.toThrow();
    setItemSpy.mockRestore();
  });
});
