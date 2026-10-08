import mutationsIconSrc from '../../assets/search.svg';
import resetFilterIconSrc from '../../assets/reset_filter.svg';

export function MutationsPage({ logic }) {
  const {
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
  } = logic;
  // With only one table present, stretch the card and its table to fill the
  // viewport so no dead whitespace is left below the content.
  const singleTable = rules.length > 0 !== formulaRules.length > 0;
  const cardClassName = singleTable
    ? 'card full-width-tile tab-primary-tile mutations-single-table'
    : 'card full-width-tile tab-primary-tile';
  return (
    <>
      <article className={cardClassName}>
        {rules.length > 0 ? (
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
                      className={`sortable-col ${column.key === 'comment' ? 'mutation-comment-column' : ''}`}
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
                      <td
                        key={`${column.key}-${index}`}
                        className={column.key === 'comment' ? 'mutation-comment-column' : ''}
                        title={column.key === 'comment' ? column.accessor(rule) : undefined}
                      >
                        {column.key === 'comment' ? (
                          <span className="mutation-comment-content">{column.accessor(rule)}</span>
                        ) : column.accessor(rule)}
                      </td>
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
        ) : null}

        {formulaRules.length > 0 ? (
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
                    <th key={column.key} className={column.key === 'comment' ? 'mutation-comment-column' : ''}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayedFormulaRules.map((rule, index) => (
                  <tr key={`${rule.formula_id || 'formula'}-${index}`}>
                    {formulaColumns.map((column) => (
                      <td
                        key={`${column.key}-${index}`}
                        className={column.key === 'comment' ? 'mutation-comment-column' : ''}
                        title={column.key === 'comment' ? column.accessor(rule) : undefined}
                      >
                        {column.key === 'comment' ? (
                          <span className="mutation-comment-content">{column.accessor(rule)}</span>
                        ) : column.accessor(rule)}
                      </td>
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
        ) : null}

        {mutationsLoaded && rules.length === 0 && formulaRules.length === 0 ? (
          <p className="status mutation-empty-state">
            No mutation rules were found for the selected database.
          </p>
        ) : null}
      </article>
    </>
  );
}
