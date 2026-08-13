import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act, renderHook } from '@testing-library/react';
import { useState } from 'react';

import { TourProvider, useTour, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';

// Helper: render the provider and expose the hook state via a consumer.
function TourConsumer({ onState }) {
  const tour = useTour();
  if (onState) {
    onState(tour);
  }
  return (
    <div>
      <span data-testid="is-active">{String(tour.isActive)}</span>
      <span data-testid="is-prompting">{String(tour.isPrompting)}</span>
      <span data-testid="step-index">{tour.stepIndex}</span>
      <button type="button" onClick={() => tour.startTour()}>start</button>
      <button type="button" onClick={() => tour.nextStep()}>next</button>
      <button type="button" onClick={() => tour.prevStep()}>prev</button>
      <button type="button" onClick={() => tour.dismissTour()}>dismiss</button>
      <button type="button" onClick={() => tour.declineTour()}>decline</button>
    </div>
  );
}

function renderTour(steps) {
  let captured;
  const utils = render(
    <TourProvider steps={steps}>
      <TourConsumer
        onState={(t) => {
          captured = t;
        }}
      />
    </TourProvider>,
  );
  return { ...utils, getTour: () => captured };
}

describe('TourContext', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const sampleSteps = [
    { id: 'a', title: 'A', body: 'aa' },
    { id: 'b', title: 'B', body: 'bb' },
    { id: 'c', title: 'C', body: 'cc' },
  ];

  describe('initial state', () => {
    it('exposes isActive=false and stepIndex=0 when the current version is dismissed', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      renderTour(sampleSteps);
      expect(screen.getByTestId('is-active').textContent).toBe('false');
      expect(screen.getByTestId('step-index').textContent).toBe('0');
    });

    it('does not change consumer rendering when inactive (renders children)', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      renderTour(sampleSteps);
      expect(screen.getByText('start')).toBeInTheDocument();
    });
  });

  describe('startTour', () => {
    it('sets isActive=true and resets stepIndex to 0', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      expect(screen.getByTestId('is-active').textContent).toBe('true');
      expect(screen.getByTestId('step-index').textContent).toBe('0');
    });
  });

  describe('nextStep / prevStep', () => {
    it('advances the step index forward', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      fireEvent.click(screen.getByText('next'));
      expect(screen.getByTestId('step-index').textContent).toBe('1');
      fireEvent.click(screen.getByText('next'));
      expect(screen.getByTestId('step-index').textContent).toBe('2');
    });

    it('clamps at the last step (does not overflow)', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      fireEvent.click(screen.getByText('next'));
      fireEvent.click(screen.getByText('next'));
      fireEvent.click(screen.getByText('next'));
      expect(screen.getByTestId('step-index').textContent).toBe('2');
    });

    it('clamps at the first step (does not underflow)', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      fireEvent.click(screen.getByText('prev'));
      expect(screen.getByTestId('step-index').textContent).toBe('0');
    });
  });

  describe('dismissTour', () => {
    it('sets isActive=false and writes the version token to localStorage', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      fireEvent.click(screen.getByText('dismiss'));
      expect(screen.getByTestId('is-active').textContent).toBe('false');
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });

    it('clamps stepIndex back to 0 after dismiss', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('start'));
      fireEvent.click(screen.getByText('next'));
      fireEvent.click(screen.getByText('dismiss'));
      expect(screen.getByTestId('step-index').textContent).toBe('0');
    });
  });

  describe('versioned start prompt', () => {
    it('shows the start prompt (isPrompting=true) when the stored version does not match TOUR_VERSION', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, 'v0');
      renderTour(sampleSteps);
      expect(screen.getByTestId('is-prompting').textContent).toBe('true');
      expect(screen.getByTestId('is-active').textContent).toBe('false');
    });

    it('does NOT show the prompt when the stored version matches TOUR_VERSION', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      renderTour(sampleSteps);
      expect(screen.getByTestId('is-prompting').textContent).toBe('false');
      expect(screen.getByTestId('is-active').textContent).toBe('false');
    });

    it('shows the prompt when no version is stored yet (first visit)', () => {
      renderTour(sampleSteps);
      expect(screen.getByTestId('is-prompting').textContent).toBe('true');
    });

    it('accepting the prompt (startTour) activates the tour and clears the prompt', () => {
      renderTour(sampleSteps);
      expect(screen.getByTestId('is-prompting').textContent).toBe('true');
      fireEvent.click(screen.getByText('start'));
      expect(screen.getByTestId('is-active').textContent).toBe('true');
      expect(screen.getByTestId('is-prompting').textContent).toBe('false');
    });

    it('declining the prompt (declineTour) writes the token and clears the prompt', () => {
      renderTour(sampleSteps);
      fireEvent.click(screen.getByText('decline'));
      expect(screen.getByTestId('is-prompting').textContent).toBe('false');
      expect(screen.getByTestId('is-active').textContent).toBe('false');
      expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBe(TOUR_VERSION);
    });
  });

  describe('storage failure (fail-open)', () => {
    it('does not crash when localStorage.setItem throws and still activates in-session', () => {
      const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('private mode');
      });
      expect(() => renderTour(sampleSteps)).not.toThrow();
      fireEvent.click(screen.getByText('start'));
      expect(screen.getByTestId('is-active').textContent).toBe('true');
      // dismiss must not throw even though storage write fails.
      expect(() => fireEvent.click(screen.getByText('dismiss'))).not.toThrow();
      expect(screen.getByTestId('is-active').textContent).toBe('false');
      setItemSpy.mockRestore();
    });

    it('does not crash when localStorage.getItem throws', () => {
      const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('storage disabled');
      });
      expect(() => renderTour(sampleSteps)).not.toThrow();
      getItemSpy.mockRestore();
    });
  });

  describe('before hook ownership', () => {
    // The provider intentionally does NOT fire `before` hooks — TourOverlay owns that so
    // the real, navigation-bound steps (passed as a prop to the overlay) run exactly once.
    it('does not fire a step before hook (the overlay owns before-hook firing)', () => {
      const beforeA = vi.fn();
      const steps = [
        { id: 'a', title: 'A', body: 'aa', before: beforeA },
        { id: 'b', title: 'B', body: 'bb' },
      ];
      renderTour(steps);
      fireEvent.click(screen.getByText('start'));
      expect(beforeA).not.toHaveBeenCalled();
    });
  });

  describe('useTour outside provider', () => {
    it('throws a descriptive error when used without a TourProvider', () => {
      // Capture the error boundary manually.
      let caught = null;
      function Bad() {
        try {
          useTour();
        } catch (e) {
          caught = e;
        }
        return null;
      }
      render(<Bad />);
      expect(caught).not.toBeNull();
      expect(caught.message).toMatch(/TourProvider/i);
    });
  });
});
