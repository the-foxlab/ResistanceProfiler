import {
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { chartLabelStyle } from './shared';

function _formatIc50Tick(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  const ic50Value = 10 ** numeric;
  if (ic50Value >= 100) {
    return String(Math.round(ic50Value));
  }
  if (ic50Value >= 10) {
    return String(Math.round(ic50Value * 10) / 10);
  }
  return String(Math.round(ic50Value * 100) / 100);
}

function _formatRawMeasurement(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return String(value);
  }
  if (numeric >= 100) {
    return String(Math.round(numeric));
  }
  if (numeric >= 10) {
    return String(Math.round(numeric * 10) / 10);
  }
  return String(Math.round(numeric * 100) / 100);
}

function _TooltipContent({ active, payload }) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }

  const point = payload[0].payload;
  if (!point) {
    return null;
  }

  return (
    <div className="database-tooltip-card">
      <p><strong>{point.drug}</strong></p>
      <p>{(point.metricLabel || 'IC50').replace('IC50', 'IC₅₀')}: {_formatRawMeasurement(point.value)}</p>
    </div>
  );
}

export function DatabaseDrugDistributionPlot({ plot }) {
  const axisStyle = chartLabelStyle();
  const majorTickSet = new Set((plot.majorTicks || []).map((tick) => Math.round(tick * 1000000) / 1000000));

  const formatMajorTick = (value) => {
    const rounded = Math.round(Number(value) * 1000000) / 1000000;
    if (!majorTickSet.has(rounded)) {
      return '';
    }
    return _formatIc50Tick(value);
  };

  return (
    <section className="database-plot-card">
      <div className="database-plot-header">
        <h3>{plot.title}</h3>
        <p>{plot.subtitle}</p>
      </div>
      <div className="database-chart-scroll">
        <div className="database-plot-canvas">
          <ResponsiveContainer width="100%" height={320}>
            <ScatterChart margin={{ top: 10, right: 16, left: 24, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="#dbe6ee" strokeDasharray="3 3" />
              <XAxis
                type="number"
                dataKey="x"
                domain={plot.xDomain}
                ticks={plot.xTicks}
                tick={axisStyle}
                tickFormatter={formatMajorTick}
                label={{
                  value: plot.xAxisLabel || 'IC₅₀ (log scale)',
                  position: 'insideBottom',
                  offset: -4,
                  ...axisStyle,
                }}
              />
              <YAxis
                type="number"
                dataKey="y"
                domain={plot.yDomain}
                ticks={plot.yTicks}
                tick={axisStyle}
                tickFormatter={(value) => plot.laneLabels[String(Math.round(value))] || ''}
                width={110}
                label={{
                  value: plot.yAxisLabel || 'Drug',
                  angle: -90,
                  position: 'left',
                  offset: 8,
                  style: {
                    ...axisStyle,
                    textAnchor: 'middle',
                  },
                }}
              />
              <Tooltip content={<_TooltipContent />} />
              <Scatter data={plot.points} name="IC50 values" isAnimationActive animationDuration={260}>
                {plot.points.map((point, idx) => (
                  <Cell key={`ic50-point-${idx}`} fill={point.color} fillOpacity={0.78} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="database-plot-footer">{plot.footer}</p>
    </section>
  );
}
