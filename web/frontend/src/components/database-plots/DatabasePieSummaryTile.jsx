import Plotly from 'plotly.js-dist-min';
import { useRef, useEffect, useState } from 'react';

function SummaryPieCard({ pie }) {
  const containerRef = useRef(null);
  const [plotError, setPlotError] = useState('');

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    if (pie.slices.length === 0) {
      Plotly.purge(containerRef.current);
      return;
    }

    setPlotError('');

    try {
      const trace = {
        type: 'pie',
        labels: pie.slices.map((s) => s.label),
        values: pie.slices.map((s) => s.count),
        marker: {
          colors: pie.slices.map((s) => s.color),
        },
        hole: 0.41,
        hovertemplate: '%{label}: %{value}<extra></extra>',
        textinfo: 'none',
        sort: false,
      };

      const layout = {
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
            font: { size: 22, color: '#122131', weight: 'bold' },
          },
          {
            text: pie.centerLabel || pie.title,
            x: 0.5,
            y: 0.5,
            xref: 'paper',
            yref: 'paper',
            showarrow: false,
            font: { size: 11, color: '#4c6072' },
            yshift: 18,
          },
        ],
        height: 230,
        dragmode: false,
      };

      const config = { responsive: true, displayModeBar: false, scrollZoom: false };

      Plotly.react(containerRef.current, [trace], layout, config);
    } catch (err) {
      setPlotError(String(err.message || err));
    }

    return () => {
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
    };
  }, [pie]);

  // Re-render on window resize (debounced)
  useEffect(() => {
    if (pie.slices.length === 0 || !containerRef.current) {
      return;
    }

    let timeoutId = null;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        if (containerRef.current) {
          try {
            Plotly.react(containerRef.current, containerRef.current.data, containerRef.current.layout, containerRef.current._config);
          } catch (_) {
            // ignore re-render errors on resize
          }
        }
      }, 300);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(timeoutId);
    };
  }, [pie]);

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
            {pie.slices.map((slice) => (
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
    // The row adapts responsively; each card receives one already-built pie payload.
    <div className="database-summary-row">
      {tile.pies.map((pie) => (
        <SummaryPieCard key={pie.key} pie={pie} />
      ))}
    </div>
  );
}