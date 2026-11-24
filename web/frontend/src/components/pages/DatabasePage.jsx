import { useEffect, useMemo, useState } from 'react';

import { buildDatabasePlots } from '../database-plots/buildDatabasePlots';
import { DatabasePieSummaryRow } from '../database-plots/DatabasePieSummaryTile';
import { DatabasePositionPlot } from '../database-plots/DatabasePositionPlot';
import { DatabaseDrugDistributionPlot } from '../database-plots/DatabaseDrugDistributionPlot';
import { DatabaseMetaPanel } from './database/DatabaseMetaPanel';
import { DatabaseAlgorithmsPanel } from './database/DatabaseAlgorithmsPanel';

export function DatabasePage({ logic }) {
  const { rules, formulaRules, mutationPlotMeta, selectedDatabase } = logic;
  const [requestedPhenotypeMode, setRequestedPhenotypeMode] = useState('auto');
  const [requestedBinSize, setRequestedBinSize] = useState(10);

  const {
    summaryTile,
    ic50Sections,
    detailSections,
    phenotypeMode,
    binSize,
  } = useMemo(
    () => buildDatabasePlots(rules, formulaRules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize),
    [rules, formulaRules, mutationPlotMeta, requestedPhenotypeMode, requestedBinSize],
  );

  const activePhenotypeMode = phenotypeMode.activeMode;

  useEffect(() => {
    // Keep mode selection valid when only one annotation source is available.
    if (phenotypeMode.hasPhenotype && !phenotypeMode.hasClinical) {
      setRequestedPhenotypeMode('phenotype');
    } else if (phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('clinical');
    } else if (!phenotypeMode.hasClinical && !phenotypeMode.hasPhenotype) {
      setRequestedPhenotypeMode('auto');
    }
  }, [phenotypeMode.hasClinical, phenotypeMode.hasPhenotype]);

  const hasPlots = Boolean(summaryTile || ic50Sections.length > 0 || detailSections.length > 0);

  return (
    <article className="card full-width-tile database-plots-tile tab-primary-tile">
      {selectedDatabase ? (
        <>
          <DatabaseMetaPanel selectedDatabase={selectedDatabase} />
          <DatabaseAlgorithmsPanel algorithms={selectedDatabase?.algorithms} />
          {hasPlots ? (
            <div className="database-plot-grid">
              {summaryTile ? <DatabasePieSummaryRow tile={summaryTile} /> : null}
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
  );
}
