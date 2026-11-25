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

/**
 * Lightweight brace-balanced CSS rule parser that tracks enclosing `@media`
 * conditions and preserves source order.
 */
function parseCssRules(rawCss) {
  const css = stripCssComments(rawCss);
  const rules = [];
  const mediaStack = [];
  let index = 0;
  let order = 0;

  while (index < css.length) {
    while (index < css.length && /\s/.test(css[index])) {
      index += 1;
    }
    if (index >= css.length) {
      break;
    }

    if (css[index] === '}') {
      if (mediaStack.length > 0) {
        mediaStack.pop();
      }
      index += 1;
      continue;
    }

    let headerStart = index;
    while (index < css.length && css[index] !== '{' && css[index] !== ';' && css[index] !== '}') {
      index += 1;
    }
    if (index >= css.length) {
      break;
    }

    const header = css.slice(headerStart, index).trim();

    if (css[index] === ';') {
      index += 1;
      continue;
    }

    if (css[index] === '}') {
      if (mediaStack.length > 0) {
        mediaStack.pop();
      }
      index += 1;
      continue;
    }

    // css[index] === '{'
    index += 1;

    if (header.startsWith('@media')) {
      mediaStack.push(header.replace(/\s+/g, ' '));
      continue;
    }

    // Skip @keyframes / @font-face / other nested blocks as atomic bodies
    let depth = 1;
    const bodyStart = index;
    while (index < css.length && depth > 0) {
      if (css[index] === '{') {
        depth += 1;
      } else if (css[index] === '}') {
        depth -= 1;
      }
      index += 1;
    }
    const body = css.slice(bodyStart, index - 1);
    rules.push({
      selector: header.replace(/\s+/g, ' '),
      body,
      media: [...mediaStack],
      order: order++,
    });
  }

  return rules;
}

function parseDeclarations(body) {
  const decls = [];
  for (const part of body.split(';')) {
    const colon = part.indexOf(':');
    if (colon === -1) continue;
    const prop = part.slice(0, colon).trim().toLowerCase();
    const value = part.slice(colon + 1).trim();
    if (prop) {
      decls.push({ prop, value });
    }
  }
  return decls;
}

function extractCustomProps(body) {
  const map = new Map();
  for (const { prop, value } of parseDeclarations(body)) {
    if (prop.startsWith('--')) {
      map.set(prop, value);
    }
  }
  return map;
}

function parseHexColor(hex) {
  const clean = hex.trim().replace(/^#/, '');
  if (!/^[0-9a-fA-F]{6}$/.test(clean)) {
    throw new Error(`Expected 6-digit hex color, got "${hex}"`);
  }
  return [
    parseInt(clean.slice(0, 2), 16),
    parseInt(clean.slice(2, 4), 16),
    parseInt(clean.slice(4, 6), 16),
  ];
}

function parseColor(value, backdropHex = null) {
  const trimmed = value.trim();
  if (trimmed.startsWith('#')) {
    return parseHexColor(trimmed);
  }
  const rgbaMatch = trimmed.match(
    /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$/i,
  );
  if (!rgbaMatch) {
    throw new Error(`Unsupported color format "${value}"`);
  }
  const r = Number(rgbaMatch[1]);
  const g = Number(rgbaMatch[2]);
  const b = Number(rgbaMatch[3]);
  const a = rgbaMatch[4] !== undefined ? Number(rgbaMatch[4]) : 1;
  if (a >= 1 || !backdropHex) {
    return [r, g, b];
  }
  const [br, bg, bb] = parseHexColor(backdropHex);
  return [
    r * a + br * (1 - a),
    g * a + bg * (1 - a),
    b * a + bb * (1 - a),
  ];
}

function relativeLuminance([r, g, b]) {
  const toLinear = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const R = toLinear(r);
  const G = toLinear(g);
  const B = toLinear(b);
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}

function contrastRatio(fgColor, bgColor) {
  const l1 = relativeLuminance(fgColor);
  const l2 = relativeLuminance(bgColor);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

function selectorSpecificity(selector) {
  // Approximate CSS specificity (a, b, c) for single selector branch
  const cleaned = selector
    .replace(/:not\(([^)]*)\)/g, ' $1 ')
    .replace(/:has\(([^)]*)\)/g, ' $1 ')
    .replace(/::[a-zA-Z-]+/g, ' __elem__ ');
  const ids = (cleaned.match(/#[a-zA-Z_-][\w-]*/g) || []).length;
  const classesAndPseudos = (
    cleaned.match(/\.[a-zA-Z_-][\w-]*|\[[^\]]+\]|:[a-zA-Z-]+(?:\([^)]*\))?/g) || []
  ).length;
  const elements = (
    cleaned
      .replace(/#[a-zA-Z_-][\w-]*/g, ' ')
      .replace(/\.[a-zA-Z_-][\w-]*/g, ' ')
      .replace(/\[[^\]]+\]/g, ' ')
      .replace(/:[a-zA-Z-]+(?:\([^)]*\))?/g, ' ')
      .match(/\b[a-zA-Z][a-zA-Z0-9-]*\b/g) || []
  ).length;
  return [ids, classesAndPseudos, elements];
}

