import mutationsIconSrc from '../../assets/search.svg';
import resetFilterIconSrc from '../../assets/reset_filter.svg';

export function MutationsTab({
  rules,
  formulaRules,
  mutationColumns,
  formulaColumns,
  displayedRules,
  displayedFormulaRules,
  mutationFilter,
  setMutationFilter,
  mutationFilterColumn,
  setMutationFilterColumn,
  mutationSortColumn,
  setMutationSortColumn,
  mutationSortAsc,
  setMutationSortAsc,
  formulaFilter,
  setFormulaFilter,
  formulaFilterColumn,
  setFormulaFilterColumn,
  mutationPlotMeta,
  mutationsLoaded,
  downloadMutationsAsTsv,
  downloadFormulaRulesAsTsv,
  databases,
  selectedDatabaseId,
}) {
  return (
    <>
      <article className="card full-width-tile tab-primary-tile">
        <div className="workspace-output-header workspace-output-header-with-db section-header">
          <div>
            <h2>Browse mutations</h2>
          </div>
        </div>
        <section className="mutation-merged-section">
          <div className="workspace-output-header section-header">
            <div>
              <h3>Single mutations</h3>
              <p>{displayedRules.length} visible row(s)</p>
            </div>
          </div>
          <div className="mutation-toolbar">
            <label className="mutation-search" htmlFor="mutation-rules-search">
              <img src={mutationsIconSrc} alt="" aria-hidden="true" />
              <input
                id="mutation-rules-search"
                className="mutation-search-input"
                type="search"
                placeholder="search rules"
                value={mutationFilter}
                onChange={(event) => setMutationFilter(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="mutation-reset-button"
              aria-label="Reset filter"
              title="Reset filter"
              onClick={() => {
                setMutationFilter('');
                setMutationFilterColumn('-1');
              }}
            >
              <img src={resetFilterIconSrc} alt="" aria-hidden="true" />
            </button>
            <button
              type="button"
              className="mutation-download-button"
              onClick={downloadMutationsAsTsv}
            >
              Download as TSV
            </button>
          </div>
          <div className="table-wrap mutation-table-wrap">
            <table>
              <thead>
                <tr>
                  {mutationColumns.map((column, index) => (
                    <th
                      key={column.key}
                      className="sortable-col"
                      onClick={() => {
                        if (mutationSortColumn === index) {
                          setMutationSortAsc(!mutationSortAsc);
                        } else {
                          setMutationSortColumn(index);
                          setMutationSortAsc(true);
                        }
                      }}
                    >
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayedRules.map((rule, index) => (
                  <tr key={`${rule.id || 'rule'}-${index}`}>
                    {mutationColumns.map((column) => (
                      <td key={`${column.key}-${index}`}>{column.accessor(rule)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {mutationsLoaded && rules.length === 0 ? (
            <p className="status">No single-mutation rules were found for the selected database/filter.</p>
          ) : null}
          {mutationsLoaded && rules.length > 0 && displayedRules.length === 0 ? (
            <p className="status">No mutations match the current filter.</p>
          ) : null}
        </section>

        <section className="mutation-merged-section">
          <div className="workspace-output-header section-header">
            <div>
              <h3>Combinatorial mutations</h3>
              <p>{displayedFormulaRules.length} visible row(s)</p>
            </div>
          </div>
          <div className="mutation-toolbar">
            <label className="mutation-search" htmlFor="formula-rules-search">
              <img src={mutationsIconSrc} alt="" aria-hidden="true" />
              <input
                id="formula-rules-search"
                className="mutation-search-input"
                type="search"
                placeholder="search rules"
                value={formulaFilter}
                onChange={(event) => setFormulaFilter(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="mutation-reset-button"
              aria-label="Reset filter"
              title="Reset filter"
              onClick={() => {
                setFormulaFilter('');
                setFormulaFilterColumn('-1');
              }}
            >
              <img src={resetFilterIconSrc} alt="" aria-hidden="true" />
            </button>
            <button
              type="button"
              className="mutation-download-button"
              onClick={downloadFormulaRulesAsTsv}
            >
              Download as TSV
            </button>
          </div>
          <div className="table-wrap mutation-table-wrap">
            <table>
              <thead>
                <tr>
                  {formulaColumns.map((column) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayedFormulaRules.map((rule, index) => (
                  <tr key={`${rule.formula_id || 'formula'}-${index}`}>
                    {formulaColumns.map((column) => (
                      <td key={`${column.key}-${index}`}>{column.accessor(rule)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {mutationsLoaded && formulaRules.length === 0 ? (
            <p className="status">No formula combinations were found for the selected database/filter.</p>
          ) : null}
          {mutationsLoaded && formulaRules.length > 0 && displayedFormulaRules.length === 0 ? (
            <p className="status">No formula combinations match the current filter.</p>
          ) : null}
        </section>
      </article>
    </>
  );
}
