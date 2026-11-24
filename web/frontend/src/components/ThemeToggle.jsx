import themeSystemIcon from '../assets/theme-system.svg';
import themeLightIcon from '../assets/theme-light.svg';
import themeDarkIcon from '../assets/theme-dark.svg';

/**
 * Three-way theme switch (System · Light · Dark) using CSS-masked SVG icons.
 */
const THEME_OPTIONS = [
  { value: 'system', label: 'System', title: 'Follow system preference', icon: themeSystemIcon },
  { value: 'light', label: 'Light', title: 'Always light', icon: themeLightIcon },
  { value: 'dark', label: 'Dark', title: 'Always dark', icon: themeDarkIcon },
];

export function ThemeToggle({ preference, onChange }) {
  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {THEME_OPTIONS.map((option) => (
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
            style={{ '--icon-src': `url(${option.icon})` }}
          />
          <span className="theme-toggle-text">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
