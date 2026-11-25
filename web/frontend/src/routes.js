/** Per-route shell metadata (URL is the single source of truth). */
export const ROUTE_META = {
  '/': { title: 'ResPro | Home', showsSidebar: false, showsDatabaseSelector: false },
  '/analysis': { title: 'ResPro | Analysis', heading: 'Analyze', description: 'Profile VCF files, consensus FASTA sequences, or regenerate a previous report from JSON. BAM files are optional and can be used for coverage analysis.', showsSidebar: true, showsDatabaseSelector: true },
  '/analysis/reports': { title: 'ResPro | Reports', heading: 'Session results', description: 'All analysis outputs from this session. Results are cleared on page reload.', showsSidebar: true, showsDatabaseSelector: false },
  '/databases': { title: 'ResPro | Databases', heading: 'Database Dashboard', description: 'Overview and visual summaries of the active resistance database.', showsSidebar: true, showsDatabaseSelector: true },
  '/databases/mutations': { title: 'ResPro | Browse Mutations', heading: 'Browse mutations', showsSidebar: true, showsDatabaseSelector: true },
  '/databases/compare': { title: 'ResPro | Compare Databases', heading: 'Compare databases', showsSidebar: true, showsDatabaseSelector: false },
  '/about': { title: 'ResPro | About', showsSidebar: false, showsDatabaseSelector: false },
};

const DEFAULT_TITLE = 'ResPro';

export function resolveRouteMeta(pathname) {
  return ROUTE_META[pathname] || { title: DEFAULT_TITLE, showsSidebar: false, showsDatabaseSelector: false };
}

/** Sidebar entries per workspace section; rendered only on matching routes. */
export const SIDEBAR_SECTIONS = [
  {
    label: 'Analysis',
    basePath: '/analysis',
    items: [
      { path: '/analysis', label: 'Analyze', page: 'analyze' },
      { path: '/analysis/reports', label: 'Reports', page: 'results' },
    ],
  },
  {
    label: 'Databases',
    basePath: '/databases',
    items: [
      { path: '/databases', label: 'Dashboard', page: 'database' },
      { path: '/databases/mutations', label: 'Browse Mutations', page: 'mutations' },
      { path: '/databases/compare', label: 'Compare Databases', page: 'compare' },
    ],
  },
];
