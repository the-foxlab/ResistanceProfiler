import { cleanTsvCell } from '../../../utils';
import { regionKey } from '../../vennAreas';

function regionLabel(region, databases) {
  const names = region
    .map((id) => databases.find((db) => db.id === id)?.name || id)
    .filter(Boolean);
  return names.length > 0 ? names.join(' ∩ ') : '—';
}

function RegionBadge({ region, databases }) {
  return (
    <span className="compare-region-badge" aria-label={`Present in ${regionLabel(region, databases)}`}>
      {databases.map((db) => (
        <span
          key={db.id}
          className={`compare-region-dot ${region.includes(db.id) ? 'is-present' : ''}`}
          title={db.name}
        >
          {db.name}
        </span>
      ))}
    </span>
  );
}

function MetadataSubTable({ row, databases, metadataFields }) {
  return (
    <div className="compare-metadata-subtable">
      {databases.map((db) => {
        const meta = row.per_db[db.id];
        return (
          <div key={db.id} className="compare-metadata-db-block">
            <div className="compare-metadata-db-name">{db.name}</div>
            {meta ? (
              <table className="compare-metadata-table">
                <tbody>
                  {metadataFields.map((field) => (
                    <tr key={field.key}>
                      <th scope="row">{field.label}</th>
                      <td>{cleanTsvCell(meta[field.key]) || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="compare-metadata-absent">—</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FragmentRow({ row, databases, metadataFields, isOpen, onToggle }) {
  return (
    <>
      <tr className="compare-table-row" onClick={onToggle}>
        <td>
          <button
            type="button"
            className="compare-expand-btn"
            aria-expanded={isOpen}
            aria-label={isOpen ? 'Collapse metadata' : 'Expand metadata'}
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
          >
            {isOpen ? '−' : '+'}
          </button>
        </td>
        <td>{row.feature}</td>
        <td>{row.reference}</td>
        <td>{row.position + 1}</td>
        <td>{row.mutation}</td>
        <td>{row.drug}</td>
        <td>
          <RegionBadge region={row.region} databases={databases} />
        </td>
      </tr>
      {isOpen && (
        <tr className="compare-table-detail">
          <td colSpan={7}>
            <MetadataSubTable row={row} databases={databases} metadataFields={metadataFields} />
          </td>
        </tr>
      )}
    </>
  );
}

export function CompareMutationsTable({
  response,
  vennRegions,
  filteredRows,
  metadataFields,
  tableSearch,
  setTableSearch,
  regionFilter,
  setRegionFilter,
  expandedKey,
  setExpandedKey,
  exportTsv,
}) {
  return (
    <section className="card">
      <div className="compare-table-toolbar">
        <h2 className="section-header">Mutations</h2>
        <div className="compare-table-controls">
          <label htmlFor="compare-table-search">Search</label>
          <input
            id="compare-table-search"
            type="search"
            placeholder="Search mutations"
            value={tableSearch}
            onChange={(event) => setTableSearch(event.target.value)}
          />
          <label htmlFor="compare-region-filter">Region</label>
          <select
            id="compare-region-filter"
            value={regionFilter}
            onChange={(event) => setRegionFilter(event.target.value)}
          >
            <option value="all">All regions</option>
            {vennRegions.map((entry) => (
              <option key={regionKey(entry.region)} value={regionKey(entry.region)}>
                {regionLabel(entry.region, response.databases)} ({entry.count})
              </option>
            ))}
          </select>
          <button type="button" onClick={exportTsv} disabled={filteredRows.length === 0}>
            Export TSV
          </button>
        </div>
      </div>

      {filteredRows.length === 0 ? (
        <div className="mutation-empty-state">No mutations in the selected region.</div>
      ) : (
        <div className="table-wrap">
          <table className="compare-table">
            <thead>
              <tr>
                <th aria-label="Expand" />
                <th>Feature</th>
                <th>Ref</th>
                <th>Pos</th>
                <th>Mut</th>
                <th>Drug</th>
                <th>Region</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const key = `${row.feature}|${row.position}|${row.reference}|${row.mutation}|${row.drug}`;
                const isOpen = expandedKey === key;
                return (
                  <FragmentRow
                    key={key}
                    row={row}
                    databases={response.databases}
                    metadataFields={metadataFields}
                    isOpen={isOpen}
                    onToggle={() => setExpandedKey(isOpen ? '' : key)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
