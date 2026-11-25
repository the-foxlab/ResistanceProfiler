import fileIconSrc from '../../../assets/file.svg';
import { useOptionalSingleAnalyzeLogic } from '../../../DashboardContext';
import { InfoButton } from '../../InfoButton';
import { Spinner } from '../../Spinner';
import { VcfCutoffsFields } from './VcfCutoffsFields';

const REPORT_DOWNLOAD_ACTIONS = [
  { key: 'pdfPath', label: 'Download PDF' },
  { key: 'jsonPath', label: 'Download JSON' },
  { key: 'tsvPath', label: 'Download TSV' },
];

export function SingleAnalyzePanel({ logic: logicProp }) {
  const contextLogic = useOptionalSingleAnalyzeLogic();
  const logic = logicProp ?? contextLogic;
  const {
    selectedDatabase,
    vcfInput,
    setVcfInput,
    fastaInput,
    setFastaInput,
    jsonInputId,
    isRegenerateBusy,
    runRegenerateFromJson,
    uploadFastaFile,
    uploadVcfFile,
    uploadReferenceFile,
    uploadBamFile,
    uploadJsonFile,
    isUploading,
    activeProfileMode,
    isProfileBusy,
    canCancelJob,
    isCancelingJob,
    cancelActiveJob,
    runSelectedProfile,
    statusError,
    selectedProfileReportPath,
    setSelectedProfileReportPath,
    reportOptions,
    buildReportUrl,
    buildArtifactUrl,
    inlineReportPath,
  } = logic;

  const selectedReportOption = reportOptions.find(
    (option) => option.path === selectedProfileReportPath,
  ) || null;
  const isRegenerate = activeProfileMode === 'regenerate';
  const isActiveRunBusy = isRegenerate ? isRegenerateBusy : isProfileBusy;
  const isAnyRunBusy = isProfileBusy || isRegenerateBusy;
  const isSubmitDisabled = isUploading || isAnyRunBusy || (
    isRegenerate
      ? !jsonInputId
      : activeProfileMode === 'vcf'
        ? (!vcfInput.vcf_id || !vcfInput.reference_id)
        : !fastaInput.fasta_id
  );

  const onSingleFile = (uploadFn) => (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) {
      uploadFn(file);
    }
  };

  return (
    <div className="profile-input-subtile section-subtile">
      {activeProfileMode === 'vcf' ? (
        <div className="profile-upload-row profile-upload-row-vcf">
          <label data-tour-target="vcf-file">
            <span className="input-label-row">
              VCF file{' '}
              <InfoButton
                label="VCF help"
                title="Upload one VCF (.vcf or .vcf.gz) with standard headers. The VCF may be multi-chrom; each CHROM must match one record in the reference FASTA by header name."
              />
            </span>
            <input type="file" accept=".vcf,.vcf.gz" disabled={isAnyRunBusy} onChange={onSingleFile(uploadVcfFile)} />
          </label>
          <label data-tour-target="vcf-reference">
            <span className="input-label-row">
              Reference FASTA{' '}
              <InfoButton
                label="Reference FASTA help"
                title="Reference FASTA must match the VCF coordinate system. May be multi-record (one FASTA record per VCF CHROM); each record header must match a CHROM name."
              />
            </span>
            <input type="file" accept=".fasta,.fa,.fna" disabled={isAnyRunBusy} onChange={onSingleFile(uploadReferenceFile)} />
          </label>
          <label data-tour-target="vcf-bam">
            <span className="label-text input-label-row">
              BAM file <span className="field-optional">(optional)</span>{' '}
              <InfoButton
                label="BAM help"
                title="Optional sorted BAM. A BAM index is generated automatically. Used for coverage evaluation."
              />
            </span>
            <input type="file" accept=".bam" disabled={isAnyRunBusy} onChange={onSingleFile(uploadBamFile)} />
          </label>
          <label data-tour-target="vcf-sample-name">
            Sample name
            <input
              className="sample-name-input"
              value={vcfInput.sample}
              disabled={isAnyRunBusy}
              onChange={(event) => setVcfInput((prev) => ({ ...prev, sample: event.target.value }))}
            />
          </label>
          <VcfCutoffsFields
            minAf={vcfInput.min_af}
            minDepth={vcfInput.min_depth}
            disabled={isAnyRunBusy}
            onChangeMinAf={(value) => setVcfInput((prev) => ({ ...prev, min_af: value }))}
            onChangeMinDepth={(value) => setVcfInput((prev) => ({ ...prev, min_depth: value }))}
            freqTourTarget="vcf-frequency-cutoff"
            covTourTarget="vcf-coverage-cutoff"
            freqHelpLabel="Frequency cutoff help"
            freqHelpTitle="Minimum allele frequency from 0 to 1. Variants below this value are ignored."
            covHelpLabel="Coverage cutoff help"
            covHelpTitle="Minimum read depth required for including a position."
          />
        </div>
      ) : null}

      {activeProfileMode === 'fasta' ? (
        <div className="profile-upload-row profile-upload-row-fasta">
          <label>
            <span className="input-label-row">
              FASTA file <InfoButton label="FASTA help" title="Upload one consensus FASTA sequence file." />
            </span>
            <input type="file" accept=".fasta,.fa,.fna,.faa" disabled={isAnyRunBusy} onChange={onSingleFile(uploadFastaFile)} />
          </label>
          <label>
            Sample name
            <input
              className="sample-name-input"
              value={fastaInput.sample}
              disabled={isAnyRunBusy}
              onChange={(event) => setFastaInput((prev) => ({ ...prev, sample: event.target.value }))}
            />
          </label>
        </div>
      ) : null}

      {isRegenerate ? (
        <div className="profile-upload-row profile-upload-row-regenerate">
          <label>
            <span className="input-label-row">
              Results JSON{' '}
              <InfoButton
                label="Results JSON help"
                title="Upload a prior results JSON exported by ResistanceProfiler. This is matched by a unique db ID. After db updates, the json regenerate will not work."
              />
            </span>
            <input type="file" accept=".json" disabled={isAnyRunBusy} onChange={onSingleFile(uploadJsonFile)} />
          </label>
        </div>
      ) : null}

      <div className="profile-analyze-row">
        <button
          type="button"
          className="analyze-primary"
          onClick={() => (isRegenerate ? runRegenerateFromJson() : runSelectedProfile())}
          disabled={isSubmitDisabled}
        >
          {isActiveRunBusy ? <><Spinner /> {isRegenerate ? 'Regenerate' : 'Analyze'}</> : (isRegenerate ? 'Regenerate' : 'Analyze')}
        </button>
        {canCancelJob ? (
          <button
            type="button"
            className="button-link report-action-btn"
            onClick={() => cancelActiveJob()}
            disabled={isCancelingJob}
          >
            {isCancelingJob ? 'Canceling...' : 'Cancel job'}
          </button>
        ) : null}
        {selectedDatabase && !isProfileBusy && !isRegenerateBusy ? (
          <span className="field-optional" style={{ marginLeft: '0.5rem' }}>using {selectedDatabase.display_name}</span>
        ) : null}
        {statusError ? (
          <p className="status" role="alert" style={{ color: 'var(--color-error)', marginLeft: '1rem' }}>{statusError}</p>
        ) : null}
      </div>

      {reportOptions.length > 0 ? (
        <div className="inline-actions report-actions analyze-report-actions">
          <select
            aria-label="Select session report"
            value={selectedProfileReportPath}
            onChange={(event) => setSelectedProfileReportPath(event.target.value)}
          >
            {reportOptions.map((option) => (
              <option key={option.path} value={option.path}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button-link report-action-btn"
            onClick={() => {
              if (selectedProfileReportPath) {
                window.open(buildReportUrl(selectedProfileReportPath), '_blank', 'noopener,noreferrer');
              }
            }}
            disabled={!selectedProfileReportPath}
          >
            Open in new tab
          </button>
          {REPORT_DOWNLOAD_ACTIONS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className="button-link report-action-btn download-action-btn"
              onClick={() => {
                if (selectedReportOption?.[key]) {
                  window.open(buildArtifactUrl(selectedReportOption[key]), '_blank', 'noopener,noreferrer');
                }
              }}
              disabled={!selectedReportOption?.[key]}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {!inlineReportPath ? (
        <div className="report-placeholder">
          <div className="report-placeholder-icon-wrap">
            <img src={fileIconSrc} alt="" className="report-placeholder-icon" aria-hidden="true" />
          </div>
          <p className="report-placeholder-text">Your analysis report will open here</p>
        </div>
      ) : null}
    </div>
  );
}
