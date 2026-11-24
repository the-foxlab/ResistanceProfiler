import { useEffect, useMemo, useRef } from 'react';
import 'd3-transition';
import { select } from 'd3-selection';
import { VennDiagram } from 'venn.js/build/venn.js';
import { buildVennAreas, regionKey, VENN_PALETTES } from './vennAreas';
import { currentTheme } from './database-plots/shared';

function themePresentation(theme) {
  return {
    palette: VENN_PALETTES[theme],
    labelColor: theme === 'dark' ? '#e4e4e7' : '#3f3f46',
    intersectionFill: theme === 'dark' ? 'rgba(39, 39, 42, 0.35)' : 'rgba(228, 228, 231, 0.45)',
  };
}

/**
 * Interactive 2- or 3-set area-proportional Venn diagram rendered with venn.js.
 */
export function ComparisonVenn({ data, activeRegion, onRegionClick }) {
  const containerRef = useRef(null);
  const areas = useMemo(() => (data ? buildVennAreas(data) : []), [data]);
  const hasRules = useMemo(() => areas.some((area) => area.size > 0), [areas]);

  useEffect(() => {
    if (!data || !containerRef.current || !hasRules) {
      return undefined;
    }
    const el = containerRef.current;
    const renderDiagram = () => {
      const selection = select(el);
      const chart = VennDiagram()
        .width(el.clientWidth || 640)
        .height(320)
        .duration(0);
      // Invoke chart directly so venn.js solves geometry in-place.
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
  }, [data, areas, hasRules, onRegionClick]);

  const theme = currentTheme();
  useEffect(() => {
    if (!data || !containerRef.current || !hasRules) return;
    applyPresentation({ el: containerRef.current, areas, theme });
  }, [data, areas, hasRules, theme]);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const activeKey = activeRegion?.length ? regionKey(activeRegion) : '';
    select(containerRef.current).selectAll('.venn-area')
      .classed('is-active', (area) => regionKey(area.sets) === activeKey);
  }, [data, activeRegion]);

  if (!data) {
    return null;
  }

  return (
    <div className="comparison-venn" role="img" aria-label="Rule overlap Venn diagram">
      <div ref={containerRef} />
      {!hasRules && <p className="comparison-venn-empty">No atomic rules to compare.</p>}
    </div>
  );
}

function applyPresentation({ el, areas, theme }) {
  const { palette, labelColor, intersectionFill } = themePresentation(theme);
  select(el).selectAll('.venn-area')
    .each(function setAreaPresentation(area) {
      const setIndex = area.sets.length === 1
        ? areas.findIndex((candidate) => candidate.sets.length === 1 && candidate.sets[0] === area.sets[0])
        : -1;
      const setColor = setIndex >= 0 ? palette[setIndex % palette.length] : null;
      const areaSelection = select(this).attr('aria-label', area.description);
      let title = areaSelection.select('title');
      if (title.empty()) {
        title = areaSelection.append('title');
      }
      title.text(area.description);

      areaSelection.select('text.label').style('fill', labelColor);

      if (setColor) {
        areaSelection.select('path')
          .style('fill', setColor.fill)
          .style('fill-opacity', 1)
          .style('stroke', setColor.line)
          .style('stroke-width', '1.5px');
      } else {
        // venn.js defaults intersection fills to black; use a neutral tint instead.
        areaSelection.select('path')
          .style('fill', intersectionFill)
          .style('fill-opacity', 1)
          .style('stroke', 'none');
      }
    });
}
