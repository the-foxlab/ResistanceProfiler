export const MIN_COMPARE_DATABASES = 2;
export const MAX_COMPARE_DATABASES = 3;

export function CompareSelectorsCard({
  slots,
  selectedIds,
  optionsForSlot,
  handleSlotChange,
  sharedRefs,
  sharedRefsLoading,
  accession,
  setAccession,
  canCompare,
  isBusy,
  runCompare,
  error,
}) {
  return (
    <section className="card compare-selectors">
      <p className="compare-hint">
        Select {MIN_COMPARE_DATABASES}–{MAX_COMPARE_DATABASES} databases that share a reference. Overlap is matched by
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

      {selectedIds.length >= MIN_COMPARE_DATABASES && (
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
  );
}
