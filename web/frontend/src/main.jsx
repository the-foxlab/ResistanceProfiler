import React from 'react';
import { createRoot } from 'react-dom/client';

import { DashboardView } from './components/DashboardView';
import { useDashboardLogic } from './useDashboardLogic';
import './styles.css';

function App() {
  // Keep data/state logic in one hook and rendering in a dedicated view component.
  const logic = useDashboardLogic();
  return <DashboardView {...logic} />;
}

// StrictMode helps surface side effects and unsafe patterns during development.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
