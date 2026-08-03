import { useEffect, useMemo, useState } from 'react';

import infoIconSrc from '../../assets/info.svg';
import { isPopulated, groupDrugThresholds, formatAlgorithmThresholds } from '../../utils';
import { buildDatabasePlots } from '../database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from '../database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from '../database-plots/DatabasePositionPlot';
import { DatabaseDrugDistributionPlot } from '../database-plots/DatabaseDrugDistributionPlot';

function _renderPmidLinks(value) {
  const pmidText = String(value);
  const pmids = Array.from(new Set(pmidText.match(/\d+/g) || []));
  if (pmids.length === 0) {
    return pmidText;
  }
  return (
    <>
      {pmids.map((pmid, index) => (
        <span key={pmid}>
          {index > 0 ? ', ' : ''}
          <a
            href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
            target="_blank"
            rel="noreferrer"
          >
            PMID:{pmid}
          </a>
        </span>
      ))}
    </>
  );
}

function _renderDatabaseMetaValue(entry) {
  const valueText = String(entry.value).trim();
  if (entry.key === 'website') {
    return (
      <a href={valueText} target="_blank" rel="noreferrer">{valueText}</a>
    );
  }
  if (entry.key === 'publication_doi') {
    const doi = valueText.replace(/^https?:\/\/doi\.org\//i, '');
    return (
      <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer">
        {valueText}
      </a>
    );
  }
  if (entry.key === 'publication_pmid') {
    return _renderPmidLinks(valueText);
  }
  if (entry.key === 'contact' && valueText.includes('@') && !valueText.includes('mailto:')) {
    return (
      <a href={`mailto:${valueText}`}>{valueText}</a>
    );
  }
  return entry.value;
}

function _groupEffectRules(rules) {
  if (!Array.isArray(rules) || rules.length === 0) {
    return [];
  }

  const grouped = new Map();
  for (const rule of rules) {
    const feature = String(rule.feature || '').trim();
    const reference = String(rule.reference || '').trim();
    const drug = String(rule.drug || '').trim();
    const effects = Array.isArray(rule.effect) ? rule.effect.map((e) => String(e).trim()).filter(Boolean) : [];
    const key = `${feature}|||${reference}`;
    if (!grouped.has(key)) {
      grouped.set(key, { feature, reference, effects: new Set(), drugs: new Set() });
    }
    for (const eff of effects) {
      grouped.get(key).effects.add(eff);
    }
    if (drug) {
      grouped.get(key).drugs.add(drug);
    }
  }

  return [...grouped.values()]
    .map((row) => ({
      feature: row.feature,
      reference: row.reference,
      effects: [...row.effects].sort((a, b) => a.localeCompare(b)),
      drugs: [...row.drugs].sort((a, b) => a.localeCompare(b)),
    }))
    .sort((a, b) => {
      const featureOrder = a.feature.localeCompare(b.feature);
      if (featureOrder !== 0) {
        return featureOrder;
      }
      return a.reference.localeCompare(b.reference);
    });
}

function _renderDrugThresholdsOverrides(drugThresholds, label) {
  const grouped = groupDrugThresholds(drugThresholds);
  if (grouped.length === 0) {
    return null;
  }
  return (
    <div className="database-meta-row database-meta-row-table">
      <span className="database-meta-label">{label}</span>
      <span className="database-meta-value">
        <table className="database-algorithm-table">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Drugs</th>
              <th>Thresholds</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map((row, idx) => (
              <tr key={`${row.reference}-${row.drugs.join(',')}-${idx}`}>
                <td>{row.reference}</td>
                <td>{row.drugs.join(', ')}</td>
                <td>{formatAlgorithmThresholds(row.thresholds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </span>
    </div>
  );
}

function _renderDatabaseAlgorithms(algorithms) {
  if (!algorithms) return null;
  const effectAsResistant = algorithms.effect_as_resistant;
  const drugInterp = algorithms.drug_interpretation;
  const ic50Thresholds = algorithms.ic50_thresholds;
  const groupedEffectRules = _groupEffectRules(effectAsResistant?.rules);
  if (!effectAsResistant && !drugInterp && !ic50Thresholds) return null;

  return (
    <section className="database-meta-panel database-algorithms-panel" aria-label="Configured algorithms">
      <div className="database-meta-row database-meta-row-heading">
        <span className="database-meta-label database-meta-section-heading">
          <span className="database-meta-section-heading-text">Supplied Interpretation Algorithms</span>
          <button
            type="button"
            className="input-info-btn"
            aria-label="Supplied interpretation algorithms info"
            title="These settings are supplied by the active database. See the About section for algorithm details and interpretation behavior."
          >
            <img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" />
          </button>
        </span>
      </div>
      {ic50Thresholds ? (
        <div className="database-meta-row database-meta-row-table">
          <span className="database-meta-label">ic50_thresholds</span>
          <span className="database-meta-value">
            <table className="database-algorithm-table">
              <thead>
                <tr>
                  <th>Use</th>
                  <th>Thresholds</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{String(ic50Thresholds.use || '').trim() || 'Not configured'}</td>
                  <td>{formatAlgorithmThresholds(ic50Thresholds.thresholds)}</td>
                </tr>
              </tbody>
            </table>
          </span>
        </div>
      ) : null}
      {ic50Thresholds?.drug_thresholds
        ? _renderDrugThresholdsOverrides(ic50Thresholds.drug_thresholds, 'ic50_thresholds overrides')
        : null}
      {drugInterp && drugInterp.length > 0 ? (
        <>
          <div className="database-meta-row database-meta-row-table">
            <span className="database-meta-label">drug_interpretation</span>
            <span className="database-meta-value">
              <table className="database-algorithm-table">
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Thresholds</th>
                  </tr>
                </thead>
                <tbody>
                  {drugInterp.map((entry, idx) => (
                    <tr key={entry.method || idx}>
                      <td>{String(entry.method || '').trim() || 'Not configured'}</td>
                      <td>{formatAlgorithmThresholds(entry.thresholds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </span>
          </div>
          {drugInterp.map((entry, idx) =>
            entry.drug_thresholds
              ? _renderDrugThresholdsOverrides(
                  entry.drug_thresholds,
                  `drug_interpretation overrides (${String(entry.method || '').trim() || idx})`,
                )
              : null,
          )}
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

export function DatabaseTab({
  rules,
  formulaRules,
  mutationPlotMeta,
  selectedDatabase,
}) {
  // These controls only affect database charts, not mutation browsing or profiling.
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);

  const {
    summaryTile,
    ic50Sections,
    detailSections,
    phenotypeMode,
    binSize,
  } = useMemo(
    () => buildDatabasePlots(
      rules,
      formulaRules,
      mutationPlotMeta,
      requestedPhenotypeMode,
      requestedBinSize,
    ),
    [
      rules,
      formulaRules,
      mutationPlotMeta,
      requestedPhenotypeMode,
      requestedBinSize,
    ]
  );

  const activePhenotypeMode = phenotypeMode.activeMode;

  const databaseInfoEntries = useMemo(() => {
    if (!selectedDatabase) {
      return [];
    }

    const metadata = selectedDatabase.metadata || {};
    const entries = [
      { key: 'display_name', label: 'Database name', value: selectedDatabase.display_name },
      { key: 'uuid', label: 'UUID', value: selectedDatabase.uuid },
      { key: 'created_at', label: 'Created at', value: selectedDatabase.created_at },
      { key: 'schema_version', label: 'Schema version', value: selectedDatabase.schema_version },
      { key: 'maintainers', label: 'Maintainers', value: metadata.maintainers },
      { key: 'contact', label: 'Contact', value: metadata.contact },
      { key: 'publication_pmid', label: 'Publication PMID', value: metadata.publication_pmid },
      { key: 'publication_doi', label: 'Publication DOI', value: metadata.publication_doi },
      { key: 'website', label: 'Website', value: metadata.website },
      { key: 'description', label: 'Description', value: metadata.description },
      { key: 'maintainer_update', label: 'Maintainer update', value: metadata.maintainer_update },
      { key: 'license', label: 'License', value: metadata.license },
    ];

    return entries.filter((entry) => isPopulated(entry.value));
  }, [selectedDatabase]);

  useEffect(() => {
    // Keep mode selection valid when only one annotation source is available.
    if (phenotypeMode.hasPhenotype && !phenotypeMode.hasClinical) {
      setRequestedPhenotypeMode('phenotype');
      return;
    }
    if (phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('clinical');
      return;
    }
    if (!phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('auto');
    }
  }, [phenotypeMode.hasClinical, phenotypeMode.hasPhenotype]);

  return (
    <>
      <article className="card full-width-tile database-plots-tile tab-primary-tile">
        <div className="workspace-output-header workspace-output-header-with-db section-header">
          <div>
            <h2>Database Dashboard</h2>
            <p>Overview and visual summaries of the active resistance database.</p>
          </div>
        </div>

        {selectedDatabase ? (
          <>
          {databaseInfoEntries.length > 0 ? (
            <section className="database-meta-panel" aria-label="Database information">
              {databaseInfoEntries.map((entry) => (
                <div key={entry.key} className="database-meta-row">
                  <span className="database-meta-label">{entry.label}</span>
                  <span className="database-meta-value">
                    {_renderDatabaseMetaValue(entry)}
                  </span>
                </div>
              ))}
            </section>
          ) : null}
          {_renderDatabaseAlgorithms(selectedDatabase?.algorithms)}
          {summaryTile || ic50Sections.length > 0 || detailSections.length > 0 ? (
            <div className="database-plot-grid">
              {summaryTile ? <DatabasePieSummaryRow tile={summaryTile} /> : null}
              {/* Chart controls are global for all gene tiles in this section. */}
              {ic50Sections.map((section) => (
                <section
                  key={section.sectionKey}
                  className={[
                    'database-reference-section',
                    section.layout === 'single-column' ? 'database-reference-section-wide' : '',
                    section.layout === 'score-grid' ? 'database-reference-section-score' : '',
                  ].filter(Boolean).join(' ')}
                >
                  <div className="database-phenotype-switch-row">
                    <h3 className="database-section-heading">{section.sectionHeading}</h3>
                  </div>
                  <div className="database-reference-plot-grid">
                    {section.plots.map((plot) => (
                      <DatabaseDrugDistributionPlot key={plot.key} plot={plot} />
                    ))}
                  </div>
                </section>
              ))}
              {detailSections.length > 0 ? (
                <>
                  <div className="database-phenotype-switch-row">
                    <h3 className="database-section-heading">Mutations in each gene</h3>
                    <div className="database-phenotype-switch-controls">
                      {phenotypeMode.hasPhenotype && phenotypeMode.hasClinical ? (
                        <div className="database-phenotype-switch" role="group" aria-label="Position annotation mode">
                          <button
                            type="button"
                            className={activePhenotypeMode === 'phenotype' ? 'active' : ''}
                            onClick={() => setRequestedPhenotypeMode('phenotype')}
                          >
                            Phenotype
                          </button>
                          <button
                            type="button"
                            className={activePhenotypeMode === 'clinical' ? 'active' : ''}
                            onClick={() => setRequestedPhenotypeMode('clinical')}
                          >
                            Clinical phenotype
                          </button>
                        </div>
                      ) : null}
                      <label className="database-bin-size-control" aria-label="Amino-acid bin size">
                        <span>Bin size</span>
                        <input
                          type="number"
                          min="1"
                          max="100"
                          step="1"
                          value={binSize}
                          onChange={(event) => {
                            const nextValue = Number(event.target.value);
                            if (Number.isFinite(nextValue)) {
                              setRequestedBinSize(nextValue);
                            }
                          }}
                        />
                      </label>
                    </div>
                  </div>
                  {detailSections.map((section) => (
                    <section key={section.referenceKey} className="database-reference-section">
                      <div className="database-reference-heading">
                        <h3>{section.referenceHeading}</h3>
                      </div>
                      <div className="database-reference-plot-grid">
                        {section.plots.map((plot) => (
                          <DatabasePositionPlot key={plot.key} plot={plot} />
                        ))}
                      </div>
                    </section>
                  ))}
                </>
              ) : null}
            </div>
          ) : (
            <p className="status">No plot-friendly data is available for the active database.</p>
          )}
          </>
        ) : (
          <p className="status">No active database loaded.</p>
        )}
      </article>
    </>
  );
}
