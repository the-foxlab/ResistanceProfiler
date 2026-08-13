// Official documentation site (GitHub Pages). The in-app tour's final step links here
// so users can read the full results/output detail (which also serves CLI users).
export const TOUR_DOCS_URL = 'https://the-foxlab.github.io/ResistanceProfiler/';
export const TOUR_DOCS_OUTPUT_URL = `${TOUR_DOCS_URL}output/`;

/**
 * Build the ordered guided-tour steps, binding each step's `before` hook to the real
 * navigation setters from `useDashboardLogic`. Calling a setter drives the app exactly
 * as a user would, so the spotlight points at live DOM.
 *
 * @param {object} navigation - Setters from useDashboardLogic.
 * @param {(mode: string) => void} navigation.setActiveMode
 * @param {(mode: string) => void} navigation.setActiveProfileMode
 * @param {(mode: string) => void} navigation.setAnalyzeSubMode
 * @returns {Array} ordered tour steps
 */
export function buildTourSteps({ setActiveMode, setActiveProfileMode, setAnalyzeSubMode }) {
  return [
    // (a) Top-bar database selector — explain switching databases.
    {
      id: 'database-selector',
      targetSelector: '.topbar-db-bar',
      title: 'Choose your database',
      body: 'The database you select here determines which resistance rules and references are used for every analysis in this session. Switch it any time before running a new analysis.',
      before: () => setActiveMode('analyze'),
    },
    // (b) Sidebar rail — overview of the 5 modes.
    {
      id: 'sidebar-rail',
      targetSelector: '.sidebar-rail',
      title: 'Navigate the app',
      body: 'The sidebar switches between the five areas: Analysis (run samples), Reports (view results), Database Dashboard (explore a database), Browse Mutations (search rules), and About.',
      before: () => setActiveMode('analyze'),
    },
    // (c) Analysis tab, sub-mode toggle (One Sample / Multiple Samples).
    {
      id: 'analyze-submode',
      targetSelector: '.analyze-submode-row',
      title: 'One sample or many',
      body: 'Choose "One Sample" to profile a single file, or "Multiple Samples" to submit a batch of up to 25 samples per batch and minute.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
      },
    },
    // (d) Single-sample VCF mode — VCF, reference FASTA, optional BAM, sample name, cutoffs.
    {
      id: 'vcf-mode',
      targetSelector: '.profile-upload-row-vcf',
      title: 'VCF mode',
      body: 'Upload a VCF (.vcf or .vcf.gz) plus a matching reference FASTA. The VCF may be multi-chrom; each CHROM must match a record in the reference FASTA. The optional BAM is used only for coverage evaluation. Set the frequency cutoff (minimum allele frequency) and coverage cutoff (minimum read depth) to filter variants.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // (e) FASTA mode — FASTA file + sample name.
    {
      id: 'fasta-mode',
      targetSelector: '.profile-upload-row-fasta',
      title: 'FASTA mode',
      body: 'Upload a consensus FASTA sequence. The reference is matched automatically by sequence identity, so no reference FASTA is needed here. Provide a sample name for your report.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('fasta');
      },
    },
    // (f) Regenerate mode — results JSON upload.
    {
      id: 'regenerate-mode',
      targetSelector: '.profile-upload-row-regenerate',
      title: 'Regenerate from JSON',
      body: 'Upload a previous results JSON exported by ResistanceProfiler to rebuild its report. This is matched by a unique database ID, so regeneration will not work after that database has been updated.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('regenerate');
      },
    },
    // (g) Analyze / Cancel job buttons + "using {db}" indicator + status error.
    {
      id: 'analyze-button',
      targetSelector: '.profile-input-card .profile-analyze-row',
      title: 'Run or cancel',
      body: 'Press Analyze to start the job. While a job runs you can cancel it. The indicator shows which database is in use, and any errors appear here in red.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // (h) Previous-reports dropdown + Open / Download PDF / Download JSON.
    {
      id: 'previous-reports',
      targetSelector: '.analyze-report-actions',
      title: 'Reopen previous reports',
      body: 'Reports from earlier in this session are listed here. Open one in a new tab, or download its PDF or JSON export without re-running the analysis.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // (i) Batch sub-mode — VCF/FASTA batch, shared reference, BAM, cutoffs, submit, download, rate limit.
    {
      id: 'batch-mode',
      targetSelector: '.profile-upload-row-batch-vcf',
      title: 'Batch multiple samples',
      body: 'Switch to "Multiple Samples" to upload many VCF or FASTA files at once, attach a shared reference FASTA (and optional BAMs for coverage), set per-sample cutoffs, then submit. You can download all artifacts together. Batches are limited to 25 samples per batch and minute.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('batch');
      },
    },
    // (j) Reports tab — session results table, per-row links, Download all/selected.
    {
      id: 'reports-table',
      targetSelector: '.table-wrap.mutation-table-wrap',
      title: 'Session results',
      body: 'Every analysis from this session is listed here (results are cleared on page reload). Each row links to its HTML report and offers PDF and JSON downloads. Use "Download all" or select rows and "Download selected" for a bundle.',
      before: () => setActiveMode('results'),
    },
    // (k) Reports comparison flow — Select all comparable, Compare selected, Clear, filters, heatmap.
    {
      id: 'comparison-heatmap',
      targetSelector: '.comparison-section, .tab-primary-tile .profile-analyze-row',
      title: 'Compare samples side by side',
      body: 'Select two or more comparable results (same database and reference), then "Compare selected" to build a mutation heatmap. Use "Select all comparable" to pick everything that matches, toggle "Non-synonymous only" or "DB hits only" to filter the heatmap, and "Clear comparison" to start over. This view is easy to miss — it lives below the results table.',
      before: () => setActiveMode('results'),
    },
    // (l) Short pointer step for Database Dashboard.
    {
      id: 'database-dashboard',
      targetSelector: '.sidebar-rail',
      title: 'Database Dashboard',
      body: 'The Database Dashboard tab summarises the rules and mutations in the selected database with interactive plots.',
      before: () => setActiveMode('database'),
    },
    // (m) Short pointer step for Browse Mutations.
    {
      id: 'browse-mutations',
      targetSelector: '.sidebar-rail',
      title: 'Browse Mutations',
      body: 'The Browse Mutations tab lets you search and filter the single and combination rules in the selected database, and export them as TSV.',
      before: () => setActiveMode('mutations'),
    },
    // (n) Short pointer step for About.
    {
      id: 'about',
      targetSelector: '.sidebar-rail',
      title: 'About',
      body: 'The About tab explains how ResistanceProfiler works, the rule nomenclature, and how to run it from the CLI.',
      before: () => setActiveMode('about'),
    },
    // (o) Final step — link to official GitHub docs for full detail.
    {
      id: 'docs-handoff',
      targetSelector: '.about-hero-actions',
      title: 'Want the full detail?',
      body: `This tour covers the essentials. For in-depth explanations of every output, the results table, report downloads, and the comparison heatmap, read the official documentation.`,
      before: () => setActiveMode('about'),
      link: { label: 'Open documentation', href: TOUR_DOCS_OUTPUT_URL },
    },
  ];
}
