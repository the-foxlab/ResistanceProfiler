import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router';

// Bump when tour content changes meaningfully so returning users see the coach card again.
export const TOUR_VERSION = 'v2';
export const TOUR_STORAGE_KEY = 'respro.tour.dismissed';
export const TOUR_PROMPT_PATH = '/analysis';

const TourContext = createContext(null);

function readDismissedVersion() {
  try {
    return localStorage.getItem(TOUR_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeDismissedVersion(version) {
  try {
    localStorage.setItem(TOUR_STORAGE_KEY, version);
  } catch {
    // Private mode / disabled storage: dismiss for this session only.
  }
}

/**
 * Provides versioned guided-tour state. Every exit path routes through
 * `dismissTour` so dismissal persistence cannot be missed; step `before` hooks
 * are fired exclusively by `TourOverlay` to avoid double-firing.
 */
export function TourProvider({ steps, children }) {
  const { pathname } = useLocation();
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

  const nextStep = useCallback(() => {
    setStepIndex((prev) => Math.min(steps.length - 1, prev + 1));
  }, [steps.length]);

  const prevStep = useCallback(() => {
    setStepIndex((prev) => Math.max(0, prev - 1));
  }, []);

  useEffect(() => {
    if (pathname !== TOUR_PROMPT_PATH || isActive) return;
    if (readDismissedVersion() !== TOUR_VERSION) {
      setIsPrompting(true);
    }
  }, [pathname, isActive]);

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
      declineTour: dismissTour,
    }),
    [isActive, isPrompting, stepIndex, steps, startTour, nextStep, prevStep, dismissTour],
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
