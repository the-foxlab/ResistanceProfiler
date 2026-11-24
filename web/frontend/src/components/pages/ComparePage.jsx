import { useDatabaseCompare } from '../../hooks/useDatabaseCompare';
import { ComparisonVenn } from '../ComparisonVenn';
import { CompareMutationsTable } from './compare/CompareMutationsTable';
import {
  CompareSelectorsCard,
  MAX_COMPARE_DATABASES,
  MIN_COMPARE_DATABASES,
} from './compare/CompareSelectorsCard';

export function ComparePage({ logic }) {
  const compare = useDatabaseCompare(logic.databases);
  const { response, regionFilter, handleRegionClick } = compare;

  return (
    <div className="compare-tab">
      <div className="compare-layout">
        <CompareSelectorsCard
          slots={compare.slots}
          selectedIds={compare.selectedIds}
          optionsForSlot={compare.optionsForSlot}
          handleSlotChange={compare.handleSlotChange}
          sharedRefs={compare.sharedRefs}
          sharedRefsLoading={compare.sharedRefsLoading}
          accession={compare.accession}
          setAccession={compare.setAccession}
          canCompare={compare.canCompare}
          isBusy={compare.isBusy}
          runCompare={compare.runCompare}
          error={compare.error}
        />

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
              Select {MIN_COMPARE_DATABASES}–{MAX_COMPARE_DATABASES} databases and run a comparison to see the overlap. This currently only compares atomic positions and not formula rules.
            </p>
          )}
        </section>
      </div>

      {response && (
        <CompareMutationsTable
          response={response}
          vennRegions={compare.vennRegions}
          filteredRows={compare.filteredRows}
          metadataFields={compare.metadataFields}
          tableSearch={compare.tableSearch}
          setTableSearch={compare.setTableSearch}
          regionFilter={compare.regionFilter}
          setRegionFilter={compare.setRegionFilter}
          expandedKey={compare.expandedKey}
          setExpandedKey={compare.setExpandedKey}
          exportTsv={compare.exportTsv}
        />
      )}
    </div>
  );
}
