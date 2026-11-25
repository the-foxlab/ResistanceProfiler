import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import { useTour } from './TourContext';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function getFocusableElements(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.closest('[aria-hidden="true"], [inert]'),
  );
}

function restorePreviousFocus(previousEl) {
  if (
    previousEl
    && previousEl.isConnected
    && typeof previousEl.focus === 'function'
    && !previousEl.hasAttribute('disabled')
    && !previousEl.closest('[aria-hidden="true"], [inert]')
  ) {
    previousEl.focus();
    if (document.activeElement === previousEl) return;
  }
  const mainFallback = document.querySelector('main[tabindex], #main-content[tabindex]');
  if (mainFallback && typeof mainFallback.focus === 'function') {
    mainFallback.focus();
  }
}

// Repositions the spotlight/tooltip on viewport changes so it stays glued to its target
// even when the app switches tabs/modes (which changes the DOM layout).
function useElementRect(selector, active) {
  const [rect, setRect] = useState(null);

  const update = useCallback(() => {
    if (!active || !selector) {
      setRect((prev) => (prev === null ? prev : null));
      return;
    }
    const el = document.querySelector(selector);
    if (!el) {
      setRect((prev) => (prev === null ? prev : null));
      return;
    }
    const next = el.getBoundingClientRect();
    if (next.width <= 0 || next.height <= 0) {
      setRect((prev) => (prev === null ? prev : null));
      return;
    }
    setRect((prev) => {
      if (
        prev
        && prev.top === next.top
        && prev.left === next.left
        && prev.width === next.width
        && prev.height === next.height
      ) {
        return prev;
      }
      return { top: next.top, left: next.left, width: next.width, height: next.height };
    });
  }, [active, selector]);

  useEffect(() => {
    update();
    if (!active) return undefined;
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [active, update]);

  return { rect, update };
}

/**
 * Renders the spotlight + tooltip for the active tour step.
 *
 * `steps` may be passed as a prop (so a parent that owns the navigation setters can
 * build steps with bound `before` hooks); when omitted, the provider's steps are used.
 */
export function TourOverlay({ steps: stepsProp }) {
  const tour = useTour();
  const steps = stepsProp ?? tour.steps;
  const { isActive, isPrompting, stepIndex, currentStep: providerStep, nextStep, prevStep, dismissTour, declineTour, startTour } = tour;
  const currentStep = steps[stepIndex] ?? providerStep ?? null;
  const { rect, update } = useElementRect(currentStep?.targetSelector ?? null, isActive);
  const tooltipRef = useRef(null);
  const promptRef = useRef(null);
  const previousFocusRef = useRef(null);
  const wasOpenRef = useRef(false);
  const wasActiveRef = useRef(false);
  const instanceId = useId();
  const tooltipId = 'tour-tooltip';
  const promptId = 'tour-prompt';
  const tooltipTitleId = `${tooltipId}-title-${instanceId}`;
  const promptTitleId = `${promptId}-title-${instanceId}`;
  const hasActiveStep = isActive && Boolean(currentStep);
  const isOverlayOpen = isPrompting || hasActiveStep;

  // Capture focus on overlay open and restore it on close without overwriting
  // previousFocusRef during the isPrompting -> isActive transition.
  useLayoutEffect(() => {
    if (isOverlayOpen && !wasOpenRef.current) {
      const activeEl = document.activeElement;
      const startedInside = Boolean(
        activeEl && (promptRef.current?.contains(activeEl) || tooltipRef.current?.contains(activeEl)),
      );
      previousFocusRef.current =
        activeEl
        && activeEl !== document.body
        && activeEl !== document.documentElement
        && !startedInside
          ? activeEl
          : null;
      wasOpenRef.current = true;
    } else if (!isOverlayOpen && wasOpenRef.current) {
      const prev = previousFocusRef.current;
      const shouldRestore =
        wasActiveRef.current
        || document.activeElement === document.body
        || document.activeElement == null;
      previousFocusRef.current = null;
      wasOpenRef.current = false;
      if (shouldRestore) {
        restorePreviousFocus(prev);
      }
    }
    wasActiveRef.current = hasActiveStep;
  }, [isOverlayOpen, hasActiveStep]);

  // Focus the start prompt when it appears, unless focus actively moved to a page control.
  useEffect(() => {
    if (!isPrompting) return undefined;
    const activeAtSchedule = document.activeElement;
    const raf = requestAnimationFrame(() => {
      const activeNow = document.activeElement;
      if (
        activeNow !== activeAtSchedule
        && activeNow !== document.body
        && activeNow !== document.documentElement
        && !promptRef.current?.contains(activeNow)
      ) {
        return;
      }
      promptRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [isPrompting]);

  // Esc on the start prompt declines it (persists dismissal).
  useEffect(() => {
    if (!isPrompting) return undefined;
    const handler = (event) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        declineTour();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isPrompting, declineTour]);

  // Re-query the target after the `before` hook has had a chance to drive navigation.
  useEffect(() => {
    if (!isActive) return undefined;
    // Defer target re-query so a `before` hook that calls setActiveMode etc. has time
    // to flush its DOM changes before we measure the target.
    const raf = requestAnimationFrame(() => {
      update();
    });
    return () => cancelAnimationFrame(raf);
  }, [isActive, stepIndex, update]);

  // Focus the tooltip only when the tour activates or the step changes.
  useEffect(() => {
    if (!isActive) return undefined;
    const raf = requestAnimationFrame(() => {
      tooltipRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [isActive, stepIndex]);

  // Fire the active step's `before` hook. Lives here (not only in the provider) so
  // prop-supplied steps whose `before` closes over the real navigation setters run.
  const lastBeforeRef = useRef(-1);
  useEffect(() => {
    if (!isActive || lastBeforeRef.current === stepIndex) return;
    lastBeforeRef.current = stepIndex;
    if (currentStep && typeof currentStep.before === 'function') {
      currentStep.before();
    }
  }, [isActive, stepIndex, currentStep]);

  // Reset the before-hook guard when the tour deactivates so a later restart re-fires.
  useEffect(() => {
    if (!isActive) {
      lastBeforeRef.current = -1;
    }
  }, [isActive]);

  // Keyboard handling: Tab/Shift+Tab focus trap inside tooltip, Esc dismisses, arrows move steps.
  useEffect(() => {
    if (!hasActiveStep) return undefined;
    const handler = (event) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (event.key === 'Tab') {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        const container = tooltipRef.current;
        if (!container) return;
        const focusable = getFocusableElements(container);
        if (focusable.length === 0) {
          event.preventDefault();
          container.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (!container.contains(active) || active === container) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
          return;
        }
        if (event.shiftKey && active === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        dismissTour();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        nextStep();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        prevStep();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [hasActiveStep, dismissTour, nextStep, prevStep]);

  if (isPrompting) {
    // Non-blocking coach card: no backdrop, no modal semantics. It floats over
    // the page and can be dismissed with the buttons or Esc without trapping focus.
    return (
      <div
        ref={promptRef}
        id={promptId}
        className="tour-coach-card"
        tabIndex={-1}
        role="dialog"
        aria-labelledby={promptTitleId}
      >
        <div className="tour-tooltip-header">
          <h3 id={promptTitleId} className="tour-tooltip-title">Take a quick tour?</h3>
        </div>
        <div className="tour-tooltip-body">
          ResPro is a pathogen-agnostic antiviral resistance analysis framework. You can analyze and visualize antiviral resistance data from sanger and next-generation sequencing data. The short guided tour will walk you through the core features.
        </div>
        <div className="tour-tooltip-actions">
          <button type="button" className="tour-btn tour-btn-skip" onClick={declineTour}>
            No thanks
          </button>
          <div className="tour-tooltip-nav">
            <button type="button" className="tour-btn tour-btn-next" onClick={startTour}>
              Start tour
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!isActive || !currentStep) {
    return null;
  }

  const isLast = stepIndex >= steps.length - 1;

  // Spotlight padding: expand the target rect by a few px on each side so the
  // highlight is comfortably larger than the element itself.
  const SPOTLIGHT_PAD = 8;

  // Spotlight position: follow the target (padded) if found, otherwise center.
  const spotlightStyle = rect
    ? {
        left: `${rect.left - SPOTLIGHT_PAD}px`,
        top: `${rect.top - SPOTLIGHT_PAD}px`,
        width: `${rect.width + SPOTLIGHT_PAD * 2}px`,
        height: `${rect.height + SPOTLIGHT_PAD * 2}px`,
      }
    : { left: '50%', top: '50%', width: 0, height: 0, transform: 'translate(-50%, -50%)' };

  // Tooltip position: fixed at the bottom-center of the viewport so the popup stays
  // put while the spotlight moves between targets — no jumping between steps.
  const tooltipStyle = {
    left: '50%',
    bottom: '24px',
    transform: 'translateX(-50%)',
  };

  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-labelledby={tooltipTitleId}>
      {/* Dark backdrop with a transparent cutout around the target. Clicking outside
          the popup does NOT dismiss the active tour — the user must use Skip, Esc, or
          Finish to exit. */}
      <div className="tour-backdrop" aria-hidden="true" />
      {currentStep.targetSelector ? (
        <div
          className="tour-spotlight"
          style={spotlightStyle}
          aria-describedby={tooltipId}
          aria-hidden="true"
        />
      ) : null}
      <div
        ref={tooltipRef}
        id={tooltipId}
        className="tour-tooltip"
        style={tooltipStyle}
        tabIndex={-1}
        role="document"
      >
        <div className="tour-tooltip-header">
          <h3 id={tooltipTitleId} className="tour-tooltip-title">{currentStep.title}</h3>
          <span className="tour-tooltip-counter">
            Step {stepIndex + 1} / {steps.length}
          </span>
        </div>
        <div className="tour-tooltip-body">
          {currentStep.body}
          {currentStep.link ? (
            <a
              className="tour-tooltip-link"
              href={currentStep.link.href}
              target="_blank"
              rel="noreferrer"
            >
              {currentStep.link.label}
            </a>
          ) : null}
        </div>
        <div className="tour-tooltip-actions">
          <button type="button" className="tour-btn tour-btn-skip" onClick={dismissTour}>
            Skip
          </button>
          <div className="tour-tooltip-nav">
            <button
              type="button"
              className="tour-btn tour-btn-prev"
              onClick={prevStep}
              disabled={stepIndex === 0}
            >
              Back
            </button>
            {isLast ? (
              <button type="button" className="tour-btn tour-btn-finish" onClick={dismissTour}>
                Finish
              </button>
            ) : (
              <button type="button" className="tour-btn tour-btn-next" onClick={nextStep}>
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
