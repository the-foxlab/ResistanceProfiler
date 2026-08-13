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
    // 1. Top-bar database selector — explain switching databases.
    {
      id: 'database-selector',
      targetSelector: '.topbar-db-bar',
      title: 'Choose your database',
      body: 'The database you select here determines which resistance rules and references are used for every analysis in this session. Switch it any time before running a new analysis.',
      before: () => setActiveMode('analyze'),
    },
    // 2. VCF file upload.
    {
      id: 'vcf-file',
      targetSelector: '[data-tour-target="vcf-file"]',
      title: 'VCF mode — variant file',
      body: 'Upload a VCF (.vcf or .vcf.gz) with standard headers. The VCF may be multi-chrom; each CHROM must match one record in the reference FASTA by header name.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 3. Reference FASTA.
    {
      id: 'vcf-reference',
      targetSelector: '[data-tour-target="vcf-reference"]',
      title: 'VCF mode — reference FASTA',
      body: 'Provide a matching reference FASTA. It must match the VCF coordinate system and may be multi-record (one FASTA record per VCF CHROM); each record header must match a CHROM name.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 4. BAM file (optional, coverage).
    {
      id: 'vcf-bam',
      targetSelector: '[data-tour-target="vcf-bam"]',
      title: 'VCF mode — BAM (optional)',
      body: 'An optional sorted BAM file for coverage evaluation. A BAM index is generated automatically. This is only needed when you want coverage-based filtering.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 5. Sample name.
    {
      id: 'vcf-sample-name',
      targetSelector: '[data-tour-target="vcf-sample-name"]',
      title: 'VCF mode — sample name',
      body: 'Give your sample a name. This label appears on the report so you can identify which run produced which output.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 6. Frequency cutoff.
    {
      id: 'vcf-frequency-cutoff',
      targetSelector: '[data-tour-target="vcf-frequency-cutoff"]',
      title: 'VCF mode — frequency cutoff',
      body: 'Set the minimum allele frequency (0 to 1). Variants below this threshold are ignored and will not appear in the report.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 7. Coverage cutoff.
    {
      id: 'vcf-coverage-cutoff',
      targetSelector: '[data-tour-target="vcf-coverage-cutoff"]',
      title: 'VCF mode — coverage cutoff',
      body: 'Set the minimum read depth required to include a position. Positions below this depth are excluded from variant calling.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('vcf');
      },
    },
    // 8. FASTA mode — switch from VCF to FASTA.
    {
      id: 'fasta-mode',
      targetSelector: '.profile-upload-row-fasta',
      title: 'FASTA mode',
      body: 'Switch to FASTA mode to upload a consensus FASTA sequence. The reference is matched automatically by sequence identity, so no reference FASTA is needed here. Provide a sample name for your report.',
      before: () => {
        setActiveMode('analyze');
        setAnalyzeSubMode('single');
        setActiveProfileMode('fasta');
      },
    },
    // 9. Regenerate mode — results JSON upload.
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
    // 10. Analyze / Cancel job buttons + "using {db}" indicator + status error.
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
    // 11. Previous-reports dropdown + Open / Download PDF / Download JSON.
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
    // 12. Reports — highlight the Reports tile in the sidebar.
    {
      id: 'reports-table',
      targetSelector: '[data-tour-target="sidebar-results"]',
      title: 'Session results',
      body: 'Every analysis from this session is listed in the Reports tab (results are cleared on page reload). Each row links to its HTML report and offers PDF and JSON downloads. Use "Download all" or select rows and "Download selected" for a bundle.',
      before: () => setActiveMode('results'),
    },
    // 13. Comparison — highlight the Reports tile (same tab, comparison lives below the table).
    {
      id: 'comparison-heatmap',
      targetSelector: '[data-tour-target="sidebar-results"]',
      title: 'Compare samples as a heatmap',
      body: 'As soon as you have results, you can select two or more comparable results (same database and reference), then "Compare selected" to build a mutation heatmap. Use "Select all comparable" to pick everything that matches, toggle "Non-synonymous only" or "DB hits only" to filter, and "Clear comparison" to start over. This view lives below the results table.',
      before: () => setActiveMode('results'),
    },
    // 14. Database Dashboard — highlight the Database tile in the sidebar.
    {
      id: 'database-dashboard',
      targetSelector: '[data-tour-target="sidebar-database"]',
      title: 'Database Dashboard',
      body: 'The Database Dashboard tab summarises the rules and mutations in the selected database with interactive plots.',
      before: () => setActiveMode('database'),
    },
    // 15. Browse Mutations — highlight the Mutations tile in the sidebar.
    {
      id: 'browse-mutations',
      targetSelector: '[data-tour-target="sidebar-mutations"]',
      title: 'Browse Mutations',
      body: 'The Browse Mutations tab lets you search and filter the single and combination rules in the selected database, and export them as TSV.',
      before: () => setActiveMode('mutations'),
    },
    // 16. About — highlight the About tile in the sidebar.
    {
      id: 'about',
      targetSelector: '[data-tour-target="sidebar-about"]',
      title: 'About',
      body: 'The About tab explains how ResistanceProfiler works, the rule nomenclature, and how to run it from the CLI.',
      before: () => setActiveMode('about'),
    },
    // 17. Final step — highlight nothing; link to official GitHub docs for full detail.
    {
      id: 'docs-handoff',
      targetSelector: null,
      title: 'Want the full detail?',
      body: 'This tour covers the essentials. For in-depth explanations of every output, the results table, report downloads, and the comparison heatmap, read the ',
      before: () => setActiveMode('about'),
      link: { label: 'official documentation.', href: TOUR_DOCS_OUTPUT_URL },
    },
  ];
}
