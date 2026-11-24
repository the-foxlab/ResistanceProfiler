import Plotly from 'plotly.js-dist-min';
import { useContext, useMemo } from 'react';

import { baseLayout, pieColorsForTheme } from './shared';
import { STATIC_PLOT_CONFIG, usePlotlyChart } from './usePlotlyChart';
import { ThemeContext } from '../../hooks/useTheme';

function SummaryPieCard({ pie }) {
  const theme = useContext(ThemeContext);
  const slices = useMemo(() => {
    const colors = pieColorsForTheme(theme);
    return pie.slices.map((slice, index) => ({
      ...slice,
      color: colors[index % colors.length],
    }));
  }, [pie.slices, theme]);

  const { containerRef, plotError } = usePlotlyChart({
    enabled: pie.slices.length > 0,
    deps: [pie, slices],
    render: (el, activeTheme) => {
      const trace = {
        type: 'pie',
        labels: slices.map((s) => s.label),
        values: slices.map((s) => s.count),
        marker: {
          colors: slices.map((s) => s.color),
          line: { color: activeTheme === 'dark' ? '#1c1c1f' : '#ffffff', width: 2 },
        },
        hole: 0.41,
        hovertemplate: '%{label}: %{value}<extra></extra>',
        textinfo: 'none',
        sort: false,
      };

      const layout = {
        ...baseLayout(),
        autosize: true,
        showlegend: false,
        margin: { l: 10, r: 10, t: 10, b: 10 },
        annotations: [
          {
            text: String(pie.total),
            x: 0.5,
            y: 0.5,
            xref: 'paper',
            yref: 'paper',
            showarrow: false,
            font: { size: 22, color: activeTheme === 'dark' ? '#f4f4f5' : '#18181b', weight: 'bold' },
          },
          {
            text: pie.centerLabel || pie.title,
            x: 0.5,
            y: 0.5,
            xref: 'paper',
            yref: 'paper',
            showarrow: false,
            font: { size: 11, color: activeTheme === 'dark' ? '#d4d4d8' : '#71717a' },
            yshift: 18,
          },
        ],
        height: 230,
        dragmode: false,
      };

      Plotly.react(el, [trace], layout, STATIC_PLOT_CONFIG);
    },
  });

  return (
    <section className="database-plot-card database-summary-pie-card">
      <div className="database-plot-header">
        <h3>{pie.title}</h3>
      </div>
      {pie.slices.length > 0 ? (
        <>
          <div className="database-pie-chart-wrap">
            <div ref={containerRef} style={{ width: '100%' }} />
          </div>
          <div className="database-pie-legend" aria-label={`${pie.title} legend`}>
            {slices.map((slice) => (
              <div key={slice.label} className="database-pie-row">
                <span className="database-legend-item">
                  <span className="database-legend-dot" style={{ backgroundColor: slice.color }} aria-hidden="true" />
                  {slice.label}
                </span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="status">No data available.</p>
      )}
      {plotError && (
        <p className="status" style={{ color: '#c2410c' }}>Chart error: {plotError}</p>
      )}
    </section>
  );
}

export function DatabasePieSummaryRow({ tile }) {
  return (
    <div className="database-summary-row">
      {tile.pies.map((pie) => (
        <SummaryPieCard key={pie.key} pie={pie} />
      ))}
    </div>
  );
}
