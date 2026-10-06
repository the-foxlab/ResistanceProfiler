import { useEffect, useRef } from 'react';
import 'd3-transition';
import { select } from 'd3-selection';
import { VennDiagram } from 'venn.js/build/venn.js';
import { buildVennAreas, VENN_PALETTES } from './vennAreas';
import { currentTheme } from './database-plots/shared';

function regionKey(region) {
  return [...region].sort().join(',');
}

// Presentation shared by both render passes: label ink per theme, the
// palette for the current theme, and the neutral fill for intersection
// regions (venn.js would otherwise leave them black).
function themePresentation(theme) {
  return {
    palette: VENN_PALETTES[theme],
    labelColor: theme === 'dark' ? '#e4e4e7' : '#3f3f46',
    intersectionFill: theme === 'dark' ? 'rgba(39, 39, 42, 0.35)' : 'rgba(228, 228, 231, 0.45)',
  };
}

/**
 * Interactive 2- or 3-set area-proportional Venn diagram rendered with venn.js.
 *
 * Only the region counts sit inside the diagram; set identity is conveyed via
 * the hover title of each region. Clicking a region sets the active region
 * filter.
 *
 * Props:
 * - data: compare response object ({ venn: [{region, count}], databases: [...] }) or null
 * - activeRegion: array of db ids currently selected as the region filter
 * - onRegionClick: (region: string[]) => void — called when a region label is clicked
 */
export function ComparisonVenn({ data, activeRegion, onRegionClick }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!data || !containerRef.current) {
      return undefined;
    }
    const el = containerRef.current;
    const areas = buildVennAreas(data);
    if (!areas.some((area) => area.size > 0)) {
      return undefined;
    }
    const width = el.clientWidth || 640;
    const renderDiagram = () => {
      const selection = select(el);
      const chart = VennDiagram()
        .width(width)
        .height(320)
        .duration(0);
      // Invoke the chart function directly (d3's .call() returns the
      // selection, not the chart result) to capture the solved geometry.
      selection.datum(areas);
      chart(selection);
      selection.selectAll('.venn-area')
        .on('click', (area) => {
          onRegionClick?.(area.sets);
        });
    };

    renderDiagram();
    const resizeObserver = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(renderDiagram);
    resizeObserver?.observe(el);

    return () => {
      resizeObserver?.disconnect();
      select(el).selectAll('.venn-area').on('click', null);
      select(el).selectAll('*').remove();
    };
  }, [data, onRegionClick]);

  // Re-render with the theme-appropriate palette when the theme flips.
  const theme = currentTheme();
  useEffect(() => {
    if (!data || !containerRef.current) return;
    const areas = buildVennAreas(data);
    if (!areas.some((area) => area.size > 0)) return;
    applyPresentation({ el: containerRef.current, areas, theme });
  }, [data, theme]);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const activeKey = activeRegion?.length ? regionKey(activeRegion) : '';
    select(containerRef.current).selectAll('.venn-area')
      .classed('is-active', (area) => regionKey(area.sets) === activeKey);
  }, [data, activeRegion]);

  if (!data) {
    return null;
  }

  const hasRules = buildVennAreas(data).some((area) => area.size > 0);
  return (
    <div className="comparison-venn" role="img" aria-label="Rule overlap Venn diagram">
      <div ref={containerRef} />
      {!hasRules && <p className="comparison-venn-empty">No atomic rules to compare.</p>}
    </div>
  );
}

/**
 * Style every area for the current theme: fills/strokes per set colour and
 * count labels inside the regions.
 */
function applyPresentation({ el, areas, theme }) {
  const { palette, labelColor, intersectionFill } = themePresentation(theme);
  select(el).selectAll('.venn-area')
    .each(function setAreaPresentation(area) {
      const setIndex = area.sets.length === 1
        ? areas.findIndex((candidate) => candidate.sets.length === 1 && candidate.sets[0] === area.sets[0])
        : -1;
      const setColor = setIndex >= 0 ? palette[setIndex % palette.length] : null;
      const areaSelection = select(this)
        .attr('aria-label', area.description);
      let title = areaSelection.select('title');
      if (title.empty()) {
        title = areaSelection.append('title');
      }
      title.text(area.description);

      const countLabel = areaSelection.select('text.label');
      countLabel.style('fill', labelColor);

      if (setColor) {
        select(this).select('path')
          .style('fill', setColor.fill)
          .style('fill-opacity', 1)
          .style('stroke', setColor.line)
          .style('stroke-width', '1.5px');
      } else {
        // venn.js leaves intersection paths with its default black fill (it
        // only styles singletons). Give them an explicit light neutral fill
        // so the active-region fill-opacity lift reads as a highlight rather
        // than a dark blob.
        select(this).select('path')
          .style('fill', intersectionFill)
          .style('fill-opacity', 1)
          .style('stroke', 'none');
      }
    });
}
