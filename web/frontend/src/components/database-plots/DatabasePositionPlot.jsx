import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';

import { chartLabelStyle, CLASSIFICATION_COLORS, CLASSIFICATION_LABELS } from './shared';

export function DatabasePositionPlot({ plot }) {
  const barGap = 6;
  const axisStyle = chartLabelStyle();
  const yAxisLabel = {
    value: 'Mutation count',
    angle: -90,
    position: 'left',
    offset: 8,
    style: {
      ...axisStyle,
      textAnchor: 'middle',
    },
  };

  return (
    <section className="database-plot-card">
      <div className="database-plot-header">
        <h3>{plot.title}</h3>
        <p>{plot.subtitle}</p>
      </div>
      <div className="database-chart-scroll">
        <div className="database-plot-canvas">
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={plot.positions} margin={{ top: 8, right: 12, left: 24, bottom: 8 }} barCategoryGap={barGap}>
              <CartesianGrid vertical={false} stroke="#dbe6ee" strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
                tick={axisStyle}
                minTickGap={18}
                // Reduce label crowding for long genes by only keeping start/end ticks.
                interval={plot.positions.length > 24 ? 'preserveStartEnd' : 0}
                label={{ value: plot.xAxisLabel || 'Amino-acid position', position: 'insideBottom', offset: -4, ...axisStyle }}
              />
              <YAxis
                allowDecimals={false}
                tick={axisStyle}
                width={48}
                label={yAxisLabel}
              />
              {plot.tones.map((tone) => (
                <Bar
                  key={tone}
                  dataKey={tone}
                  stackId={plot.hasTypedClassification ? 'rules' : undefined}
                  fill={CLASSIFICATION_COLORS[tone] || CLASSIFICATION_COLORS.count}
                  radius={[4, 4, 0, 0]}
                  barSize={12}
                  // Short animation keeps visual feedback without heavy render cost.
                  isAnimationActive
                  animationDuration={320}
                  animationEasing="ease-out"
                  name={CLASSIFICATION_LABELS[tone] || 'Rules'}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="database-plot-footer">{plot.footer}</p>
      <div className="database-plot-legend">
        {plot.tones.map((tone) => (
          <span key={tone} className="database-legend-item">
            <span className="database-legend-dot" style={{ backgroundColor: CLASSIFICATION_COLORS[tone] }} aria-hidden="true" />
            {CLASSIFICATION_LABELS[tone]}
          </span>
        ))}
      </div>
    </section>
  );
}