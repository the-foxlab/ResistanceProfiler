import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

import { TourProvider, useTour, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';
import { buildTourSteps } from './steps';

// A small app that wires the tour to real navigation setters (spies) and renders targets
// the steps point at, so we can test the full provider + overlay + steps integration.
function TourApp({ steps, setActiveMode, setActiveProfileMode, setAnalyzeSubMode }) {
  return (
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
        <div data-tour-target="sidebar-results" data-testid="target-sidebar-results" />
        <div data-tour-target="sidebar-database" data-testid="target-sidebar-database" />
        <div data-tour-target="sidebar-mutations" data-testid="target-sidebar-mutations" />
        <div data-tour-target="sidebar-about" data-testid="target-sidebar-about" />
      </div>
      <TourOverlay steps={steps} />
    </TourProvider>
  );
}

const STEPS = buildTourSteps({
  setActiveMode: () => {},
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

  it('shows the start prompt on first visit (no stored version) instead of auto-starting', () => {
    const { container } = render(<TourApp steps={STEPS} />);
    expect(container.querySelector('.tour-prompt')).not.toBeNull();
    expect(container.querySelector('.tour-tooltip')).toBeNull();
    expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
  });

  it('does NOT show the prompt when the current version is already dismissed', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    const { container } = render(<TourApp steps={STEPS} />);
    expect(container.querySelector('.tour-overlay')).toBeNull();
  });

  it('bumping the version re-triggers the prompt on next mount', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, 'v0');
    const { container } = render(<TourApp steps={STEPS} />);
    expect(container.querySelector('.tour-prompt')).not.toBeNull();
  });

  describe('start prompt acceptance / decline', () => {
    it('accepting the prompt activates the tour at step 0', () => {
      render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      expect(screen.getByText(STEPS[0].title)).toBeInTheDocument();
    });

    it('declining the prompt (No thanks) writes the version token and hides the overlay', () => {
      const { container } = render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /no thanks/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
      expect(container.querySelector('.tour-overlay')).toBeNull();
    });
  });

  describe('every exit path writes the version token', () => {
    it('Skip button persists dismissal', () => {
      const { container } = render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      fireEvent.click(screen.getByRole('button', { name: /skip/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
      expect(container.querySelector('.tour-overlay')).toBeNull();
    });

    it('Esc key persists dismissal', () => {
      render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('backdrop click does NOT dismiss the active tour (must use Skip/Esc/Finish)', () => {
      const { container } = render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      fireEvent.click(container.querySelector('.tour-backdrop'));
      // Tour is still active — no token written, overlay still present.
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBeNull();
      expect(container.querySelector('.tour-overlay')).not.toBeNull();
      expect(screen.getByText(STEPS[0].title)).toBeInTheDocument();
    });

    it('Finish on the last step persists dismissal', () => {
      render(<TourApp steps={STEPS} />);
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      // Advance to the last step via ArrowRight repeated.
      for (let i = 1; i < STEPS.length; i += 1) {
        fireEvent.keyDown(document, { key: 'ArrowRight' });
      }
      fireEvent.click(screen.getByRole('button', { name: /finish/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });
  });

  it('the "Take a tour" button starts the tour regardless of the dismissed flag', () => {
    localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
    let tour;
    function Starter() {
      tour = useTour();
      return (
        <button type="button" onClick={() => tour.startTour()}>
          take a tour
        </button>
      );
    }
    const { container } = render(
      <TourProvider steps={STEPS}>
        <Starter />
        <TourOverlay steps={STEPS} />
      </TourProvider>,
    );
    expect(container.querySelector('.tour-overlay')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /take a tour/i }));
    expect(container.querySelector('.tour-overlay')).not.toBeNull();
  });

  it('nextStep/prevStep move the step index within bounds and clamp at the ends', () => {
    render(<TourApp steps={STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    // Start at step 0.
    expect(screen.getByText(/1\s*\/\s*\d+/)).toBeInTheDocument();
    // Prev at 0 stays at 0.
    fireEvent.keyDown(document, { key: 'ArrowLeft' });
    expect(screen.getByText(/1\s*\/\s*\d+/)).toBeInTheDocument();
    // Forward through all steps; should clamp at last.
    for (let i = 1; i < STEPS.length + 2; i += 1) {
      fireEvent.keyDown(document, { key: 'ArrowRight' });
    }
    expect(screen.getByText(new RegExp(`${STEPS.length}\\s*/\\s*${STEPS.length}`))).toBeInTheDocument();
  });

  it('a before hook spy is called when its step becomes active', () => {
    const beforeSpy = vi.fn();
    const steps = [
      { id: 'x', targetSelector: '.sidebar-rail', title: 'X', body: 'x' },
      { id: 'y', targetSelector: '.sidebar-rail', title: 'Y', body: 'y', before: beforeSpy },
    ];
    render(<TourApp steps={steps} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    expect(beforeSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(beforeSpy).toHaveBeenCalledTimes(1);
  });

  it('the tooltip aria-describedby matches the spotlight element', () => {
    const { container } = render(<TourApp steps={STEPS} />);
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
    const { container } = render(<TourApp steps={STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
    // Tour still activated in-session despite storage failure.
    expect(container.querySelector('.tour-tooltip')).not.toBeNull();
    // Esc dismiss does not throw.
    expect(() => fireEvent.keyDown(document, { key: 'Escape' })).not.toThrow();
    setItemSpy.mockRestore();
  });
});
