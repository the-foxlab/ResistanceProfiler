import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchSharedReferences, fetchReferenceAccessions, compareDatabases } from '../../api';
import { ComparisonVenn } from '../ComparisonVenn';
import { getCompareState, setCompareState } from './compareSessionState';

const MIN_DATABASES = 2;
const MAX_DATABASES = 3;

const METADATA_FIELDS = [
  { key: 'drug', label: 'Drug' },
  { key: 'phenotype', label: 'Phenotype' },
  { key: 'ic50', label: 'IC50' },
  { key: 'fold_ic50', label: 'Fold-IC50' },
  { key: 'score', label: 'Score' },
  { key: 'publication', label: 'Publication' },
];

function regionKey(region) {
  return [...region].sort().join(',');
}

function regionLabel(region, databases) {
  const names = region
    .map((id) => databases.find((db) => db.id === id)?.name || id)
    .filter(Boolean);
  return names.length > 0 ? names.join(' ∩ ') : '—';
}

function cleanCell(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value).replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
}

// Region badge: a dot per selected database, filled where the key is present.
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

// Expandable per-database metadata sub-table for one mutation key.
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
                      <td>{cleanCell(meta[field.key]) || '—'}</td>
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

/**
 * Compare Databases tab: pick 2-3 project databases via three dropdowns and
 * view rule overlap as a Venn diagram plus an expandable per-database
 * metadata table. Dropdowns are progressively constrained: a database is only
 * offered when it shares at least one reference accession with every database
 * already chosen, so any completed selection is comparable.
 *
 * Props:
 * - databases: [{id, name, display_name, ...}] — all project databases
 */
