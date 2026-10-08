import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { ComparisonVenn } from './ComparisonVenn';
import { buildVennAreas, VENN_PALETTES } from './vennAreas';

if (!SVGElement.prototype.getComputedTextLength) {
  SVGElement.prototype.getComputedTextLength = function getComputedTextLength() {
    return this.textContent.length * 6;
  };
}

const COMPARE_RESPONSE = {
  databases: [
    { id: 'db1', name: 'Alpha' },
    { id: 'db2', name: 'Beta' },
  ],
  venn: [
    { region: ['db1'], count: 1 },
    { region: ['db2'], count: 1 },
    { region: ['db1', 'db2'], count: 1 },
  ],
};

describe('ComparisonVenn', () => {
  it('excludes size-0 areas when one database has no rules', () => {
    const zeroRuleDb = {
      databases: [
        { id: 'db1', name: 'Alpha' },
        { id: 'db2', name: 'Beta' },
      ],
      venn: [
        { region: ['db1'], count: 2 },
        { region: ['db1', 'db2'], count: 0 },
      ],
    };
    const areas = buildVennAreas(zeroRuleDb);
    // The empty db2 singleton and the empty intersection must not become
    // venn.js sets — degenerate geometry for size-0 circles is undefined.
    expect(areas.every((area) => area.size > 0)).toBe(true);
    expect(areas.map((area) => area.sets.join(','))).toEqual(['db1']);
  });

  it('renders 3 labeled regions for a 2-DB response', () => {
    const { container } = render(
      <ComparisonVenn data={COMPARE_RESPONSE} activeRegion={null} onRegionClick={() => {}} />,
    );
    const areas = [...container.querySelectorAll('.venn-area')];
    expect(areas).toHaveLength(3);
    // Counts stay inside the circles; set identity comes from hover titles.
    expect(areas.map((area) => area.querySelector('text.label').textContent)).toEqual([
      '1',
      '1',
      '1',
    ]);
    expect(areas.map((area) => area.querySelector('text.venn-set-name'))).toEqual([null, null, null]);
    expect(areas[2].getAttribute('aria-label')).toBe('Alpha ∩ Beta · 1');
    expect(areas[2].querySelector('title').textContent).toBe('Alpha ∩ Beta · 1');
    expect(container.querySelectorAll('.venn-area path')).toHaveLength(3);
  });

  it('exposes set names only via hover titles, not rendered text', () => {
    const { container } = render(
      <ComparisonVenn data={COMPARE_RESPONSE} activeRegion={null} onRegionClick={() => {}} />,
    );
    const alpha = container.querySelector('[data-venn-sets="db1"]');
    expect(alpha.querySelector('text.venn-set-name')).toBeNull();
    expect(alpha.querySelector('title').textContent).toBe('Alpha · 1');
  });

  it('renders 7 labeled regions for a 3-DB response', () => {
    const threeDb = {
      databases: [
        { id: 'db1', name: 'Alpha' },
        { id: 'db2', name: 'Beta' },
        { id: 'db3', name: 'Gamma' },
      ],
      venn: [
        { region: ['db1'], count: 1 },
        { region: ['db2'], count: 1 },
        { region: ['db3'], count: 1 },
        { region: ['db1', 'db2'], count: 1 },
        { region: ['db1', 'db3'], count: 1 },
        { region: ['db2', 'db3'], count: 1 },
        { region: ['db1', 'db2', 'db3'], count: 1 },
      ],
    };
    const { container } = render(
      <ComparisonVenn data={threeDb} activeRegion={null} onRegionClick={() => {}} />,
    );
    expect(container.querySelectorAll('.venn-area')).toHaveLength(7);
  });

  it('invokes onRegionClick with the region db ids when a region is clicked', () => {
    const onRegionClick = vi.fn();
    const { container } = render(
      <ComparisonVenn data={COMPARE_RESPONSE} activeRegion={null} onRegionClick={onRegionClick} />,
    );
    fireEvent.click(container.querySelector('[data-venn-sets="db1_db2"]'));
    expect(onRegionClick).toHaveBeenCalledWith(['db1', 'db2']);
  });

  it('keeps the rendered SVG when the active region changes', () => {
    const props = { data: COMPARE_RESPONSE, onRegionClick: () => {} };
    const { container, rerender } = render(<ComparisonVenn {...props} activeRegion={null} />);
    const svg = container.querySelector('svg');
    rerender(<ComparisonVenn {...props} activeRegion={['db1']} />);
    expect(container.querySelector('svg')).toBe(svg);
    expect(container.querySelector('[data-venn-sets="db1"]')).toHaveClass('is-active');
  });

  it('gives intersection paths an explicit light fill, not venn.js black', () => {
    const { container } = render(
      <ComparisonVenn data={COMPARE_RESPONSE} activeRegion={null} onRegionClick={() => {}} />,
    );
    const intersectionPath = container
      .querySelector('[data-venn-sets="db1_db2"] path');
    const fill = intersectionPath.style.fill;
    expect(fill).not.toBe('');
    expect(fill).not.toBe('rgb(0, 0, 0)');
  });

  it('renders the active intersection count in bold, keeping the theme ink', () => {
    const { container } = render(
      <ComparisonVenn data={COMPARE_RESPONSE} activeRegion={['db1', 'db2']} onRegionClick={() => {}} />,
    );
    const label = container
      .querySelector('[data-venn-sets="db1_db2"] text.label');
    expect(label.style.fill).not.toBe('rgb(255, 255, 255)');
    expect(label).toHaveClass('label');
    expect(container.querySelector('[data-venn-sets="db1_db2"]'))
      .toHaveClass('is-active');
  });

  it('provides distinct light and dark palettes with one entry per set', () => {
    for (const theme of ['light', 'dark']) {
      expect(VENN_PALETTES[theme]).toHaveLength(3);
      for (const color of VENN_PALETTES[theme]) {
        expect(color.fill).toBeTruthy();
        expect(color.line).toBeTruthy();
      }
    }
    // The dark palette must differ from the light one (brighter on dark).
    expect(VENN_PALETTES.dark.map((c) => c.fill)).not.toEqual(VENN_PALETTES.light.map((c) => c.fill));
  });

  it('converts exact-membership counts into inclusive area counts', () => {
    expect(buildVennAreas(COMPARE_RESPONSE)).toEqual([
      { sets: ['db1'], size: 2, label: '1', description: 'Alpha · 1' },
      { sets: ['db2'], size: 2, label: '1', description: 'Beta · 1' },
      { sets: ['db1', 'db2'], size: 1, label: '1', description: 'Alpha ∩ Beta · 1' },
    ]);
  });

  it('includes larger intersections in each count-proportional subset size', () => {
    const threeDb = {
      databases: [
        { id: 'db1', name: 'Alpha' },
        { id: 'db2', name: 'Beta' },
        { id: 'db3', name: 'Gamma' },
      ],
      venn: [
        { region: ['db1'], count: 2 },
        { region: ['db2'], count: 1 },
        { region: ['db3'], count: 3 },
        { region: ['db1', 'db2'], count: 4 },
        { region: ['db1', 'db3'], count: 5 },
        { region: ['db2', 'db3'], count: 6 },
        { region: ['db1', 'db2', 'db3'], count: 7 },
      ],
    };
    expect(buildVennAreas(threeDb).map((area) => area.size)).toEqual([18, 18, 21, 11, 12, 13, 7]);
  });
});
