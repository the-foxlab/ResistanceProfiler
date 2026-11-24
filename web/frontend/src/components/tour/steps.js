// Official documentation site linked from the final tour step.
export const TOUR_DOCS_URL = 'https://the-foxlab.github.io/ResistanceProfiler/';
export const TOUR_DOCS_OUTPUT_URL = `${TOUR_DOCS_URL}output/`;

/**
 * Build the ordered guided-tour steps bound to real navigation setters from
 * `useDashboardLogic` so each spotlight points at live DOM.
 */
export function buildTourSteps({ navigate, setActiveProfileMode, setAnalyzeSubMode }) {
  const goSingle = (mode, { openAdvanced = false } = {}) => () => {
    navigate('/analysis');
    setAnalyzeSubMode('single');
    if (mode) {
      setActiveProfileMode(mode);
    }
    if (openAdvanced) {
      setTimeout(() => {
        const advancedOptions = document.querySelector('.profile-upload-row-vcf .profile-advanced-options');
        if (advancedOptions) advancedOptions.open = true;
      }, 0);
    }
  };

  return [
    {
      id: 'database-selector',
      targetSelector: '.topbar-db-bar',
      title: 'Choose your database',
      body: 'The database you select here determines which resistance rules and references are used for every analysis in this session. Switch it any time before running a new analysis.',
      before: () => navigate('/analysis'),
    },
    {
      id: 'vcf-file',
      targetSelector: '[data-tour-target="vcf-file"]',
      title: 'VCF mode — variant file',
      body: 'Upload a VCF (.vcf or .vcf.gz) with standard headers. The VCF may be multi-chrom; each CHROM must match one record in the reference FASTA by header name.',
      before: goSingle('vcf'),
    },
    {
      id: 'vcf-reference',
      targetSelector: '[data-tour-target="vcf-reference"]',
      title: 'VCF mode — reference FASTA',
      body: 'Provide a matching reference FASTA. It must match the VCF coordinate system and may be multi-record (one FASTA record per VCF CHROM); each record header must match a CHROM name.',
      before: goSingle('vcf'),
    },
    {
      id: 'vcf-bam',
      targetSelector: '[data-tour-target="vcf-bam"]',
      title: 'VCF mode — BAM (optional)',
      body: 'An optional sorted BAM file for coverage evaluation. A BAM index is generated automatically. This is only needed when you want coverage-annotation and combined SNP codon analysis.',
      before: goSingle('vcf', { openAdvanced: true }),
    },
    {
      id: 'vcf-sample-name',
      targetSelector: '[data-tour-target="vcf-sample-name"]',
      title: 'VCF mode — sample name',
      body: 'Give your sample a name. This label appears on the report.',
      before: goSingle('vcf', { openAdvanced: true }),
    },
    {
      id: 'vcf-frequency-cutoff',
      targetSelector: '[data-tour-target="vcf-frequency-cutoff"]',
      title: 'VCF mode — frequency cutoff',
      body: 'Set the minimum allele frequency (0 to 1). Variants below this threshold are ignored and will not appear in the report.',
      before: goSingle('vcf'),
    },
    {
      id: 'vcf-coverage-cutoff',
      targetSelector: '[data-tour-target="vcf-coverage-cutoff"]',
      title: 'VCF mode — coverage cutoff',
      body: 'Set the minimum read depth required to include a position. Variants below this threshold are ignored and will not appear in the report.',
      before: goSingle('vcf'),
    },
    {
      id: 'fasta-mode',
      targetSelector: '.profile-upload-row-fasta',
      title: 'FASTA mode',
      body: 'Switch to FASTA mode to upload a consensus FASTA sequence. The reference is matched automatically by sequence identity.',
      before: goSingle('fasta'),
    },
    {
      id: 'regenerate-mode',
      targetSelector: '.profile-upload-row-regenerate',
      title: 'Regenerate from JSON',
      body: 'Upload a previous results JSON exported by ResistanceProfiler to rebuild its report. This is matched by a unique database ID, so regeneration will not work after that database has been updated.',
      before: goSingle('regenerate'),
    },
    {
      id: 'analyze-button',
      targetSelector: '.profile-input-card .profile-analyze-row',
      title: 'Run or cancel',
      body: 'Press Analyze to start the job. While a job runs you can cancel it. The indicator shows which database is in use, and any errors appear here in red.',
      before: goSingle('vcf'),
    },
    {
      id: 'previous-reports',
      targetSelector: '.analyze-report-actions',
      title: 'Reopen previous reports',
      body: 'Reports from earlier in this session are listed here. Open one in a new tab, or download its PDF, JSON, or TSV export without re-running the analysis.',
      before: goSingle('vcf'),
    },
    {
      id: 'analyze-submode',
      targetSelector: '.analyze-submode-row',
      title: 'One sample or many',
      body: 'Use this toggle to switch between "One Sample" and "Multiple Samples". The batch mode lets you submit multiple VCF or FASTA files at once with a shared reference, per-sample BAMs, and a single set of cutoffs — ideal when you have a plate or folder of samples to profile together.',
      before: goSingle(null),
    },
    {
      id: 'reports-table',
      targetSelector: '[data-tour-target="sidebar-results"]',
      title: 'Session results',
      body: 'Every analysis from this session is listed in the Reports tab (results are cleared on page reload). Each row links to its HTML report and offers PDF and JSON downloads. Use "Download all" or select rows and "Download selected" for a bundle.',
      before: () => navigate('/analysis/reports'),
    },
    {
      id: 'comparison-heatmap',
      targetSelector: '[data-tour-target="sidebar-results"]',
      title: 'Compare samples as a heatmap',
      body: 'As soon as you have results, you can select two or more comparable results (same database and reference), then "Compare selected" to build a mutation heatmap.',
      before: () => navigate('/analysis/reports'),
    },
    {
      id: 'database-dashboard',
      targetSelector: '[data-tour-target="sidebar-database"]',
      title: 'Database Dashboard',
      body: 'The Database Dashboard tab summarises the rules and mutations in the selected database with interactive plots.',
      before: () => navigate('/databases'),
    },
    {
      id: 'browse-mutations',
      targetSelector: '[data-tour-target="sidebar-mutations"]',
      title: 'Browse Mutations',
      body: 'The Browse Mutations tab lets you search and filter the single and combination rules in the selected database, and export them as TSV.',
      before: () => navigate('/databases/mutations'),
    },
    {
      id: 'about',
      targetSelector: '[data-tour-target="sidebar-about"]',
      title: 'About',
      body: 'The About tab explains how ResistanceProfiler works, the rule nomenclature, and how to run it from the CLI.',
      before: () => navigate('/about'),
    },
    {
      id: 'docs-handoff',
      targetSelector: null,
      title: 'Want the full detail?',
      body: 'This tour covers the essentials. For in-depth explanations read the ',
      before: () => navigate('/about'),
      link: { label: 'official documentation.', href: TOUR_DOCS_OUTPUT_URL },
    },
  ];
}
