import { useOptionalBatchAnalyzeLogic } from '../../../DashboardContext';
import { ArtifactTableCells } from '../../ArtifactTableCells';
import { InfoButton } from '../../InfoButton';
import { Spinner } from '../../Spinner';
import { VcfCutoffsFields } from './VcfCutoffsFields';

function BatchUploadedFilesTable({
  batchMode,
  batchFiles,
  batchMaxSamples,
  batchSubmitting,
  attachBatchBam,
  removeBatchFile,
  resetBatch,
}) {
  if (batchFiles.length === 0) {
    return null;
  }

  const isVcf = batchMode === 'vcf';
  const fileColumnLabel = isVcf ? 'VCF file' : batchMode === 'fasta' ? 'FASTA file' : 'JSON file';

  return (
    <div className="profile-upload-row profile-upload-row-batch-files">
      <div className="batch-files-count-row">
        <p className="field-optional">
          {batchFiles.length} / {batchMaxSamples} files
          {batchFiles.length >= batchMaxSamples ? (
            <span style={{ color: 'var(--color-error)', marginLeft: '0.4em' }}>Limit reached</span>
          ) : null}
        </p>
        <button
          type="button"
          className="button-link batch-clear-all-btn"
          onClick={() => resetBatch()}
          disabled={batchSubmitting}
          aria-label="Clear all uploaded batch files"
          title="Clear all"
        >
          Clear all
        </button>
      </div>
      <div className="table-wrap batch-uploaded-table-wrap">
        <table className="batch-uploaded-table">
          <thead>
            <tr>
              <th>{fileColumnLabel}</th>
              <th>Size</th>
              {isVcf ? <th>BAM file</th> : null}
              {isVcf ? <th>BAM size</th> : null}
              {isVcf ? <th>Upload BAM</th> : null}
              <th aria-label="Remove row" />
            </tr>
          </thead>
          <tbody>
            {batchFiles.map((file) => {
              const rowId = file.rowId ?? file.uploadId;
              return (
                <tr key={rowId}>
                  <td className="batch-uploaded-cell-name" title={file.name}>{file.name}</td>
                  <td className="batch-uploaded-cell-size field-optional">{Math.round(file.size / 1024)} KB</td>
                  {isVcf ? (
                    <td className="batch-uploaded-cell-bam-name" title={file.bamName || 'No BAM'}>
                      {file.bamName || '—'}
                    </td>
                  ) : null}
                  {isVcf ? (
                    <td className="batch-uploaded-cell-bam-size field-optional">
                      {file.bamSize != null ? `${Math.round(file.bamSize / 1024)} KB` : '—'}
                    </td>
                  ) : null}
                  {isVcf ? (
                    <td className="batch-uploaded-cell-bam-upload">
                      <label className="batch-uploaded-bam-attach">
                        <input
                          type="file"
                          accept=".bam"
                          disabled={batchSubmitting}
                          aria-label={file.bamName ? `Replace BAM for ${file.name}` : `Attach BAM to ${file.name}`}
                          onChange={(event) => {
                            const selected = event.target.files?.[0];
                            event.target.value = '';
                            if (selected) {
                              attachBatchBam(rowId, selected);
                            }
                          }}
                        />
                      </label>
                    </td>
                  ) : null}
                  <td className="batch-uploaded-cell-remove">
                    <button
                      type="button"
                      className="button-link batch-remove-file-btn"
                      onClick={() => removeBatchFile(rowId)}
                      disabled={batchSubmitting}
                      aria-label={`Remove ${file.name}`}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function BatchAnalyzePanel({ logic: logicProp }) {
  const contextLogic = useOptionalBatchAnalyzeLogic();
  const logic = logicProp ?? contextLogic;
  const {
    selectedDatabase,
    buildReportUrl,
    buildArtifactUrl,
    batchMode,
    batchVcfFiles,
    batchFastaFiles,
    batchJsonFiles,
    batchReferenceFasta,
    batchSamples,
    isBatchUploading,
    batchSubmitting,
    isBatchPolling = false,
    isBatchDownloadBusy,
    batchError,
    batchRateLimitCooldown,
    batchSubmitted,
    batchMaxSamples,
    batchVcfCutoffs,
    setBatchVcfCutoffs,
    addBatchVcfFiles,
    addBatchFastaFiles,
    addBatchJsonFiles,
    addBatchBamFiles,
    attachBatchBam,
    removeBatchFile,
    uploadBatchReferenceFasta,
    submitBatch,
    stopBatchPolling,
    downloadAllBatchArtifacts,
    resetBatch,
  } = logic;

  const batchFiles = batchMode === 'vcf'
    ? batchVcfFiles
    : batchMode === 'fasta'
      ? batchFastaFiles
      : batchJsonFiles;

  const onMultiFiles = (addFn) => async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (files.length === 0) return;
    await addFn(files);
  };

  return (
    <div className="profile-input-subtile section-subtile">
      {!batchSubmitted ? (
        <>
          {batchMode === 'vcf' ? (
            <div className="profile-upload-row profile-upload-row-batch-vcf">
              <label>
                <span className="input-label-row">
                  VCF files <InfoButton label="Batch VCF help" title="Upload one or more VCF files (.vcf or .vcf.gz)." />
                </span>
                <input type="file" multiple accept=".vcf,.vcf.gz" disabled={batchSubmitting} onChange={onMultiFiles(addBatchVcfFiles)} />
              </label>
              <label>
                <span className="label-text input-label-row">
                  Shared reference FASTA <span className="field-optional">(required)</span>{' '}
                  <InfoButton label="Batch reference help" title="Shared reference FASTA for all uploaded VCF files." />
                </span>
                <input
                  type="file"
                  accept=".fasta,.fa,.fna"
                  disabled={batchSubmitting}
                  onChange={(event) => {
                    const selected = event.target.files?.[0];
                    event.target.value = '';
                    if (selected) {
                      uploadBatchReferenceFasta(selected);
                    }
                  }}
                />
              </label>
              <label>
                <span className="input-label-row">
                  BAM files <span className="field-optional">(optional)</span>{' '}
                  <InfoButton
                    label="Batch BAM help"
                    title="Upload one or more BAM files, used for per-sample coverage evaluation. Each BAM is auto-paired to the VCF with the same filename stem (e.g. sample1.vcf ↔ sample1.bam). Unmatched or already-paired cases are reported near the Analyze button and can be fixed per row."
                  />
                </span>
                <input type="file" multiple accept=".bam" disabled={batchSubmitting} onChange={onMultiFiles(addBatchBamFiles)} />
              </label>
              <VcfCutoffsFields
                minAf={batchVcfCutoffs.min_af}
                minDepth={batchVcfCutoffs.min_depth}
                disabled={batchSubmitting}
                onChangeMinAf={(value) => setBatchVcfCutoffs((prev) => ({ ...prev, min_af: value }))}
                onChangeMinDepth={(value) => setBatchVcfCutoffs((prev) => ({ ...prev, min_depth: value }))}
                detailsClassName="profile-advanced-options batch-advanced-options"
                labelClassName="batch-settings-label"
                freqHelpLabel="Batch frequency cutoff help"
                freqHelpTitle="Minimum allele frequency from 0 to 1 for all batch VCF runs."
                covHelpLabel="Batch coverage cutoff help"
                covHelpTitle="Minimum read depth for all batch VCF runs."
              />
            </div>
          ) : batchMode === 'fasta' ? (
            <div className="profile-upload-row">
              <label>
                <span className="input-label-row">
                  FASTA files <InfoButton label="Batch FASTA help" title="Upload one or more consensus FASTA files." />
                </span>
                <input type="file" multiple accept=".fasta,.fa,.fna" disabled={batchSubmitting} onChange={onMultiFiles(addBatchFastaFiles)} />
              </label>
            </div>
          ) : (
            <div className="profile-upload-row profile-upload-row-batch-json">
              <label>
                <span className="input-label-row">
                  Results JSON files{' '}
                  <InfoButton
                    label="Batch JSON help"
                    title="Upload one or more prior results JSON files exported by ResistanceProfiler. Each JSON is matched to its database by a unique project fingerprint."
                  />
                </span>
                <input type="file" multiple accept=".json" disabled={batchSubmitting} onChange={onMultiFiles(addBatchJsonFiles)} />
              </label>
            </div>
          )}

          <BatchUploadedFilesTable
            batchMode={batchMode}
            batchFiles={batchFiles}
            batchMaxSamples={batchMaxSamples}
            batchSubmitting={batchSubmitting}
            attachBatchBam={attachBatchBam}
            removeBatchFile={removeBatchFile}
            resetBatch={resetBatch}
          />

          {batchMode === 'vcf' && batchReferenceFasta ? (
            <p className="field-optional">Uploaded reference: {batchReferenceFasta.name}</p>
          ) : null}

          <div className="profile-analyze-row">
            <button
              type="button"
              className="analyze-primary"
              onClick={() => submitBatch()}
              disabled={
                isBatchUploading
                || batchSubmitting
                || batchRateLimitCooldown > 0
                || batchFiles.length === 0
                || (batchMode === 'vcf' && !batchReferenceFasta)
              }
            >
              {batchSubmitting ? <><Spinner /> Submitting...</> : 'Submit batch'}
            </button>
            {selectedDatabase && !batchSubmitting ? (
              <span className="field-optional" style={{ marginLeft: '0.5rem' }}>using {selectedDatabase.display_name}</span>
            ) : null}
          </div>
          {batchRateLimitCooldown > 0 ? <p className="status">{`Rate limit reached. Try again in ${batchRateLimitCooldown}s.`}</p> : null}
          {batchError ? <p className="status" role="alert" style={{ color: 'var(--color-error)' }}>{batchError}</p> : null}
        </>
      ) : (
        <>
          {batchRateLimitCooldown > 0 ? (
            <p className="status">{`Rate limit reached. Retrying in ${batchRateLimitCooldown}s.`}</p>
          ) : null}
          {batchError ? (
            <p className="status" role="alert" style={{ color: 'var(--color-error)' }}>{batchError}</p>
          ) : null}
          {isBatchPolling ? (
            <p className="status"><Spinner /> Polling batch status...</p>
          ) : null}
          {!isBatchPolling
            && !batchSubmitting
            && !batchError
            && batchSamples.some((sample) => sample.status === 'queued' || sample.status === 'running') ? (
              <p className="status">Batch status polling stopped. Completed results remain available below.</p>
            ) : null}
          <div className="table-wrap mutation-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sample</th>
                  <th>Status</th>
                  <th>Error</th>
                  <th>HTML</th>
                  <th>PDF</th>
                  <th>JSON</th>
                  <th>TSV</th>
                </tr>
              </thead>
              <tbody>
                {batchSamples.map((sample) => (
                  <tr key={sample.job_id}>
                    <td>{sample.sample_name}</td>
                    <td>
                      <span className={`job-status-badge status-${sample.status}`}>
                        {sample.status}
                      </span>
                    </td>
                    <td>{sample.errorMessage || '—'}</td>
                    <ArtifactTableCells
                      htmlPath={sample.reportHtmlPath}
                      pdfPath={sample.reportPdfPath}
                      jsonPath={sample.reportJsonPath}
                      tsvPath={sample.reportTsvPath}
                      buildReportUrl={buildReportUrl}
                      buildArtifactUrl={buildArtifactUrl}
                    />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="profile-analyze-row">
            <button
              type="button"
              className="analyze-primary"
              onClick={() => downloadAllBatchArtifacts()}
              disabled={isBatchDownloadBusy}
            >
              {isBatchDownloadBusy ? <><Spinner /> Preparing...</> : 'Download all'}
            </button>
            {isBatchPolling ? (
              <button
                type="button"
                className="button-link report-action-btn"
                onClick={() => stopBatchPolling?.()}
              >
                Stop polling
              </button>
            ) : null}
            <button type="button" className="analyze-primary" onClick={() => resetBatch()}>
              New batch
            </button>
          </div>
        </>
      )}
    </div>
  );
}
