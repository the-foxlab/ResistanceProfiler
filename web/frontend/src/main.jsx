import React from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import './styles.css';

// StrictMode helps surface side effects and unsafe patterns during development.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
