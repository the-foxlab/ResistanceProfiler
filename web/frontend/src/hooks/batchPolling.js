import { FRONTEND_CONFIG } from '../config';
import { apiGet, formatUserError } from '../api';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed']);

function formatBatchSampleView(sample) {
  const result = sample.result;
  return {
    job_id: sample.job_id,
    sample_name: sample.sample_name,
    status: sample.status,
    errorMessage: sample.errorMessage || null,
    report_url: result ? result.report_html_path : null,
    reportHtmlPath: result ? result.report_html_path : null,
    reportPdfPath: result ? result.report_pdf_path : null,
    reportJsonPath: result ? result.report_json_path : null,
    reportTsvPath: result ? result.report_tsv_path : null,
    artifactIds: result ? result.artifact_ids : null,
  };
}

/** Poll batch jobs until every sample reaches a terminal state. */
export async function pollBatchJobs({ initialSamples, onSamplesChange, onSucceededResults }) {
  let samples = initialSamples.map((sample) => ({ ...sample }));
  for (;;) {
    const pending = samples.filter((sample) => !TERMINAL_STATUSES.has(sample.status));
    if (pending.length === 0) break;

    await new Promise((resolve) => setTimeout(resolve, FRONTEND_CONFIG.profile.jobPollIntervalMs));
    const updated = await Promise.all(
      pending.map(async (sample) => {
        try {
          const payload = await apiGet(`/api/jobs/${sample.job_id}`);
          return {
            ...sample,
            status: payload.status,
            result: payload.result || null,
            errorMessage: payload.error ? formatUserError(payload.error) : null,
          };
        } catch (error) {
          return { ...sample, errorMessage: formatUserError(error.message) };
        }
      }),
    );

    const updatedById = new Map(updated.map((item) => [item.job_id, item]));
    samples = samples.map((sample) => updatedById.get(sample.job_id) || sample);

    const succeededResults = samples
      .filter((sample) => sample.status === 'succeeded' && sample.result)
      .map((sample) => sample.result);
    if (succeededResults.length > 0) {
      onSucceededResults(succeededResults);
    }
    onSamplesChange(samples.map(formatBatchSampleView));
  }
}
