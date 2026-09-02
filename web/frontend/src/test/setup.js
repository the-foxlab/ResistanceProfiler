import '@testing-library/jest-dom';

// jsdom does not implement URL.createObjectURL / revokeObjectURL, but plotly.js
// references them at import time. Stub them so any component that pulls in a
// plotly-using subtree (e.g. DashboardView → DatabaseTab) renders under vitest.
if (typeof window.URL.createObjectURL !== 'function') {
  window.URL.createObjectURL = () => 'blob:mock';
}
if (typeof window.URL.revokeObjectURL !== 'function') {
  window.URL.revokeObjectURL = () => {};
}

// jsdom does not implement HTMLCanvasElement.prototype.getContext; plotly.js
// calls it during render. Stub it so plotly-using components mount under vitest.
if (!HTMLCanvasElement.prototype.getContext) {
  HTMLCanvasElement.prototype.getContext = () => null;
}

