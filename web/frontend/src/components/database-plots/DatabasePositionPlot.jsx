import Plotly from 'plotly.js-dist-min';

import { CLASSIFICATION_COLORS, CLASSIFICATION_LABELS, baseLayout, axisTitleFont } from './shared';
import { STATIC_PLOT_CONFIG, usePlotlyChart } from './usePlotlyChart';

export function DatabasePositionPlot({ plot }) {
  const { containerRef, plotError } = usePlotlyChart({
    deps: [plot],
    render: (el) => {
      // Prefer database wording observed in plot.toneLabels; canonical map is fallback.
      const labelFor = (tone) => plot.toneLabels?.[tone] || CLASSIFICATION_LABELS[tone] || 'Rules';
      const base = baseLayout();
      const xLabels = plot.positions.map((p) => p.label);

      const traces = plot.tones.map((tone) => ({
        type: 'bar',
        x: xLabels,
        y: plot.positions.map((p) => p[tone] || 0),
        name: labelFor(tone),
        marker: {
          color: CLASSIFICATION_COLORS[tone] || CLASSIFICATION_COLORS.count,
        },
        customdata: plot.positions.map((p) => [p.rangeStart, p.rangeEnd]),
        hovertemplate: `Range: %{customdata[0]}-%{customdata[1]}<br>${labelFor(tone)}: %{y}<extra></extra>`,
      }));

      const xaxisTickConfig = plot.positions.length > 24
        ? { tickmode: 'auto', nticks: 25 }
        : { tickmode: 'auto', dtick: 1 };

      const layout = {
        ...base,
        barmode: 'stack',
        bargap: 0.15,
        xaxis: {
          ...base.xaxis,
          showgrid: false,
          title: { text: plot.xAxisLabel || 'Amino-acid position', font: axisTitleFont() },
          tickangle: -45,
          ...xaxisTickConfig,
        },
        yaxis: {
          ...base.yaxis,
          title: { text: 'Mutation count', font: axisTitleFont() },
          showgrid: true,
          rangemode: 'tozero',
        },
        margin: { l: 50, r: 12, t: 8, b: 58 },
        height: 250,
        showlegend: false,
        dragmode: false,
      };

      Plotly.react(el, traces, layout, STATIC_PLOT_CONFIG);
    },
  });

  return (
    <section className="database-plot-card">
      <div className="database-plot-header">
        <h3>{plot.title}</h3>
        <p>{plot.subtitle}</p>
      </div>
      <div className="database-chart-scroll">
        <div className="database-plot-canvas">
          <div ref={containerRef} />
        </div>
      </div>
      <p className="database-plot-footer">{plot.footer}</p>
      <div className="database-plot-legend">
        {plot.tones.map((tone) => (
          <span key={tone} className="database-legend-item">
            <span className="database-legend-dot" style={{ backgroundColor: CLASSIFICATION_COLORS[tone] }} aria-hidden="true" />
            {plot.toneLabels?.[tone] || CLASSIFICATION_LABELS[tone]}
          </span>
        ))}
      </div>
      {plotError && (
        <p className="status" style={{ color: '#c2410c' }}>Chart error: {plotError}</p>
      )}
    </section>
  );
}
