import { useRef } from 'react';
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
  const buttonRefs = useRef([]);
  const matchedIndex = THEME_OPTIONS.findIndex((option) => option.value === preference);
  const focusableIndex = matchedIndex >= 0 ? matchedIndex : 0;

  const handleKeyDown = (event, index) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
      return;
    }
    let nextIndex = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (index + 1) % THEME_OPTIONS.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (index - 1 + THEME_OPTIONS.length) % THEME_OPTIONS.length;
    } else {
      return;
    }
    event.preventDefault();
    const nextOption = THEME_OPTIONS[nextIndex];
    buttonRefs.current[nextIndex]?.focus();
    onChange?.(nextOption.value);
  };

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Colour theme">
      {THEME_OPTIONS.map((option, index) => {
        const isChecked = preference === option.value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              buttonRefs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-label={option.label}
            aria-checked={isChecked}
            tabIndex={index === focusableIndex ? 0 : -1}
            className={`theme-toggle-btn ${isChecked ? 'active' : ''}`}
            title={option.title}
            onClick={() => onChange?.(option.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            <span
              className="theme-toggle-icon"
              aria-hidden="true"
              style={{ '--icon-src': `url(${option.icon})` }}
            />
            <span className="theme-toggle-text">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
