// Apply the theme before first paint to avoid a light flash on dark systems.
// Mirrors the resolution logic in src/hooks/useTheme.js: stored 'light'/'dark'
// wins; otherwise follow prefers-color-scheme.
(function () {
  try {
    var stored = localStorage.getItem('respro-theme');
    var theme =
      stored === 'light' || stored === 'dark'
        ? stored
        : window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
    document.documentElement.setAttribute('data-theme', theme);
  } catch {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
