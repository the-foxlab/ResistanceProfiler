import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { TourProvider, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';

// A minimal app that renders a target element and the overlay wired to the provider.
function AppWithTarget({ steps }) {
  return (
    <TourProvider steps={steps}>
      <div data-testid="target-a" id="target-a">Target A</div>
      <div data-testid="target-b" id="target-b">Target B</div>
      <TourOverlay />
    </TourProvider>
  );
}

const stepsWithTargets = [
  { id: 'a', targetSelector: '#target-a', title: 'Step A', body: 'Body A' },
  { id: 'b', targetSelector: '#target-b', title: 'Step B', body: 'Body B' },
];

// Helper: render and accept the start prompt so the tour is active at step 0.
function renderAndAcceptTour(steps) {
  const utils = render(<AppWithTarget steps={steps} />);
  fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
  return utils;
}

describe('TourOverlay', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('start prompt', () => {
    it('shows the start prompt on first visit instead of auto-starting', () => {
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      expect(container.querySelector('.tour-prompt')).not.toBeNull();
      expect(container.querySelector('.tour-tooltip')).toBeNull();
      expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /no thanks/i })).toBeInTheDocument();
    });

    it('renders nothing when the current version is already dismissed', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      expect(container.querySelector('.tour-overlay')).toBeNull();
      expect(container.querySelector('.tour-prompt')).toBeNull();
    });

    it('Esc on the prompt declines it and writes the version token', () => {
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(container.querySelector('.tour-prompt')).toBeNull();
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('backdrop click on the prompt declines it and writes the version token', () => {
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      fireEvent.click(container.querySelector('.tour-backdrop'));
      expect(container.querySelector('.tour-prompt')).toBeNull();
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });
  });

  describe('active tour', () => {
    it('renders the tooltip with the current step title and body when active', () => {
      renderAndAcceptTour(stepsWithTargets);
      expect(screen.getByText('Step A')).toBeInTheDocument();
      expect(screen.getByText('Body A')).toBeInTheDocument();
    });

    it('shows the step counter (Step x / y)', () => {
      renderAndAcceptTour(stepsWithTargets);
      expect(screen.getByText(/1\s*\/\s*2/)).toBeInTheDocument();
    });

    it('renders a spotlight element with aria-describedby pointing at the tooltip', () => {
      const { container } = renderAndAcceptTour(stepsWithTargets);
      const spotlight = container.querySelector('.tour-spotlight');
      expect(spotlight).not.toBeNull();
      const describedBy = spotlight.getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
      const tooltip = container.querySelector(`#${describedBy}`);
      expect(tooltip).not.toBeNull();
    });

    it('Next button advances the step', () => {
      renderAndAcceptTour(stepsWithTargets);
      fireEvent.click(screen.getByRole('button', { name: /next/i }));
      expect(screen.getByText('Step B')).toBeInTheDocument();
      expect(screen.getByText(/2\s*\/\s*2/)).toBeInTheDocument();
    });

    it('Prev button moves back', () => {
      renderAndAcceptTour(stepsWithTargets);
      fireEvent.click(screen.getByRole('button', { name: /next/i }));
      fireEvent.click(screen.getByRole('button', { name: /prev|back/i }));
      expect(screen.getByText('Step A')).toBeInTheDocument();
    });

    it('Skip button calls dismissTour and writes the version token', () => {
      const { container } = renderAndAcceptTour(stepsWithTargets);
      fireEvent.click(screen.getByRole('button', { name: /skip/i }));
      expect(container.querySelector('.tour-overlay')).toBeNull();
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('Esc key dismisses the active tour and persists', () => {
      const { container } = renderAndAcceptTour(stepsWithTargets);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(container.querySelector('.tour-overlay')).toBeNull();
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('Finish button on the last step dismisses the tour and persists', () => {
      renderAndAcceptTour(stepsWithTargets);
      fireEvent.click(screen.getByRole('button', { name: /next/i }));
      // Now on last step: the Next button becomes Finish.
      fireEvent.click(screen.getByRole('button', { name: /finish|done|complete/i }));
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('arrow-right and arrow-left keys move steps', () => {
      renderAndAcceptTour(stepsWithTargets);
      fireEvent.keyDown(document, { key: 'ArrowRight' });
      expect(screen.getByText('Step B')).toBeInTheDocument();
      fireEvent.keyDown(document, { key: 'ArrowLeft' });
      expect(screen.getByText('Step A')).toBeInTheDocument();
    });

    it('does not crash when the target selector does not match any element', () => {
      const steps = [{ id: 'missing', targetSelector: '#nope', title: 'X', body: 'Y' }];
      expect(() => renderAndAcceptTour(steps)).not.toThrow();
      expect(screen.getByText('X')).toBeInTheDocument();
    });
  });
});
