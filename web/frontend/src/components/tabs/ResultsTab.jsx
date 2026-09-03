import { useState } from 'react';

import infoIconSrc from '../../assets/info.svg';
import { Spinner } from '../Spinner';
import { ComparisonHeatmap } from '../ComparisonHeatmap';

export function ResultsTab({
  sessionResults,
  selectedProfileReportPath,
  setSelectedProfileReportPath,
  inlineReportPath,
  inlineReportLabel,
  reportOptions,
  buildReportUrl,
  buildArtifactUrl,
  downloadAllSessionArtifacts,
  downloadSelectedArtifacts,
  isSessionDownloadBusy,
  selectedResultIndices,
  setSelectedResultIndices,
  comparisonDbId,
  comparisonRefName,
  selectAllComparable,
  comparisonData,
  isComparisonBusy,
  nonSynonymousOnly,
  setNonSynonymousOnly,
  dbHitsOnly,
  setDbHitsOnly,
  fetchComparisonData,
  clearComparison,
}) {
  const selectedResults = [...selectedResultIndices]
    .map((i) => sessionResults[i])
    .filter(Boolean);
  const selectedDbIds = new Set(selectedResults.map((r) => r.database_id || ''));
  const selectedRefNames = new Set(selectedResults.map((r) => r.reference_name || ''));
  const isSelectionComparable = selectedResults.length >= 2
    && selectedDbIds.size === 1
    && selectedRefNames.size === 1;

  return (
    <article className="card full-width-tile tab-primary-tile">
      <div className="workspace-output-header section-header">
        <div>
          <h2>Session results</h2>
          <p>All analysis outputs from this session. Results are cleared on page reload.</p>
        </div>
        {sessionResults.length > 0 ? (
          <button
            type="button"
            className="analyze-primary results-download-btn"
            onClick={() => downloadSelectedArtifacts()}
            disabled={selectedResultIndices.size === 0 || isSessionDownloadBusy}
          >
            {isSessionDownloadBusy ? (
              <><Spinner /> Preparing...</>
            ) : (
              'Download'
            )}
          </button>
        ) : null}
      </div>
      {sessionResults.length === 0 ? (
        <p className="status">No results yet. Run an analysis to see results here.</p>
      ) : (
        <>
        <div className="table-wrap mutation-table-wrap">
          <table>
            <thead>
              <tr>
                <th className="checkbox-col">
                  <input
                    type="checkbox"
                    aria-label="Select all results"
                    checked={sessionResults.length > 0 && selectedResultIndices.size === sessionResults.length}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedResultIndices(new Set(sessionResults.map((_, i) => i)));
                      } else {
                        setSelectedResultIndices(new Set());
                      }
                    }}
                  />
                </th>
                <th>Mode</th>
                <th>Sample</th>
                <th>Reference</th>
                <th>Database</th>
                <th>Timestamp</th>
                <th>HTML</th>
                <th>PDF</th>
                <th>JSON</th>
                <th>TSV</th>
              </tr>
            </thead>
            <tbody>
              {[...sessionResults].reverse().map((result, revIndex) => {
                const originalIndex = sessionResults.length - 1 - revIndex;
                const isSelected = selectedResultIndices.has(originalIndex);
                const resultDbId = result.database_id || '';
                const resultRefName = result.reference_name || '';
                const isDisabled = comparisonDbId !== null && (resultDbId !== comparisonDbId || resultRefName !== comparisonRefName);
                return (
                  <tr key={result.run_id || revIndex}>
                    <td className="checkbox-col">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        disabled={isDisabled}
                        onChange={() => {
                          setSelectedResultIndices((prev) => {
                            const next = new Set(prev);
                            if (next.has(originalIndex)) {
                              next.delete(originalIndex);
                            } else {
                              next.add(originalIndex);
                            }
                            return next;
                          });
                        }}
                      />
                    </td>
                    <td>
                      <span className={`job-status-badge mode-badge-${String(result.mode || '').replace(/[^a-z]/g, '')}`}>
                        {result.mode || '—'}
                      </span>
                    </td>
                    <td>{result.sample_name || '—'}</td>
                    <td>{result.reference_name || '—'}</td>
                    <td>{result.database_id || '—'}</td>
                    <td>{result.created_at ? new Date(result.created_at).toLocaleString() : '—'}</td>
                    <td>{result.report_html_path ? <a href={buildReportUrl(result.report_html_path)} target="_blank" rel="noreferrer">View</a> : '—'}</td>
                    <td>{result.report_pdf_path ? <a href={buildArtifactUrl(result.report_pdf_path)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                    <td>{result.report_json_path ? <a href={buildArtifactUrl(result.report_json_path)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                    <td>{result.report_tsv_path ? <a href={buildArtifactUrl(result.report_tsv_path)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="profile-analyze-row comparison-actions">
          <button
            type="button"
            className="analyze-primary comparison-secondary"
            onClick={() => selectAllComparable()}
            disabled={selectedResultIndices.size === 0}
          >
            Select all comparable
          </button>
          <button
            type="button"
            className="analyze-primary"
            onClick={() => fetchComparisonData()}
            disabled={selectedResultIndices.size < 2 || isComparisonBusy || !isSelectionComparable}
          >
            {isComparisonBusy ? <><Spinner /> Comparing...</> : 'Compare selected'}
          </button>
          <button
            type="button"
            className="analyze-primary"
            onClick={() => clearComparison()}
            disabled={!comparisonData}
          >
            Clear comparison
          </button>
        </div>
        {comparisonData && (
          <>
        <div className="comparison-filters">
          <label className="comparison-switch" title="Show only non-synonymous mutations">
            <input
              type="checkbox"
              checked={nonSynonymousOnly}
              onChange={(e) => {
                setNonSynonymousOnly(e.target.checked);
                if (selectedResultIndices.size >= 2) {
                  fetchComparisonData(e.target.checked);
                }
              }}
            />
            <span>Non-synonymous only</span>
          </label>
          <label className="comparison-switch" title="Show only database hits">
            <input
              type="checkbox"
              checked={dbHitsOnly}
              onChange={(e) => {
                setDbHitsOnly(e.target.checked);
                if (selectedResultIndices.size >= 2) {
                  fetchComparisonData(undefined, e.target.checked);
                }
              }}
            />
            <span>DB hits only</span>
          </label>
        </div>
        <section className="comparison-section">
            <div className="workspace-output-header section-header">
              <div>
                <h3>Comparison heatmap
                  {comparisonData.sample_disambiguation_note && (
                    <button
                      type="button"
                      className="input-info-btn"
                      aria-label="Sample naming info"
                      title={comparisonData.sample_disambiguation_note}
                    >
                      <img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" />
                    </button>
                  )}
                </h3>
                <p>
                  {comparisonData.samples.length} samples × {comparisonData.mutations.length} mutations
                  {comparisonData.references.length > 0 && ` — Reference: ${comparisonData.references[0]}`}
                </p>
              </div>
            </div>
            <ComparisonHeatmap data={comparisonData} isBusy={isComparisonBusy} />
          </section>
          </>
        )}
        </>
      )}
    </article>
  );
}
