import {
  Cell,
  Label,
  Pie,
  PieChart,
  ResponsiveContainer,
} from 'recharts';

function SummaryPieCard({ pie }) {
  return (
    <section className="database-plot-card database-summary-pie-card">
      <div className="database-plot-header">
        <h3>{pie.title}</h3>
      </div>
      {pie.slices.length > 0 ? (
        <>
          <div className="database-pie-chart-wrap">
            <ResponsiveContainer width="100%" height={230}>
              <PieChart>
                <Pie
                  data={pie.slices}
                  dataKey="count"
                  nameKey="label"
                  cx="50%"
                  cy="50%"
                  innerRadius={46}
                  outerRadius={78}
                  paddingAngle={1}
                  isAnimationActive
                  animationDuration={460}
                  animationEasing="ease-out"
                >
                  <Label
                    position="center"
                    content={({ viewBox }) => {
                      // Draw custom center text so every pie consistently shows total + metric label.
                      if (!viewBox) {
                        return null;
                      }
                      const centerX = viewBox.cx;
                      const centerY = viewBox.cy;
                      return (
                        <g>
                          <text x={centerX} y={centerY - 7} textAnchor="middle" className="database-center-value">
                            {pie.total}
                          </text>
                          <text x={centerX} y={centerY + 11} textAnchor="middle" className="database-center-label">
                            {pie.centerLabel || pie.title}
                          </text>
                        </g>
                      );
                    }}
                  />
                  {pie.slices.map((slice) => (
                    <Cell key={slice.label} fill={slice.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
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