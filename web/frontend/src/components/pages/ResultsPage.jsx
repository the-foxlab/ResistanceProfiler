import { ResultsTab } from '../tabs/ResultsTab';

export function ResultsPage({ logic }) {
  return (
    <div className="page-workspace">
      <ResultsTab
        sessionResults={logic.sessionResults}
        selectedProfileReportPath={logic.selectedProfileReportPath}
        setSelectedProfileReportPath={logic.setSelectedProfileReportPath}
        inlineReportPath={logic.inlineReportPath}
        inlineReportLabel={logic.inlineReportLabel}
        reportOptions={logic.reportOptions}
        buildReportUrl={logic.buildReportUrl}
        buildArtifactUrl={logic.buildArtifactUrl}
        downloadAllSessionArtifacts={logic.downloadAllSessionArtifacts}
        downloadSelectedArtifacts={logic.downloadSelectedArtifacts}
        isSessionDownloadBusy={logic.isSessionDownloadBusy}
        selectedResultIndices={logic.selectedResultIndices}
        setSelectedResultIndices={logic.setSelectedResultIndices}
        comparisonDbId={logic.comparisonDbId}
        comparisonRefName={logic.comparisonRefName}
        selectAllComparable={logic.selectAllComparable}
        comparisonData={logic.comparisonData}
        isComparisonBusy={logic.isComparisonBusy}
        nonSynonymousOnly={logic.nonSynonymousOnly}
        setNonSynonymousOnly={logic.setNonSynonymousOnly}
        dbHitsOnly={logic.dbHitsOnly}
        setDbHitsOnly={logic.setDbHitsOnly}
        fetchComparisonData={logic.fetchComparisonData}
        clearComparison={logic.clearComparison}
      />
    </div>
  );
}