export function ComparePage({ logic }) {
  const { databases } = logic;
  // Hydrate from the session store so the selection survives switching to
  // another sidebar mode (the tab unmounts on mode change).
  const persisted = getCompareState();
  const [slots, setSlots] = useState(persisted.slots);
  const [accessionsByDb, setAccessionsByDb] = useState(null);
  const [sharedRefs, setSharedRefs] = useState(persisted.sharedRefs || []);
  const [sharedRefsLoading, setSharedRefsLoading] = useState(false);
  const [accession, setAccession] = useState(persisted.accession);
  const [response, setResponse] = useState(persisted.response);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');
  const [regionFilter, setRegionFilter] = useState(persisted.regionFilter);
  const [expandedKey, setExpandedKey] = useState(persisted.expandedKey);
  const [tableSearch, setTableSearch] = useState(persisted.tableSearch || '');
  // Selection the currently loaded sharedRefs belong to; seeded from the store
  // so a remount with the same selection does not refetch.
  const loadedRefsKeyRef = useRef(persisted.sharedRefsKey || '');

  const selectedIds = useMemo(() => slots.filter(Boolean), [slots]);

  const selectedDatabases = useMemo(
    () => selectedIds
      .map((id) => databases.find((db) => db.id === id))
      .filter(Boolean)
      .map((db) => ({ ...db, name: db.display_name || db.name || db.id })),
    [selectedIds, databases],
  );

  // Load each database's reference accessions once so dropdown options can be
  // constrained to comparable databases without extra requests per selection.
  useEffect(() => {
    if (databases.length === 0) {
      setAccessionsByDb(null);
      return undefined;
    }
    let cancelled = false;
    fetchReferenceAccessions(databases.map((db) => db.id))
      .then((data) => {
        if (!cancelled) setAccessionsByDb(data.items || {});
      })
      .catch(() => {
        if (!cancelled) setAccessionsByDb(null);
      });
    return () => {
      cancelled = true;
    };
  }, [databases]);

  // Fetch shared references whenever the selection reaches the minimum and has
  // not been loaded for this exact selection already (e.g. after a remount).
  useEffect(() => {
    const selectionKey = selectedIds.join(',');
    if (selectedIds.length < MIN_DATABASES) {
      loadedRefsKeyRef.current = '';
      setSharedRefs([]);
      setAccession('');
      return undefined;
    }
    if (loadedRefsKeyRef.current === selectionKey) {
      return undefined;
    }
    loadedRefsKeyRef.current = selectionKey;
    let cancelled = false;
    setSharedRefsLoading(true);
    setError('');
    fetchSharedReferences(selectedIds)
      .then((data) => {
        if (cancelled) return;
        const items = data.items || [];
        setSharedRefs(items);
        // Pre-select the first shared reference so the value shown in the
        // dropdown is always backed by state and Compare is usable right away.
        setAccession(items.length > 0 ? items[0].accession : '');
      })
      .catch((err) => {
        if (cancelled) return;
        setSharedRefs([]);
        setAccession('');
        setError(err.message || 'Failed to load shared references.');
      })
      .finally(() => {
        if (!cancelled) setSharedRefsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIds]);

  // Mirror the tab state into the session store on every change so it
  // survives unmounting when the user switches sidebar modes.
  useEffect(() => {
    setCompareState({
      slots,
      accession,
      response,
      regionFilter,
      expandedKey,
      tableSearch,
      sharedRefs,
      sharedRefsKey: loadedRefsKeyRef.current,
    });
  }, [slots, accession, response, regionFilter, expandedKey, tableSearch, sharedRefs]);

  const resetResults = useCallback(() => {
    setResponse(null);
    setRegionFilter('all');
    setExpandedKey('');
    setTableSearch('');
  }, []);

  const handleSlotChange = (index, value) => {
    setSlots((current) => {
      const next = [...current];
      next[index] = value;
      // Changing a slot invalidates every downstream choice.
      for (let later = index + 1; later < next.length; later += 1) {
        next[later] = '';
      }
      return next;
    });
    resetResults();
  };

  const sharesAccession = useCallback(
    (candidateId, otherId) => {
      if (!accessionsByDb) return true;
      const candidate = accessionsByDb[candidateId] || {};
      const other = accessionsByDb[otherId] || {};
      return Object.keys(candidate).some((acc) => acc in other);
    },
    [accessionsByDb],
  );

  const optionsForSlot = (index) => {
    const chosen = slots.slice(0, index).filter(Boolean);
    return databases.filter((db) => {
      if (chosen.includes(db.id)) return false;
      if (chosen.length === 0) return true;
      return chosen.every((id) => sharesAccession(db.id, id));
    });
  };

  const runCompare = useCallback(async () => {
    if (selectedIds.length < MIN_DATABASES || !accession) {
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      const data = await compareDatabases(selectedIds, accession);
      setResponse({ ...data, databases: selectedDatabases });
      setRegionFilter('all');
      setExpandedKey('');
      setTableSearch('');
    } catch (err) {
      setResponse(null);
      setError(err.message || 'Comparison failed.');
    } finally {
      setIsBusy(false);
    }
  }, [selectedIds, selectedDatabases, accession]);

  const handleRegionClick = useCallback((region) => {
    setRegionFilter(regionKey(region));
  }, []);

  // Non-empty Venn regions drive the region filter dropdown.
  const vennRegions = useMemo(() => {
    if (!response) return [];
    return (response.venn || []).filter((entry) => entry.count > 0);
  }, [response]);

  // Subset the main rows by the search term. Only main-row columns are
  // searched (feature, ref, displayed 1-based position, mutation, drug) —
  // expanded metadata rows are never matched.
  const filteredRows = useMemo(() => {
    if (!response) return [];
    const rows = response.rows || [];
    const regionMatched = regionFilter === 'all'
      ? rows
      : rows.filter((row) => regionKey(row.region) === regionFilter);
    const term = tableSearch.trim().toLowerCase();
    if (!term) return regionMatched;
    return regionMatched.filter((row) => [
      row.feature,
      row.reference,
      row.position + 1,
      row.mutation,
      row.drug,
    ].some((value) => String(value ?? '').toLowerCase().includes(term)));
  }, [response, regionFilter, tableSearch]);

  const metadataFields = response?.metadata_fields
    ? METADATA_FIELDS.filter((field) => response.metadata_fields.includes(field.key))
    : METADATA_FIELDS;

  const downloadTsv = () => {
    if (!response) return;
    const dbs = response.databases || [];
    const headers = ['feature', 'ref', 'pos', 'mut', 'database', ...metadataFields.map((f) => f.label)];
    const lines = [headers.join('\t')];
    for (const row of filteredRows) {
      for (const db of dbs) {
        const meta = row.per_db[db.id];
        const cells = [
          cleanCell(row.feature),
          cleanCell(row.reference),
          cleanCell(row.position + 1),
          cleanCell(row.mutation),
          cleanCell(db.name),
          ...metadataFields.map((f) => cleanCell(meta?.[f.key])),
        ];
        lines.push(cells.join('\t'));
      }
    }
    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'database-comparison.tsv';
    anchor.click();
    URL.revokeObjectURL(href);
  };

  const canCompare = selectedIds.length >= MIN_DATABASES && Boolean(accession) && !isBusy;

  return (
    <div className="compare-tab">
      <div className="compare-layout">
        <section className="card compare-selectors">
          <p className="compare-hint">
            Select {MIN_DATABASES}–{MAX_DATABASES} databases that share a reference. Overlap is matched by
            reference accession.
          </p>

          <div className="compare-db-dropdowns">
            {[0, 1, 2].map((index) => {
              const options = optionsForSlot(index);
              const isDisabled = index > 0 && options.length === 0;
              return (
                <div key={index} className="compare-db-slot">
                  <label htmlFor={`compare-db-${index}`}>Database {index + 1}</label>
                  <select
                    id={`compare-db-${index}`}
                    value={slots[index]}
                    disabled={index > 0 && slots[index - 1] === ''}
                    onChange={(event) => handleSlotChange(index, event.target.value)}
                  >
                    <option value="">—</option>
                    {options.map((db) => (
                      <option key={db.id} value={db.id}>
                        {db.display_name || db.name || db.id}
                      </option>
                    ))}
                  </select>
                  {isDisabled && slots[index] === '' && (
                    <span className="compare-slot-hint">No comparable databases</span>
                  )}
                </div>
              );
            })}
          </div>

          {selectedIds.length >= MIN_DATABASES && (
            <div className="compare-ref-row">
              <label htmlFor="compare-ref-select">Shared reference</label>
              <select
                id="compare-ref-select"
                value={accession}
                disabled={sharedRefsLoading || sharedRefs.length === 0}
                onChange={(event) => setAccession(event.target.value)}
              >
                {sharedRefs.length === 0 && <option value="">No shared references</option>}
                {sharedRefs.map((ref) => (
                  <option key={ref.accession} value={ref.accession}>
                    {ref.accession}{ref.organism ? ` (${ref.organism})` : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            type="button"
            className="analyze-primary"
            disabled={!canCompare}
            onClick={runCompare}
          >
            {isBusy ? 'Comparing…' : 'Compare'}
          </button>

          {error && <p className="compare-error" role="alert">{error}</p>}
        </section>

        <section className="card compare-venn-card">
          <h2 className="section-header">Rule overlap</h2>
          {response ? (
            <>
              <ComparisonVenn
                data={response}
                activeRegion={regionFilter === 'all' ? null : regionFilter.split(',')}
                onRegionClick={handleRegionClick}
              />
              <div className="compare-counts">
                <span>Total unique: <b>{response.counts?.total_unique ?? 0}</b></span>
                <span>Intersection: <b>{response.counts?.intersection ?? 0}</b></span>
                {(response.databases || []).map((db) => (
                  <span key={db.id}>
                    {db.name}: <b>{response.counts?.per_db?.[db.id] ?? 0}</b>
                  </span>
                ))}
              </div>
            </>
          ) : (
            <p className="compare-venn-placeholder">
              Select {MIN_DATABASES}–{MAX_DATABASES} databases and run a comparison to see the overlap. This currently only compares atomic positions and not formula rules.
            </p>
          )}
        </section>
      </div>

      {response && (
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
                <button type="button" onClick={downloadTsv} disabled={filteredRows.length === 0}>
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
      )}
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
