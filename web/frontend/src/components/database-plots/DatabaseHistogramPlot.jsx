import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';

import { chartLabelStyle } from './shared';

export function DatabaseHistogramPlot({ plot }) {
  const axisStyle = chartLabelStyle();

  return (
    <section className="database-plot-card">
      <div className="database-plot-header">
        <h3>{plot.title}</h3>
        <p>{plot.subtitle}</p>
      </div>
      <div className="database-plot-canvas">
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={plot.bins} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="#dbe6ee" strokeDasharray="3 3" />
            <XAxis
              dataKey="label"
              tick={axisStyle}
              minTickGap={12}
              interval={0}
              label={{ value: 'IC50 bin', position: 'insideBottom', offset: -4, ...axisStyle }}
            />
            <YAxis
              allowDecimals={false}
              tick={axisStyle}
              label={{ value: 'Count', angle: -90, position: 'insideLeft', ...axisStyle }}
            />
            <Bar dataKey="count" fill="#0f766e" radius={[4, 4, 0, 0]} barSize={20} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="database-plot-footer">{plot.footer}</p>
    </section>
  );
}