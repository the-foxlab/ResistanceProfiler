import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

// Bump this when the tour content changes meaningfully and returning users should see it again.
export const TOUR_VERSION = 'v1';
export const TOUR_STORAGE_KEY = 'respro.tour.dismissed';

const TourContext = createContext(null);

function readDismissedVersion() {
  try {
    return localStorage.getItem(TOUR_STORAGE_KEY);
  } catch {
    // Private mode / disabled storage: treat as "not dismissed" so the tour can run in-session.
    return null;
  }
}

function writeDismissedVersion(version) {
  try {
    localStorage.setItem(TOUR_STORAGE_KEY, version);
  } catch {
    // Storage unavailable (e.g. Safari private mode). Fail open: dismiss for this session only.
  }
}

/**
 * Provides guided-tour state to the app.
 *
 * Dismissal is versioned: `respro.tour.dismissed` stores a version token (e.g. "v1"),
 * not a boolean. A user is considered dismissed only when the stored token matches the
 * current `TOUR_VERSION`. Bumping `TOUR_VERSION` re-triggers the prompt for returning users.
 *
 * On first visit (or when the stored version is stale) the provider shows a start prompt
 * (`isPrompting`) rather than auto-starting the tour. The user must accept the prompt to
 * begin; declining writes the version token and dismisses. The "Take a tour" button calls
 * `startTour()` directly and bypasses the prompt.
 *
 * `dismissTour()` is the single function that persists the dismissal and clears tour state;
 * every exit path (Skip, Esc, backdrop, last step, declining the prompt) must route through
 * it so no branch forgets to persist. All `localStorage` access is wrapped in try/catch so a
 * disabled storage never crashes the app — the tour still runs in-session.
 *
 * Note: the active step's `before` hook is fired by `TourOverlay` (which owns the real,
 * navigation-bound steps passed as a prop), not here, to avoid double-firing.
 */
export function TourProvider({ steps, children }) {
  const [isActive, setIsActive] = useState(false);
  const [isPrompting, setIsPrompting] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);

  const startTour = useCallback(() => {
    setIsPrompting(false);
    setStepIndex(0);
    setIsActive(true);
  }, []);

  const dismissTour = useCallback(() => {
    writeDismissedVersion(TOUR_VERSION);
    setIsPrompting(false);
    setIsActive(false);
    setStepIndex(0);
  }, []);

  // Declining the start prompt persists dismissal (same as any other exit path) so the
  // user is not asked again until the tour version bumps.
  const declineTour = useCallback(() => {
    dismissTour();
  }, [dismissTour]);

  // nextStep clamps at the last index. Completing the tour (an exit path that
  // persists dismissal) is done by the overlay's Finish button calling dismissTour.
  const nextStep = useCallback(() => {
    setStepIndex((prev) => Math.min(steps.length - 1, prev + 1));
  }, [steps.length]);

  const prevStep = useCallback(() => {
    setStepIndex((prev) => Math.max(0, prev - 1));
  }, []);

  // Show the start prompt on first visit (or when the stored version is stale). Runs once
  // on mount. The tour itself only starts if the user accepts the prompt.
  useEffect(() => {
    const dismissed = readDismissedVersion();
    if (dismissed !== TOUR_VERSION) {
      setIsPrompting(true);
    }
    // Intentionally run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(
    () => ({
      isActive,
      isPrompting,
      stepIndex,
      steps,
      stepCount: steps.length,
      currentStep: steps[stepIndex] ?? null,
      startTour,
      nextStep,
      prevStep,
      dismissTour,
      declineTour,
    }),
    [isActive, isPrompting, stepIndex, steps, startTour, nextStep, prevStep, dismissTour, declineTour],
  );

  return <TourContext.Provider value={value}>{children}</TourContext.Provider>;
}

export function useTour() {
  const ctx = useContext(TourContext);
  if (!ctx) {
    throw new Error('useTour must be used within a TourProvider');
  }
  return ctx;
}
