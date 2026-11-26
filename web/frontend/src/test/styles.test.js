import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const STYLES_DIR = path.resolve(import.meta.dirname, '../styles');

function listCssFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...listCssFiles(fullPath));
    } else if (entry.isFile() && entry.name.endsWith('.css')) {
      files.push(fullPath);
    }
  }
  return files.sort();
}

function stripCssComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

function extractCustomProps(block) {
  const map = new Map();
  for (const part of block.split(';')) {
    const colon = part.indexOf(':');
    if (colon === -1) continue;
    const prop = part.slice(0, colon).trim().toLowerCase();
    const value = part.slice(colon + 1).trim();
    if (prop.startsWith('--')) map.set(prop, value);
  }
  return map;
}

function extractBlock(css, selector) {
  const idx = css.indexOf(`${selector} {`);
  if (idx === -1) return '';
  const start = css.indexOf('{', idx) + 1;
  const end = css.indexOf('}', start);
  return end === -1 ? '' : css.slice(start, end);
}

function parseHexColor(hex) {
  const clean = hex.trim().replace(/^#/, '');
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

function relativeLuminance([r, g, b]) {
  const toLinear = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

function contrastRatio(fgHex, bgHex) {
  const l1 = relativeLuminance(parseHexColor(fgHex));
  const l2 = relativeLuminance(parseHexColor(bgHex));
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

describe('frontend CSS global invariants', () => {
  const allCssFiles = listCssFiles(STYLES_DIR);
  const styleContents = new Map(
    allCssFiles.map((filePath) => [
      path.relative(STYLES_DIR, filePath).replace(/\\/g, '/'),
      fs.readFileSync(filePath, 'utf8'),
    ]),
  );

  it('imports every CSS file under src/styles/ in styles/index.css', () => {
    const indexCss = stripCssComments(styleContents.get('index.css') || '');
    const imports = [...indexCss.matchAll(/@import\s+['"]\.\/([^'"]+)['"]\s*;/g)].map((m) => m[1]);
    const nonIndexFiles = [...styleContents.keys()].filter((rel) => rel !== 'index.css').sort();
    expect([...imports].sort()).toEqual(nonIndexFiles);
  });

  it('defines every referenced var(--*) token (excluding runtime-injected CSS vars)', () => {
    const runtimeInjectedVars = new Set(['--icon-src', '--popover-shift']);
    const definedVars = new Set(runtimeInjectedVars);
    const referencedVars = new Map();

    for (const [rel, raw] of styleContents.entries()) {
      const css = stripCssComments(raw);
      for (const m of css.matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)) {
        definedVars.add(m[1]);
      }
      for (const m of css.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)\s*(?:,[^)]+)?\)/g)) {
        if (!referencedVars.has(m[1])) referencedVars.set(m[1], rel);
      }
    }

    const undefinedTokens = [...referencedVars.entries()]
      .filter(([token]) => !definedVars.has(token))
      .map(([token, rel]) => `${token} (in ${rel})`);
    expect(undefinedTokens).toEqual([]);
  });

  it('satisfies WCAG AA (>= 4.5:1) for --color-error and --on-accent in light & dark themes', () => {
    const globalCss = stripCssComments(styleContents.get('global.css') || '');
    const lightVars = extractCustomProps(extractBlock(globalCss, ':root'));
    const darkVars = extractCustomProps(extractBlock(globalCss, ":root[data-theme='dark']"));

    expect(lightVars.get('--color-error')).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(darkVars.get('--color-error')).toMatch(/^#[0-9a-fA-F]{6}$/);

    for (const surfaceToken of ['--paper', '--surface', '--surface-strong']) {
      expect(contrastRatio(lightVars.get('--color-error'), lightVars.get(surfaceToken))).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(darkVars.get('--color-error'), darkVars.get(surfaceToken))).toBeGreaterThanOrEqual(4.5);
    }

    expect(contrastRatio(lightVars.get('--on-accent'), lightVars.get('--accent'))).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(darkVars.get('--on-accent'), darkVars.get('--accent'))).toBeGreaterThanOrEqual(4.5);
  });

  it('contains no invalid align-items: left declarations', () => {
    for (const [rel, raw] of styleContents.entries()) {
      expect(stripCssComments(raw), `Invalid align-items: left in ${rel}`).not.toMatch(/align-items\s*:\s*left\b/i);
    }
  });
});
