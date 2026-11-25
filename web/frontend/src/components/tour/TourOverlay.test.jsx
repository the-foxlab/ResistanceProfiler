import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useState } from 'react';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { TourProvider, useTour, TOUR_VERSION, TOUR_STORAGE_KEY } from './TourContext';
import { TourOverlay } from './TourOverlay';

// A minimal app that renders a target element and the overlay wired to the provider.
// The initial path is /analysis so the provider offers the coach card.
function AppWithTarget({ steps }) {
  return (
    <MemoryRouter initialEntries={['/analysis']}>
      <TourProvider steps={steps}>
        <div data-testid="target-a" id="target-a">Target A</div>
        <div data-testid="target-b" id="target-b">Target B</div>
        <TourOverlay />
      </TourProvider>
    </MemoryRouter>
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

  describe('coach card', () => {
    it('shows the coach card on first /analysis visit instead of auto-starting', () => {
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      expect(container.querySelector('.tour-coach-card')).not.toBeNull();
      expect(container.querySelector('.tour-tooltip')).toBeNull();
      expect(screen.getByRole('button', { name: /start tour/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /no thanks/i })).toBeInTheDocument();
    });

    it('renders nothing when the current version is already dismissed', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      expect(container.querySelector('.tour-overlay')).toBeNull();
      expect(container.querySelector('.tour-coach-card')).toBeNull();
    });

    it('Esc on the coach card declines it and writes the version token', () => {
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(container.querySelector('.tour-coach-card')).toBeNull();
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

    it('renders padded spotlight for non-zero rect and does not steal focus when geometry is unchanged', () => {
      const rafCallbacks = [];
      vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
        rafCallbacks.push(cb);
        return rafCallbacks.length;
      });
      vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});

      const rectValue = { top: 40, left: 60, width: 120, height: 30, right: 180, bottom: 70 };
      const getRectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
        if (this.id === 'target-a') {
          return rectValue;
        }
        return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 };
      });

      const { container } = renderAndAcceptTour(stepsWithTargets);

      act(() => {
        while (rafCallbacks.length > 0) {
          const cb = rafCallbacks.shift();
          cb(0);
        }
      });

      const spotlight = container.querySelector('.tour-spotlight');
      expect(spotlight.style.left).toBe('52px');
      expect(spotlight.style.top).toBe('32px');
      expect(spotlight.style.width).toBe('136px');
      expect(spotlight.style.height).toBe('46px');

      const nextButton = screen.getByRole('button', { name: /next/i });
      nextButton.focus();
      expect(document.activeElement).toBe(nextButton);

      const callsAfterInit = getRectSpy.mock.calls.length;
      act(() => {
        window.dispatchEvent(new Event('resize'));
        window.dispatchEvent(new Event('scroll'));
        while (rafCallbacks.length > 0) {
          const cb = rafCallbacks.shift();
          cb(0);
        }
      });

      expect(document.activeElement).toBe(nextButton);
      expect(getRectSpy.mock.calls.length - callsAfterInit).toBeLessThanOrEqual(2);
      expect(rafCallbacks).toHaveLength(0);
    });

    it('falls back to centered 0x0 spotlight when target getBoundingClientRect has zero area', () => {
      vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
        top: 0,
        left: 0,
        width: 0,
        height: 0,
        right: 0,
        bottom: 0,
      });

      const { container } = renderAndAcceptTour(stepsWithTargets);
      const spotlight = container.querySelector('.tour-spotlight');
      expect(spotlight.style.left).toBe('50%');
      expect(spotlight.style.top).toBe('50%');
      expect(spotlight.style.width).toBe('0px');
      expect(spotlight.style.height).toBe('0px');
    });
  });

  describe('accessibility (WA-053)', () => {
    it('points coach card aria-labelledby to its h3 heading rather than #tour-prompt', () => {
      render(<AppWithTarget steps={stepsWithTargets} />);
      const dialog = screen.getByRole('dialog', { name: 'Take a quick tour?' });
      const heading = screen.getByRole('heading', { level: 3, name: 'Take a quick tour?' });
      expect(dialog.getAttribute('aria-labelledby')).toBe(heading.id);
      expect(heading.id).not.toBe('tour-prompt');
    });

    it('points active tour aria-labelledby to its h3 heading while preserving #tour-tooltip for aria-describedby', () => {
      const { container } = renderAndAcceptTour(stepsWithTargets);
      const dialog = screen.getByRole('dialog', { name: 'Step A' });
      const heading = screen.getByRole('heading', { level: 3, name: 'Step A' });
      expect(dialog).toHaveAttribute('aria-modal', 'true');
      expect(dialog.getAttribute('aria-labelledby')).toBe(heading.id);
      expect(heading.id).not.toBe('tour-tooltip');

      const spotlight = container.querySelector('.tour-spotlight');
      expect(spotlight.getAttribute('aria-describedby')).toBe('tour-tooltip');
      expect(container.querySelector('#tour-tooltip')).not.toBeNull();
    });

    it('traps Tab and Shift+Tab within active tour controls while allowing modified Tab', () => {
      const stepsWithLink = [
        {
          id: 'a',
          targetSelector: '#target-a',
          title: 'Step A',
          body: 'Body A',
          link: { href: 'https://example.org/docs', label: 'Read docs' },
        },
        { id: 'b', targetSelector: '#target-b', title: 'Step B', body: 'Body B' },
      ];
      renderAndAcceptTour(stepsWithLink);

      const docsLink = screen.getByRole('link', { name: 'Read docs' });
      const skipBtn = screen.getByRole('button', { name: /skip/i });
      const backBtn = screen.getByRole('button', { name: /back/i });
      const nextBtn = screen.getByRole('button', { name: /next/i });
      expect(backBtn).toBeDisabled();

      // Escaped focus on document.body is pulled back inside.
      document.body.focus();
      const bodyTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      document.dispatchEvent(bodyTab);
      expect(bodyTab.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(docsLink);

      // Shift+Tab on first focusable wraps to last (Next).
      docsLink.focus();
      const shiftTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
      document.dispatchEvent(shiftTab);
      expect(shiftTab.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(nextBtn);

      // Tab on last focusable wraps to first (docsLink, skipping disabled Back).
      nextBtn.focus();
      const forwardTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      document.dispatchEvent(forwardTab);
      expect(forwardTab.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(docsLink);

      // Tab from non-boundary control (Skip) is not prevented.
      skipBtn.focus();
      const middleTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      document.dispatchEvent(middleTab);
      expect(middleTab.defaultPrevented).toBe(false);

      // Modified Tab passes through untouched.
      for (const mod of ['ctrlKey', 'altKey', 'metaKey']) {
        nextBtn.focus();
        const modTab = new KeyboardEvent('keydown', { key: 'Tab', [mod]: true, bubbles: true, cancelable: true });
        document.dispatchEvent(modTab);
        expect(modTab.defaultPrevented).toBe(false);
      }
    });

    it('does not intercept Alt/Ctrl/Meta/Shift modified arrow or Escape keys', () => {
      // Coach card ignores modified Escape.
      const { container } = render(<AppWithTarget steps={stepsWithTargets} />);
      for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
        const escEvent = new KeyboardEvent('keydown', { key: 'Escape', [mod]: true, bubbles: true, cancelable: true });
        document.dispatchEvent(escEvent);
        expect(escEvent.defaultPrevented).toBe(false);
        expect(container.querySelector('.tour-coach-card')).not.toBeNull();
      }

      // Active tour ignores modified ArrowLeft/ArrowRight/Escape.
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
        for (const key of ['ArrowRight', 'ArrowLeft', 'Escape']) {
          const ev = new KeyboardEvent('keydown', { key, [mod]: true, bubbles: true, cancelable: true });
          document.dispatchEvent(ev);
          expect(ev.defaultPrevented).toBe(false);
          expect(screen.getByText('Step A')).toBeInTheDocument();
        }
      }
    });

    it('restores focus to trigger button when closing active tour', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      function Harness() {
        const tour = useTour();
        return (
          <>
            <button type="button" onClick={tour.startTour}>Launch tour</button>
            <TourOverlay />
          </>
        );
      }
      render(
        <MemoryRouter initialEntries={['/analysis']}>
          <TourProvider steps={stepsWithTargets}>
            <Harness />
          </TourProvider>
        </MemoryRouter>,
      );

      const trigger = screen.getByRole('button', { name: 'Launch tour' });
      trigger.focus();
      expect(document.activeElement).toBe(trigger);

      fireEvent.click(trigger);
      expect(screen.getByText('Step A')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /skip/i }));
      expect(document.activeElement).toBe(trigger);
    });

    it('restores focus to pre-prompt element after Start tour -> Skip without clobbering ref', () => {
      function ControlledPromptApp() {
        const [showOverlay, setShowOverlay] = useState(false);
        return (
          <MemoryRouter initialEntries={['/analysis']}>
            <TourProvider steps={stepsWithTargets}>
              <button type="button" onClick={() => setShowOverlay(true)}>Before prompt</button>
              {showOverlay ? <TourOverlay /> : null}
            </TourProvider>
          </MemoryRouter>
        );
      }
      const { container } = render(<ControlledPromptApp />);
      const beforeBtn = screen.getByRole('button', { name: 'Before prompt' });
      beforeBtn.focus();
      expect(document.activeElement).toBe(beforeBtn);

      fireEvent.click(beforeBtn);
      expect(container.querySelector('.tour-coach-card')).not.toBeNull();

      // Transition isPrompting -> isActive via Start tour button.
      fireEvent.click(screen.getByRole('button', { name: /start tour/i }));
      expect(screen.getByText('Step A')).toBeInTheDocument();

      // Closing active tour restores focus to beforeBtn (not detached Start tour button).
      fireEvent.click(screen.getByRole('button', { name: /skip/i }));
      expect(document.activeElement).toBe(beforeBtn);
    });

    it('restores focus to pre-prompt element when declining the coach card', () => {
      function DeclinePromptApp() {
        const [showOverlay, setShowOverlay] = useState(false);
        return (
          <MemoryRouter initialEntries={['/analysis']}>
            <TourProvider steps={stepsWithTargets}>
              <button type="button" onClick={() => setShowOverlay(true)}>Trigger</button>
              {showOverlay ? <TourOverlay /> : null}
            </TourProvider>
          </MemoryRouter>
        );
      }
      const { container } = render(<DeclinePromptApp />);
      const triggerBtn = screen.getByRole('button', { name: 'Trigger' });
      triggerBtn.focus();
      fireEvent.click(triggerBtn);
      expect(container.querySelector('.tour-coach-card')).not.toBeNull();

      const noThanksBtn = screen.getByRole('button', { name: /no thanks/i });
      noThanksBtn.focus();
      fireEvent.click(noThanksBtn);
      expect(container.querySelector('.tour-coach-card')).toBeNull();
      expect(document.activeElement).toBe(triggerBtn);
    });

    it('restores focus when declining the coach card and falls back to #main-content when trigger detaches', () => {
      function DetachHarness() {
        const tour = useTour();
        const [mounted, setMounted] = useState(true);
        return (
          <main id="main-content" tabIndex={-1}>
            {mounted ? (
              <button
                type="button"
                onClick={() => {
                  setMounted(false);
                  tour.startTour();
                }}
              >
                Detaching trigger
              </button>
            ) : null}
            <TourOverlay />
          </main>
        );
      }
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      render(
        <MemoryRouter initialEntries={['/analysis']}>
          <TourProvider steps={stepsWithTargets}>
            <DetachHarness />
          </TourProvider>
        </MemoryRouter>,
      );

      const trigger = screen.getByRole('button', { name: 'Detaching trigger' });
      trigger.focus();
      fireEvent.click(trigger);
      expect(screen.getByText('Step A')).toBeInTheDocument();

      fireEvent.keyDown(document, { key: 'Escape' });
      expect(document.activeElement).toBe(document.getElementById('main-content'));
    });

    it('no-ops cleanly when trigger detaches and no #main-content fallback exists', () => {
      function MinimalDetachHarness() {
        const tour = useTour();
        const [mounted, setMounted] = useState(true);
        return (
          <div>
            {mounted ? (
              <button
                type="button"
                onClick={() => {
                  setMounted(false);
                  tour.startTour();
                }}
              >
                Go
              </button>
            ) : null}
            <TourOverlay />
          </div>
        );
      }
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      render(
        <MemoryRouter initialEntries={['/analysis']}>
          <TourProvider steps={stepsWithTargets}>
            <MinimalDetachHarness />
          </TourProvider>
        </MemoryRouter>,
      );

      const trigger = screen.getByRole('button', { name: 'Go' });
      trigger.focus();
      fireEvent.click(trigger);
      expect(() => fireEvent.keyDown(document, { key: 'Escape' })).not.toThrow();
    });

    it('does not intercept ArrowRight/ArrowLeft when steps is empty', () => {
      localStorage.setItem(TOUR_STORAGE_KEY, TOUR_VERSION);
      function EmptyStepsHarness() {
        const tour = useTour();
        return (
          <>
            <button type="button" onClick={tour.startTour}>Start empty</button>
            <TourOverlay />
          </>
        );
      }
      const { container } = render(
        <MemoryRouter initialEntries={['/analysis']}>
          <TourProvider steps={[]}>
            <EmptyStepsHarness />
          </TourProvider>
        </MemoryRouter>,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Start empty' }));
      expect(container.querySelector('.tour-overlay')).toBeNull();

      const rightEv = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true });
      const leftEv = new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true });
      document.dispatchEvent(rightEv);
      document.dispatchEvent(leftEv);
      expect(rightEv.defaultPrevented).toBe(false);
      expect(leftEv.defaultPrevented).toBe(false);
    });

    it('does not steal focus on initial coach-card rAF if user focused a page control', () => {
      const rafCallbacks = [];
      vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
        rafCallbacks.push(cb);
        return rafCallbacks.length;
      });
      vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/analysis']}>
          <TourProvider steps={stepsWithTargets}>
            <button type="button">Page control</button>
            <TourOverlay />
          </TourProvider>
        </MemoryRouter>,
      );

      const pageControl = screen.getByRole('button', { name: 'Page control' });
      pageControl.focus();
      expect(document.activeElement).toBe(pageControl);

      act(() => {
        while (rafCallbacks.length > 0) {
          rafCallbacks.shift()(0);
        }
      });

      expect(document.activeElement).toBe(pageControl);
    });
  });
});
