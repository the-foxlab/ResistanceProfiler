import { afterEach, describe, expect, it } from 'vitest';
import {
  CLASSIFICATION_COLORS,
  DARK_CLASSIFICATION_COLORS,
  PIE_COLORS,
  axisTitleFont,
  baseLayout,
  classificationColorsForTheme,
  currentTheme,
  pieColorsForTheme,
  resolveTheme,
  scoreBarColorForTheme,
} from '../components/database-plots/shared';

function srgbChannel(channel) {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex) {
  const clean = hex.replace('#', '');
  const r = srgbChannel(parseInt(clean.slice(0, 2), 16));
  const g = srgbChannel(parseInt(clean.slice(2, 4), 16));
  const b = srgbChannel(parseInt(clean.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(hexA, hexB) {
  const lumA = relativeLuminance(hexA);
  const lumB = relativeLuminance(hexB);
  return (Math.max(lumA, lumB) + 0.05) / (Math.min(lumA, lumB) + 0.05);
}

describe('database-plots shared theme helpers', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme');
  });

  it('reads currentTheme from DOM data-theme and normalizes via resolveTheme', () => {
    expect(currentTheme()).toBe('light');
    document.documentElement.setAttribute('data-theme', 'dark');
    expect(currentTheme()).toBe('dark');

    expect(resolveTheme('dark')).toBe('dark');
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('unexpected')).toBe('light');
  });

  it('parameterizes baseLayout and axisTitleFont to override DOM data-theme while preserving 0-arg defaults', () => {
    document.documentElement.setAttribute('data-theme', 'light');
    expect(baseLayout().font.color).toBe('#52525b');
    expect(baseLayout().hoverlabel.bgcolor).toBe('#ffffff');
    expect(axisTitleFont().color).toBe('#71717a');

    // Explicit 'dark' overrides DOM 'light'.
    const explicitDark = baseLayout('dark');
    expect(explicitDark.font.color).toBe('#d4d4d8');
    expect(explicitDark.xaxis.gridcolor).toBe('#2e2e33');
    expect(explicitDark.xaxis.tickfont.color).toBe('#d4d4d8');
    expect(explicitDark.yaxis.gridcolor).toBe('#2e2e33');
    expect(explicitDark.yaxis.tickfont.color).toBe('#d4d4d8');
    expect(explicitDark.hoverlabel.font.color).toBe('#f4f4f5');
    expect(explicitDark.hoverlabel.bgcolor).toBe('#27272a');
    expect(explicitDark.hoverlabel.bordercolor).toBe('#3f3f46');
    expect(axisTitleFont('dark').color).toBe('#d4d4d8');

    // Explicit 'light' overrides DOM 'dark'.
    document.documentElement.setAttribute('data-theme', 'dark');
    expect(baseLayout().font.color).toBe('#d4d4d8');
    const explicitLight = baseLayout('light');
    expect(explicitLight.font.color).toBe('#52525b');
    expect(explicitLight.hoverlabel.bgcolor).toBe('#ffffff');
    expect(explicitLight.hoverlabel.bordercolor).toBe('#d4d4d8');
    expect(axisTitleFont('light').color).toBe('#71717a');
  });

  it('returns theme-appropriate pie, classification, and score bar colors', () => {
    expect(pieColorsForTheme('light')).toEqual(PIE_COLORS);
    expect(pieColorsForTheme('dark')).not.toEqual(PIE_COLORS);
    expect(pieColorsForTheme('dark')).toHaveLength(PIE_COLORS.length);

    expect(classificationColorsForTheme('light')).toEqual(CLASSIFICATION_COLORS);
    expect(classificationColorsForTheme('dark')).toEqual(DARK_CLASSIFICATION_COLORS);

    expect(scoreBarColorForTheme('light')).toBe('#0f766e');
    expect(scoreBarColorForTheme('dark')).toBe('#2dd4bf');

    for (const hex of [...Object.values(DARK_CLASSIFICATION_COLORS), scoreBarColorForTheme('dark')]) {
      expect(contrastRatio(hex, '#1c1c1f')).toBeGreaterThanOrEqual(3.0);
      expect(contrastRatio(hex, '#18181b')).toBeGreaterThanOrEqual(3.0);
    }
  });
});
