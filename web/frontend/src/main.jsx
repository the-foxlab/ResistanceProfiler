import React, { useMemo } from 'react';
import { createRoot } from 'react-dom/client';

import { DashboardView } from './components/DashboardView';
import { TourProvider } from './components/tour/TourContext';
import { buildTourSteps } from './components/tour/steps';
import { useDashboardLogic } from './useDashboardLogic';
import './styles.css';

function App() {
  // Keep data/state logic in one hook and rendering in a dedicated view component.
  const logic = useDashboardLogic();
  // Build the tour steps once with the real navigation setters (stable useState setters)
  // and pass them to the provider so its nextStep/prevStep clamping uses the real step
  // count. The overlay reads the same steps from context. (Fixes the Critical wiring bug
  // where steps={[]} made nextStep clamp to -1 and the tour died after the first Next.)
  const tourSteps = useMemo(
    () => buildTourSteps({
      setActiveMode: logic.setActiveMode,
      setActiveProfileMode: logic.setActiveProfileMode,
      setAnalyzeSubMode: logic.setAnalyzeSubMode,
    }),
    [logic.setActiveMode, logic.setActiveProfileMode, logic.setAnalyzeSubMode],
  );
  return (
    <TourProvider steps={tourSteps}>
      <DashboardView {...logic} />
    </TourProvider>
  );
}

// StrictMode helps surface side effects and unsafe patterns during development.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
