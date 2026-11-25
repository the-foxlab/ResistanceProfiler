import { useOptionalMutationsLogic } from '../../DashboardContext';

function RuleTableSection({
  title,
  searchId,
  searchLabel,
  filterValue,
  onFilterChange,
  onReset,
  onDownload,
  columns,
  rows,
  rowKeyPrefix,
  rowKeyField,
  emptyFilterMessage,
  mutationsLoaded,
  onSortColumn,
  sortColumn,
  sortAsc,
}) {
  return (
    <section className="mutation-merged-section">
      <div className="workspace-output-header section-header">
        <div>
          <h3>{title}</h3>
          <p>{rows.length} visible row(s)</p>
        </div>
      </div>
      <div className="mutation-toolbar">
        <label className="mutation-search" htmlFor={searchId}>
          <span className="mutation-search-icon" aria-hidden="true" />
          <input
            id={searchId}
            className="mutation-search-input"
            type="search"
            aria-label={searchLabel}
            placeholder="search rules"
            value={filterValue}
            onChange={(event) => onFilterChange(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="mutation-reset-button"
          aria-label="Reset filter"
          title="Reset filter"
          onClick={onReset}
        >
          <span className="mutation-reset-icon" aria-hidden="true" />
        </button>
        <button
          type="button"
          className="mutation-download-button"
          onClick={onDownload}
          disabled={!mutationsLoaded}
        >
          Download as TSV
        </button>
      </div>
      <div className="table-wrap mutation-table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((column, index) => {
                const isSortable = Boolean(onSortColumn);
                const isActiveSort = isSortable && sortColumn === index;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={isSortable ? (isActiveSort ? (sortAsc ? 'ascending' : 'descending') : 'none') : undefined}
                    className={[
                      isSortable ? 'sortable-col' : '',
                      column.key === 'comment' ? 'mutation-comment-column' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    {isSortable ? (
                      <button
                        type="button"
                        className="mutation-sort-button"
                        onClick={() => onSortColumn(index)}
                      >
                        {column.label}
                        {isActiveSort ? (
                          <span className="mutation-sort-indicator" aria-hidden="true">
                            {sortAsc ? '▲' : '▼'}
                          </span>
                        ) : null}
                      </button>
                    ) : (
                      column.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((rule, index) => (
              <tr key={`${rule[rowKeyField] || rowKeyPrefix}-${index}`}>
                {columns.map((column) => {
                  const value = column.accessor(rule);
                  const isComment = column.key === 'comment';
                  return (
                    <td
                      key={`${column.key}-${index}`}
                      className={isComment ? 'mutation-comment-column' : ''}
                      title={isComment ? value : undefined}
                    >
                      {isComment ? <span className="mutation-comment-content">{value}</span> : value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mutationsLoaded && rows.length === 0 ? <p className="status">{emptyFilterMessage}</p> : null}
    </section>
  );
}

export function MutationsPage({ logic: logicProp }) {
  const contextLogic = useOptionalMutationsLogic();
  const logic = logicProp ?? contextLogic;
  const {
    rules,
    formulaRules,
    mutationColumns,
    formulaColumns,
    displayedRules,
    displayedFormulaRules,
    mutationFilter,
    setMutationFilter,
    mutationSortColumn,
    setMutationSortColumn,
    mutationSortAsc,
    setMutationSortAsc,
    formulaFilter,
    setFormulaFilter,
    mutationsLoaded,
    statusError,
    downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv,
  } = logic;

  // With only one table present, stretch the card to fill the viewport height.
  const singleTable = rules.length > 0 !== formulaRules.length > 0;
  const cardClassName = singleTable
    ? 'card full-width-tile tab-primary-tile mutations-single-table'
    : 'card full-width-tile tab-primary-tile';

  const handleSortSingle = (index) => {
    if (mutationSortColumn === index) {
      setMutationSortAsc(!mutationSortAsc);
    } else {
      setMutationSortColumn(index);
      setMutationSortAsc(true);
    }
  };

  return (
    <article className={cardClassName}>
      {statusError ? (
        <p className="status" role="alert" style={{ color: 'var(--color-error)' }}>
          {statusError}
        </p>
      ) : null}

      {rules.length > 0 ? (
        <RuleTableSection
          title="Single mutations"
          searchId="mutation-rules-search"
          searchLabel="Search single mutations"
          filterValue={mutationFilter}
          onFilterChange={setMutationFilter}
          onReset={() => setMutationFilter('')}
          onDownload={downloadMutationsAsTsv}
          columns={mutationColumns}
          rows={displayedRules}
          rowKeyPrefix="rule"
          rowKeyField="id"
          emptyFilterMessage="No mutations match the current filter."
          mutationsLoaded={mutationsLoaded}
          onSortColumn={handleSortSingle}
          sortColumn={mutationSortColumn}
          sortAsc={mutationSortAsc}
        />
      ) : null}

      {formulaRules.length > 0 ? (
        <RuleTableSection
          title="Combinatorial mutations"
          searchId="formula-rules-search"
          searchLabel="Search combinatorial mutations"
          filterValue={formulaFilter}
          onFilterChange={setFormulaFilter}
          onReset={() => setFormulaFilter('')}
          onDownload={downloadFormulaRulesAsTsv}
          columns={formulaColumns}
          rows={displayedFormulaRules}
          rowKeyPrefix="formula"
          rowKeyField="formula_id"
          emptyFilterMessage="No formula combinations match the current filter."
          mutationsLoaded={mutationsLoaded}
        />
      ) : null}

      {mutationsLoaded && rules.length === 0 && formulaRules.length === 0 ? (
        <p className="status mutation-empty-state">
          No mutation rules were found for the selected database.
        </p>
      ) : null}
    </article>
  );
}
