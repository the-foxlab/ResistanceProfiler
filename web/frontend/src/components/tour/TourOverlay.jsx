import { useEffect, useRef, useState } from 'react';

import { useTour } from './TourContext';

// Repositions the spotlight/tooltip on viewport changes so it stays glued to its target
// even when the app switches tabs/modes (which changes the DOM layout).
function useElementRect(selector, active) {
  const [rect, setRect] = useState(null);

  const update = () => {
    if (!active || !selector) {
      setRect(null);
      return;
    }
    const el = document.querySelector(selector);
    if (!el) {
      setRect(null);
      return;
    }
    setRect(el.getBoundingClientRect());
  };

  useEffect(() => {
    update();
    if (!active) return undefined;
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, selector]);

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
  const tooltipId = 'tour-tooltip';
  const promptId = 'tour-prompt';

  // Focus the start prompt when it appears.
  useEffect(() => {
    if (!isPrompting) return undefined;
    const raf = requestAnimationFrame(() => {
      promptRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [isPrompting]);

  // Esc on the start prompt declines it (persists dismissal).
  useEffect(() => {
    if (!isPrompting) return undefined;
    const handler = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        declineTour();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isPrompting, declineTour]);

  // Focus the tooltip when a step becomes active, and re-query the target after the
  // `before` hook has had a chance to drive navigation (which changes the DOM).
  useEffect(() => {
    if (!isActive) return undefined;
    // Defer target re-query so a `before` hook that calls setActiveMode etc. has time
    // to flush its DOM changes before we measure the target.
    const raf = requestAnimationFrame(() => {
      update();
      tooltipRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [isActive, stepIndex, update]);

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

  // Keyboard handling: Esc dismisses, arrows move steps.
  useEffect(() => {
    if (!isActive) return undefined;
    const handler = (event) => {
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
  }, [isActive, dismissTour, nextStep, prevStep]);

  if (isPrompting) {
    return (
      <div className="tour-overlay" role="dialog" aria-modal="true" aria-labelledby={promptId}>
        <div className="tour-backdrop" onClick={declineTour} aria-hidden="true" />
        <div
          ref={promptRef}
          id={promptId}
          className="tour-prompt"
          tabIndex={-1}
          role="document"
          style={{ left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }}
        >
          <div className="tour-tooltip-header">
            <h3 className="tour-tooltip-title">Take a quick tour?</h3>
          </div>
          <div className="tour-tooltip-body">
            A short guided tour will walk you through the core features — analysis modes, reports, and the
            comparison heatmap. You can restart it any time from the About tab.
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
      </div>
    );
  }

  if (!isActive || !currentStep) {
    return null;
  }

  const isLast = stepIndex >= steps.length - 1;

  // Spotlight position: follow the target if found, otherwise center the viewport.
  const spotlightStyle = rect
    ? {
        left: `${rect.left}px`,
        top: `${rect.top}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
      }
    : { left: '50%', top: '50%', width: 0, height: 0, transform: 'translate(-50%, -50%)' };

  // Tooltip position: place below the target by default, fall back to viewport center.
  const tooltipStyle = rect
    ? { left: `${rect.left}px`, top: `${rect.bottom + 12}px` }
    : { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' };

  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-labelledby={tooltipId}>
      {/* Dark backdrop with a transparent cutout around the target. */}
      <div className="tour-backdrop" onClick={dismissTour} aria-hidden="true" />
      <div
        className="tour-spotlight"
        style={spotlightStyle}
        aria-describedby={tooltipId}
        aria-hidden="true"
      />
      <div
        ref={tooltipRef}
        id={tooltipId}
        className="tour-tooltip"
        style={tooltipStyle}
        tabIndex={-1}
        role="document"
      >
        <div className="tour-tooltip-header">
          <h3 className="tour-tooltip-title">{currentStep.title}</h3>
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
