export function ArtifactTableCells({
  htmlPath,
  pdfPath,
  jsonPath,
  tsvPath,
  buildReportUrl,
  buildArtifactUrl,
}) {
  return (
    <>
      <td>{htmlPath ? <a href={buildReportUrl(htmlPath)} target="_blank" rel="noreferrer">View</a> : '—'}</td>
      <td>{pdfPath ? <a href={buildArtifactUrl(pdfPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
      <td>{jsonPath ? <a href={buildArtifactUrl(jsonPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
      <td>{tsvPath ? <a href={buildArtifactUrl(tsvPath)} target="_blank" rel="noreferrer">Download</a> : '—'}</td>
    </>
  );
}