function compareSpecificity(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function readStyle(relPath) {
  return fs.readFileSync(path.join(STYLES_DIR, relPath), 'utf8');
}

describe('frontend CSS design system & M5a invariants', () => {
  const allCssFiles = listCssFiles(STYLES_DIR);
  const styleContents = new Map(
    allCssFiles.map((filePath) => [
      path.relative(STYLES_DIR, filePath).replace(/\\/g, '/'),
      fs.readFileSync(filePath, 'utf8'),
    ]),
  );

  it('imports every CSS file under src/styles/ in styles/index.css', () => {
    const indexCss = stripCssComments(readStyle('index.css'));
    const imports = [...indexCss.matchAll(/@import\s+['"]\.\/([^'"]+)['"]\s*;/g)].map(
      (m) => m[1],
    );

    const nonIndexFiles = [...styleContents.keys()].filter((rel) => rel !== 'index.css').sort();
    expect([...imports].sort()).toEqual(nonIndexFiles);
    for (const rel of imports) {
      expect(fs.existsSync(path.join(STYLES_DIR, rel))).toBe(true);
    }
  });

  it('defines --color-error in light & dark themes and satisfies WCAG AA (>= 4.5:1)', () => {
    const globalRules = parseCssRules(readStyle('global.css'));
    const rootRule = globalRules.find((r) => r.selector === ':root' && r.media.length === 0);
    const darkRule = globalRules.find(
      (r) => r.selector === ":root[data-theme='dark']" && r.media.length === 0,
    );

    expect(rootRule).toBeDefined();
    expect(darkRule).toBeDefined();

    const lightVars = extractCustomProps(rootRule.body);
    const darkVars = extractCustomProps(darkRule.body);

    expect(lightVars.get('--color-error')).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(darkVars.get('--color-error')).toMatch(/^#[0-9a-fA-F]{6}$/);

    const lightError = parseColor(lightVars.get('--color-error'));
    const darkError = parseColor(darkVars.get('--color-error'));

    for (const surfaceToken of [
      '--paper',
      '--paper-soft',
      '--surface',
      '--surface-strong',
      '--neutral-bg',
      '--accent-tint',
      '--resistant-bg',
    ]) {
      const lightBg = parseColor(lightVars.get(surfaceToken));
      expect(
        contrastRatio(lightError, lightBg),
        `light --color-error vs ${surfaceToken}`,
      ).toBeGreaterThanOrEqual(4.5);
    }

    const darkSolidSurfaces = [
      '--paper',
      '--paper-soft',
      '--surface',
      '--surface-strong',
      '--neutral-bg',
    ];
    for (const surfaceToken of darkSolidSurfaces) {
      const darkBg = parseColor(darkVars.get(surfaceToken));
      expect(
        contrastRatio(darkError, darkBg),
        `dark --color-error vs ${surfaceToken}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
    for (const alphaSurface of ['--accent-tint', '--resistant-bg']) {
      for (const backdrop of ['--surface', '--surface-strong', '--paper']) {
        const compositedBg = parseColor(darkVars.get(alphaSurface), darkVars.get(backdrop));
        expect(
          contrastRatio(darkError, compositedBg),
          `dark --color-error vs ${alphaSurface} over ${backdrop}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }

    // Verify --on-accent contrast against --accent and --accent-hover in both themes
    for (const [label, vars] of [
      ['light', lightVars],
      ['dark', darkVars],
    ]) {
      const onAccent = parseColor(vars.get('--on-accent'));
      for (const accentToken of ['--accent', '--accent-hover']) {
        const accentBg = parseColor(vars.get(accentToken));
        expect(
          contrastRatio(onAccent, accentBg),
          `${label} --on-accent vs ${accentToken}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('contains zero undefined CSS custom properties across all 13 stylesheets', () => {
    const definedProps = new Set(['--icon-src']); // Runtime inline var from React components

    for (const rawCss of styleContents.values()) {
      for (const rule of parseCssRules(rawCss)) {
        for (const prop of extractCustomProps(rule.body).keys()) {
          definedProps.add(prop);
        }
      }
    }

    const undefinedRefs = [];
    for (const [relPath, rawCss] of styleContents.entries()) {
      const stripped = stripCssComments(rawCss);
      for (const match of stripped.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)/g)) {
        const varName = match[1];
        if (!definedProps.has(varName)) {
          undefinedRefs.push(`${relPath}: ${varName}`);
        }
      }
    }

    expect(undefinedRefs).toEqual([]);
  });

  it('has zero hardcoded error hexes outside global.css :root, zero invalid alignments, and zero bare outline: none/0', () => {
    const globalRules = parseCssRules(readStyle('global.css'));
    const globalRootOnlyRemoved = globalRules
      .filter((r) => r.selector !== ':root')
      .map((r) => r.body)
      .join('\n');

    for (const [relPath, rawCss] of styleContents.entries()) {
      const stripped = stripCssComments(rawCss);

      if (relPath !== 'global.css') {
        expect(stripped, `${relPath} must not contain #b91c1c`).not.toMatch(/#b91c1c\b/i);
        expect(stripped, `${relPath} must not contain #c2410c`).not.toMatch(/#c2410c\b/i);
      } else {
        expect(globalRootOnlyRemoved).not.toMatch(/#b91c1c\b/i);
        expect(globalRootOnlyRemoved).not.toMatch(/#c2410c\b/i);
      }

      expect(stripped, `${relPath} invalid alignment`).not.toMatch(
        /\b(?:align-items|justify-content)\s*:\s*left\b/i,
      );
      expect(stripped, `${relPath} bare outline: none/0`).not.toMatch(
        /\boutline\s*:\s*(?:none|0)\b/i,
      );
    }
  });

  it('fixes layout.css collapsed sidebar width, skip-link contrast, WA-003, and S5-F4 specificity', () => {
    const rules = parseCssRules(readStyle('layout.css'));

    const collapsedMain = rules.find(
      (r) =>
        r.media.length === 0 &&
        r.selector.includes('.dashboard-body:has(.sidebar-rail.collapsed) .dashboard-main'),
    );
    expect(collapsedMain).toBeDefined();
    const collapsedDecls = Object.fromEntries(
      parseDeclarations(collapsedMain.body).map((d) => [d.prop, d.value]),
    );
    expect(collapsedDecls['margin-left']).toBe('68px');
    expect(collapsedDecls.width).toBe('calc(100% - 68px)');

    const skipLink = rules.find((r) => r.media.length === 0 && r.selector === '.skip-link');
    expect(skipLink).toBeDefined();
    expect(skipLink.body).toMatch(/color\s*:\s*var\(--on-accent\)/);
    expect(skipLink.body).not.toContain('--accent-contrast');

    // M3 WA-003 preserved
    const mobileBrandWrap = rules.find(
      (r) =>
        r.media.some((m) => m.includes('max-width: 820px')) &&
        r.selector === '.brand-logo-wrap',
    );
    expect(mobileBrandWrap).toBeDefined();
    expect(mobileBrandWrap.body).not.toMatch(/\bgrid-(?:column|row)\s*:/);

    // S5-F4: no <=620px .header-db-select rule shadowed by 820px (0,3,0) rule
    const max820Select = rules.find(
      (r) =>
        r.media.some((m) => m.includes('max-width: 820px')) &&
        r.selector.includes('.workspace-subheader .topbar-db-bar .header-db-select'),
    );
    expect(max820Select).toBeDefined();
    const max820Spec = selectorSpecificity(max820Select.selector);

    const max620SelectRules = rules.filter(
      (r) =>
        r.media.some((m) => m.includes('max-width: 620px')) &&
        r.selector.includes('.header-db-select'),
    );
    for (const rule of max620SelectRules) {
      const spec = selectorSpecificity(rule.selector);
      expect(
        compareSpecificity(spec, max820Spec),
        `Shadowed rule in @media (max-width: 620px): ${rule.selector}`,
      ).toBeGreaterThanOrEqual(0);
    }
  });

  it('fixes components.css error color and flex alignment', () => {
    const rules = parseCssRules(readStyle('components.css'));
    const clearBtnHover = rules.find((r) => r.selector === '.batch-clear-all-btn:hover');
    expect(clearBtnHover).toBeDefined();
    expect(clearBtnHover.body).toMatch(/color\s*:\s*var\(--color-error\)/);

    const outputHeader = rules.find((r) => r.selector === '.workspace-output-header');
    expect(outputHeader).toBeDefined();
    expect(outputHeader.body).toMatch(/align-items\s*:\s*flex-start/);
  });

  it('fixes analyze.css --ink hover, 980px/820px VCF grid order & areas, and preserves WA-003', () => {
    const rules = parseCssRules(readStyle('pages/analyze.css'));

    const cancelHover = rules.find((r) => r.selector === '.upload-cancel-btn:hover');
    expect(cancelHover).toBeDefined();
    expect(cancelHover.body).toMatch(/color\s*:\s*var\(--ink\)/);

    const rules980 = rules.filter((r) => r.media.some((m) => m.includes('max-width: 980px')));
    const row980 = rules980.find((r) => r.selector === '.profile-upload-row');
    const vcf980 = rules980.find((r) => r.selector === '.profile-upload-row-vcf');
    expect(row980).toBeDefined();
    expect(vcf980).toBeDefined();
    expect(vcf980.order).toBeGreaterThan(row980.order);
    expect(vcf980.body).toMatch(/grid-template-columns\s*:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);

    const rules820 = rules.filter((r) => r.media.some((m) => m.includes('max-width: 820px')));
    const vcf820Cols = rules820.find(
      (r) =>
        r.selector.split(',').some((s) => s.trim() === '.profile-upload-row-vcf') &&
        /grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/.test(r.body),
    );
    const vcf820Areas = rules820.find(
      (r) =>
        r.selector.split(',').some((s) => s.trim() === '.profile-upload-row-vcf') &&
        /grid-template-areas\s*:/.test(r.body),
    );
    expect(vcf820Cols).toBeDefined();
    expect(vcf820Areas).toBeDefined();
    expect(vcf820Areas.body).toMatch(
      /'vcf-file'\s+'vcf-reference'\s+'vcf-bam'\s+'vcf-sample'\s+'vcf-settings'/,
    );

    const rules620 = rules.filter((r) => r.media.some((m) => m.includes('max-width: 620px')));
    const pill620 = rules620.find(
      (r) =>
        r.selector.includes('.analyze-submode-btn') &&
        r.selector.includes('.analyze-submode-switch-option'),
    );
    expect(pill620).toBeDefined();
    expect(pill620.body).toMatch(/white-space\s*:\s*normal/);
    expect(pill620.body).toMatch(/text-align\s*:\s*center/);
  });

  it('fixes compare.css error token and preserves all Venn dark/active rules', () => {
    const rules = parseCssRules(readStyle('pages/compare.css'));
    const compareError = rules.find((r) => r.selector === '.compare-error');
    expect(compareError).toBeDefined();
    expect(compareError.body).toMatch(/color\s*:\s*var\(--color-error\)/);

    const vennActivePath = rules.find(
      (r) => r.selector === '.comparison-venn .venn-area.is-active path',
    );
    const darkVennLabel = rules.find(
      (r) => r.selector === ":root[data-theme='dark'] .comparison-venn .venn-area text.label",
    );
    const darkVennActivePath = rules.find(
      (r) =>
        r.selector ===
        ":root[data-theme='dark'] .comparison-venn .venn-area.is-active path",
    );
    expect(vennActivePath?.body).toMatch(/stroke-width\s*:\s*3px\s*!important/);
    expect(darkVennLabel?.body).toMatch(/fill\s*:\s*var\(--muted-strong\)/);
    expect(darkVennActivePath?.body).toMatch(/stroke-width\s*:\s*3px\s*!important/);
  });

  it('fixes database.css @media (max-width: 980px) selector specificity to >= (0,2,0)', () => {
    const rules = parseCssRules(readStyle('pages/database.css'));
    const rules980 = rules.filter((r) => r.media.some((m) => m.includes('max-width: 980px')));

    const scoreGrid980 = rules980.find((r) =>
      r.selector
        .split(',')
        .some((s) => s.trim() === '.database-reference-section-score .database-reference-plot-grid'),
    );
    expect(scoreGrid980).toBeDefined();
    expect(scoreGrid980.body).toMatch(/grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/);

    const algoRow980 = rules980.find((r) =>
      r.selector
        .split(',')
        .some((s) => s.trim() === '.database-algorithms-panel .database-meta-row'),
    );
    expect(algoRow980).toBeDefined();
    expect(algoRow980.body).toMatch(/grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/);

    const rules620 = rules.filter((r) => r.media.some((m) => m.includes('max-width: 620px')));
    const scoreFlex620 = rules620.find(
      (r) => r.selector === '.database-reference-section-score .database-reference-plot-grid',
    );
    expect(scoreFlex620).toBeDefined();
    expect(scoreFlex620.body).toMatch(/display\s*:\s*flex/);
  });

  it('defines mutations.css dark-mode-safe CSS mask icons and explicit reset-button color', () => {
    const rules = parseCssRules(readStyle('pages/mutations.css'));

    const resetBtn = rules.find((r) => r.media.length === 0 && r.selector === '.mutation-reset-button');
    expect(resetBtn).toBeDefined();
    expect(resetBtn.body).toMatch(/(?:^|;|\s)color\s*:\s*var\(--(?:muted-strong|ink)\)/);

    // Keep img sizing rules until M5d switches JSX
    expect(rules.some((r) => r.selector === '.mutation-search img')).toBe(true);
    expect(rules.some((r) => r.selector === '.mutation-reset-button img')).toBe(true);

    const sharedMask = rules.find(
      (r) =>
        r.media.length === 0 &&
        r.selector.includes('.mutation-search-icon') &&
        r.selector.includes('.mutation-reset-icon'),
    );
    expect(sharedMask).toBeDefined();
    expect(sharedMask.body).toMatch(/background-color\s*:\s*currentColor/);

    const searchIcon = rules.find(
      (r) => r.media.length === 0 && r.selector === '.mutation-search-icon',
    );
    const resetIcon = rules.find(
      (r) => r.media.length === 0 && r.selector === '.mutation-reset-icon',
    );
    expect(searchIcon).toBeDefined();
    expect(searchIcon.body).toMatch(/mask-image\s*:\s*url\(['"]\.\.\/\.\.\/assets\/search\.svg['"]\)/);
    expect(searchIcon.body).toMatch(
      /-webkit-mask-image\s*:\s*url\(['"]\.\.\/\.\.\/assets\/search\.svg['"]\)/,
    );
    expect(resetIcon).toBeDefined();
    expect(resetIcon.body).toMatch(
      /mask-image\s*:\s*url\(['"]\.\.\/\.\.\/assets\/reset_filter\.svg['"]\)/,
    );
    expect(resetIcon.body).toMatch(
      /-webkit-mask-image\s*:\s*url\(['"]\.\.\/\.\.\/assets\/reset_filter\.svg['"]\)/,
    );
  });

  it('covers every transitioning/animated stylesheet with @media (prefers-reduced-motion: reduce)', () => {
    const motionFiles = [
      'global.css',
      'components.css',
      'layout.css',
      'tour.css',
      'pages/analyze.css',
      'pages/database.css',
      'pages/home.css',
      'pages/mutations.css',
    ];

    for (const relPath of motionFiles) {
      const rules = parseCssRules(readStyle(relPath));
      const reducedRules = rules.filter((r) =>
        r.media.some((m) => m.includes('prefers-reduced-motion: reduce')),
      );
      expect(reducedRules.length, `${relPath} missing prefers-reduced-motion: reduce`).toBeGreaterThan(
        0,
      );

      // Every selector in this file that declares transition or animation outside reduced-motion
      // must appear inside reduced-motion rules.
      const reducedSelectorsText = reducedRules.map((r) => r.selector).join(', ');
      const animatedSelectors = rules
        .filter(
          (r) =>
            !r.media.some((m) => m.includes('prefers-reduced-motion: reduce')) &&
            (/\btransition\s*:/.test(r.body) || /\banimation\s*:/.test(r.body)),
        )
        .flatMap((r) => r.selector.split(',').map((s) => s.trim()));

      for (const sel of animatedSelectors) {
        expect(
          reducedSelectorsText.includes(sel),
          `${relPath}: selector "${sel}" missing from @media (prefers-reduced-motion: reduce)`,
        ).toBe(true);
      }
    }

    // Explicitly verify .spinner disables animation under reduced motion
    const componentRules = parseCssRules(readStyle('components.css'));
    const spinnerReduced = componentRules.find(
      (r) =>
        r.media.some((m) => m.includes('prefers-reduced-motion: reduce')) &&
        r.selector.includes('.spinner'),
    );
    expect(spinnerReduced).toBeDefined();
    expect(spinnerReduced.body).toMatch(/animation\s*:\s*none/);
  });

  it('provides @media (forced-colors: active) rules for focus indicators, active controls, and SVG masks', () => {
    const forcedColorFiles = [
      'global.css',
      'layout.css',
      'components.css',
      'tour.css',
      'pages/analyze.css',
      'pages/compare.css',
      'pages/database.css',
      'pages/mutations.css',
    ];

    for (const relPath of forcedColorFiles) {
      const rules = parseCssRules(readStyle(relPath));
      const forcedRules = rules.filter((r) =>
        r.media.some((m) => m.includes('forced-colors: active')),
      );
      expect(forcedRules.length, `${relPath} missing forced-colors: active`).toBeGreaterThan(0);
    }

    const mutationForced = parseCssRules(readStyle('pages/mutations.css')).filter((r) =>
      r.media.some((m) => m.includes('forced-colors: active')),
    );
    const searchIconForced = mutationForced.find((r) => r.selector === '.mutation-search-icon');
    const resetIconForced = mutationForced.find((r) => r.selector === '.mutation-reset-icon');
    const searchFocusForced = mutationForced.find(
      (r) => r.selector === '.mutation-search:focus-within',
    );
    expect(searchIconForced?.body).toMatch(/forced-color-adjust\s*:\s*none/);
    expect(searchIconForced?.body).toMatch(/background-color\s*:\s*CanvasText/);
    expect(resetIconForced?.body).toMatch(/forced-color-adjust\s*:\s*none/);
    expect(resetIconForced?.body).toMatch(/background-color\s*:\s*ButtonText/);
    expect(searchFocusForced?.body).toMatch(/outline\s*:\s*2px solid Highlight/);
  });
});
