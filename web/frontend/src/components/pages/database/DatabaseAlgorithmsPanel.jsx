import { groupDrugThresholds, orderedThresholdLabels } from '../../../utils';
import { InfoButton } from '../../InfoButton';

function groupEffectRules(rules) {
  if (!Array.isArray(rules) || rules.length === 0) {
    return [];
  }

  const grouped = new Map();
  for (const rule of rules) {
    const feature = String(rule.feature || '').trim();
    const reference = String(rule.reference || '').trim();
    const drug = String(rule.drug || '').trim();
    const effects = Array.isArray(rule.effect)
      ? rule.effect.map((e) => String(e).trim()).filter(Boolean)
      : [];
    const key = `${feature}|||${reference}`;
    if (!grouped.has(key)) {
      grouped.set(key, { feature, reference, effects: new Set(), drugs: new Set() });
    }
    const bucket = grouped.get(key);
    for (const eff of effects) bucket.effects.add(eff);
    if (drug) bucket.drugs.add(drug);
  }

  return [...grouped.values()]
    .map((row) => ({
      feature: row.feature,
      reference: row.reference,
      effects: [...row.effects].sort((a, b) => a.localeCompare(b)),
      drugs: [...row.drugs].sort((a, b) => a.localeCompare(b)),
    }))
    .sort((a, b) => a.feature.localeCompare(b.feature) || a.reference.localeCompare(b.reference));
}

function thresholdCell(thresholds, key) {
  if (!thresholds || typeof thresholds !== 'object') return '';
  const value = thresholds[key];
  return value === undefined || value === null ? '' : String(value);
}

function titleLabel(label) {
  return String(label || '').trim()
    ? String(label).replace(/\b\w/g, (c) => c.toUpperCase())
    : '';
}

function collectThresholdLabels(thresholdDicts) {
  const seen = new Set();
  const ordered = [];
  for (const thresholds of thresholdDicts) {
    if (!thresholds || typeof thresholds !== 'object') continue;
    for (const label of orderedThresholdLabels(thresholds)) {
      if (!seen.has(label)) {
        seen.add(label);
        ordered.push(label);
      }
    }
  }
  return ordered;
}

function ThresholdTableRow({ label, leadingHeaders, rows }) {
  const thresholdLabels = collectThresholdLabels(rows.map((row) => row.thresholds));
  return (
    <div className="database-meta-row database-meta-row-table">
      <span className="database-meta-label">{label}</span>
      <span className="database-meta-value">
        <table className="database-algorithm-table">
          <thead>
            <tr>
              {leadingHeaders.map((header) => <th key={header}>{header}</th>)}
              {thresholdLabels.map((lbl) => <th key={lbl}>{titleLabel(lbl)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                {row.leadingCells.map((cell, idx) => <td key={idx}>{cell}</td>)}
                {thresholdLabels.map((lbl) => (
                  <td key={lbl}>{thresholdCell(row.thresholds, lbl)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </span>
    </div>
  );
}

export function DatabaseAlgorithmsPanel({ algorithms }) {
  if (!algorithms) return null;
  const effectAsResistant = algorithms.effect_as_resistant;
  const drugInterp = algorithms.drug_interpretation;
  const groupedEffectRules = groupEffectRules(effectAsResistant?.rules);
  if (!effectAsResistant && !drugInterp) return null;

  return (
    <section className="database-meta-panel database-algorithms-panel" aria-label="Configured algorithms">
      <div className="database-meta-row database-meta-row-heading">
        <span className="database-meta-label database-meta-section-heading">
          <span className="database-meta-section-heading-text">Supplied Interpretation Algorithms</span>
          <InfoButton
            label="Supplied interpretation algorithms info"
            title="These settings are supplied by the active database. See the About section for algorithm details and interpretation behavior."
          />
        </span>
      </div>

      {drugInterp && drugInterp.length > 0 ? (
        <>
          <ThresholdTableRow
            label="drug_interpretation"
            leadingHeaders={['Method']}
            rows={drugInterp.map((entry, idx) => ({
              key: entry.method || idx,
              leadingCells: [String(entry.method || '').trim() || 'Not configured'],
              thresholds: entry.thresholds,
            }))}
          />
          {drugInterp.map((entry, idx) => {
            if (!entry.drug_thresholds) return null;
            const grouped = groupDrugThresholds(entry.drug_thresholds);
            if (grouped.length === 0) return null;
            const methodLabel = String(entry.method || '').trim() || idx;
            return (
              <ThresholdTableRow
                key={`override-${methodLabel}-${idx}`}
                label={`drug_interpretation overrides (${methodLabel})`}
                leadingHeaders={['Reference', 'Drugs']}
                rows={grouped.map((row, rowIdx) => ({
                  key: `${row.reference}-${row.drugs.join(',')}-${rowIdx}`,
                  leadingCells: [row.reference, row.drugs.join(', ')],
                  thresholds: row.thresholds,
                }))}
              />
            );
          })}
        </>
      ) : null}

      {groupedEffectRules.length > 0 ? (
        <div className="database-meta-row database-meta-row-table">
          <span className="database-meta-label">effect_as_resistant</span>
          <span className="database-meta-value">
            <table className="database-algorithm-table">
              <thead>
                <tr>
                  <th>Feature</th>
                  <th>Effects</th>
                  <th>Reference</th>
                  <th>Drugs</th>
                </tr>
              </thead>
              <tbody>
                {groupedEffectRules.map((rule) => (
                  <tr key={`${rule.feature}-${rule.reference}`}>
                    <td>{rule.feature}</td>
                    <td>{rule.effects.join(', ')}</td>
                    <td>{rule.reference}</td>
                    <td>{rule.drugs.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </span>
        </div>
      ) : null}
    </section>
  );
}
