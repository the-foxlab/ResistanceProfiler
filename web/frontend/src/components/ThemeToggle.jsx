import themeSystemIcon from '../assets/theme-system.svg';
import themeLightIcon from '../assets/theme-light.svg';
import themeDarkIcon from '../assets/theme-dark.svg';

/**
 * Theme switch for the top bar. Three icon-only options:
 * System (follows prefers-color-scheme) · Light · Dark. Icons live in
 * src/assets/ as SVG files and are recoloured via the CSS mask pattern
 * (background-color: currentColor), so they follow the theme's text colour.
 */
const ICONS = {
  system: themeSystemIcon,
  light: themeLightIcon,
  dark: themeDarkIcon,
};

export function ThemeToggle({ preference, resolved, onChange }) {
  const options = [
    { value: 'system', label: 'System', title: 'Follow system preference' },
    { value: 'light', label: 'Light', title: 'Always light' },
    { value: 'dark', label: 'Dark', title: 'Always dark' },
  ];

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-label={option.label}
          aria-checked={preference === option.value}
          className={`theme-toggle-btn ${preference === option.value ? 'active' : ''}`}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          <span
            className="theme-toggle-icon"
            aria-hidden="true"
            style={{ '--icon-src': `url(${ICONS[option.value]})` }}
          />
          <span className="theme-toggle-text">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
