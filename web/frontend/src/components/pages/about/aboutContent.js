export const ABOUT_TOC = [
  { id: 'about-scope', label: 'Project scope and how it works' },
  { id: 'about-nomenclature', label: 'Rule nomenclature' },
  { id: 'about-combinations', label: 'Rule combinations' },
  { id: 'about-algorithms', label: 'Interpretation algorithms' },
  { id: 'about-databases', label: 'Databases' },
  { id: 'about-cli', label: 'CLI and extended functionality' },
  { id: 'about-governance', label: 'Contributing, data usage, licensing' },
  { id: 'about-funding', label: 'Funding' },
];

export const ABOUT_KNOWLEDGE_IDS = ['about-nomenclature', 'about-combinations', 'about-algorithms'];

export const ABOUT_CLI_COMMANDS = [
  'respro databases --download db_name --output my_folder/',
  'respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db',
  'respro add --project project.db --rules more_rules.tsv --formula-rules combinatorial_rules.tsv',
  'respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/ --export json',
  'respro fasta --project project.db --fasta sample.fasta --output report/',
  'respro regenerate --project project.db --json report/sample.results.json --output report/',
];

export const ABOUT_DOCKER_COMMAND = 'docker compose -f docker-compose.web.yml up --build';

export const ABOUT_DOCS_ROOT = 'https://the-foxlab.github.io/ResistanceProfiler/';
export const ABOUT_DOCS = {
  workflow: `${ABOUT_DOCS_ROOT}how-it-works/#pipeline-overview`,
  nomenclature: `${ABOUT_DOCS_ROOT}rules-format/#normalization-examples-input-canonical-interpretation`,
  phenotypeNormalization: `${ABOUT_DOCS_ROOT}rules-format/#phenotype-normalization`,
  combinations: `${ABOUT_DOCS_ROOT}how-it-works/#rule-matching`,
  algorithms: `${ABOUT_DOCS_ROOT}algorithms/#how-respro-evaluates-resistance`,
  databaseSetup: `${ABOUT_DOCS_ROOT}database-preparation/#create-a-new-project-database`,
  databaseDownload: `${ABOUT_DOCS_ROOT}cli-reference/#download-a-maintained-database`,
  cli: `${ABOUT_DOCS_ROOT}cli-reference/#profile-fasta-input`,
  cliVcf: `${ABOUT_DOCS_ROOT}cli-reference/#profile-vcf-input`,
  webData: `${ABOUT_DOCS_ROOT}webapp/#data-and-filesystem`,
  databaseMetadata: `${ABOUT_DOCS_ROOT}database-preparation/#optional-metadata-json`,
};

export const ABOUT_DATABASES_URL = 'https://github.com/the-foxlab/respro-databases';
export const ABOUT_FUNDING_URL = 'https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/';

export const ABOUT_WORKFLOW_STEPS = [
  {
    title: 'Input',
    text: 'Provide a consensus sequence (FASTA) or called variants (VCF) with the reference used for variant calling. An optional BAM adds read-level coverage and co-occurrence evidence.',
  },
  {
    title: 'Reference matching',
    text: 'ResPro maps the sample to the reference sequence and annotated features in the project database. This puts variants in the coordinate system used by its rules.',
  },
  {
    title: 'Mutation detection',
    text: 'Nucleotide differences are mapped to annotated coding regions and translated into amino-acid substitutions or other coding effects where the sequence permits.',
  },
  {
    title: 'Rule evaluation',
    text: 'Observed amino-acid changes are checked against single-change rules and, when defined, formula rules that require a specific combination.',
  },
  {
    title: 'Report generation',
    text: 'The report presents matched rules, drug-level interpretations, frequencies, and coverage limitations, with downloadable formats for review.',
  },
];

export const ABOUT_SCOPE_NOTES = [
  {
    title: 'What you provide',
    text: 'A consensus sequence (FASTA), or a VCF plus the exact reference used to call its variants. An optional BAM provides aligned reads for coverage assessment and direct evidence about which variants occur in the same codon.',
  },
  {
    title: 'What you get',
    text: 'A per-drug interpretation summary, matched changes and their reported frequencies, and coverage notes identifying regions with insufficient or unavailable evidence. Results can be exported for review.',
  },
  {
    title: 'What it does not do',
    text: 'ResPro does not validate the sequencing assay or replace an accredited diagnostic. Interpretation is limited by reference matching, sequence quality, coverage, and the scope and currency of the selected rules; review these factors before drawing clinical conclusions.',
  },
];

export const ABOUT_NOMENCLATURE_ROWS = [
  { type: 'Substitution', example: 'A123V', meaning: 'Position 123 changed from A to V.' },
  { type: 'Anchored deletion', example: 'VG215V', meaning: 'G is deleted after the reference V at position 215.' },
  { type: 'Anchored insertion', example: 'V215VG', meaning: 'G is inserted after the reference V at position 215.' },
  { type: 'Frameshift', example: 'L201LfsX', meaning: 'Reading-frame shift after the reference L at position 201.' },
];

export const ABOUT_PHENOTYPE_RANKS = [
  { rank: '1', interpretation: 'Susceptible', labels: ['susceptible', 'sensitive', 'normal inhibition', 'ni', 'normal'] },
  { rank: '2', interpretation: 'Potential low-level resistance', labels: ['potential low-level resistance', 'possibly resistant', 'suspected reduced'] },
  { rank: '3', interpretation: 'Low-level resistance', labels: ['low-level resistance', 'reduced susceptibility', 'limited susceptibility'] },
  { rank: '4', interpretation: 'Intermediate', labels: ['intermediate', 'intermediate resistance', 'reduced inhibition', 'ri'] },
  { rank: '5', interpretation: 'Resistant', labels: ['resistant', 'high-level resistance', 'highly reduced inhibition', 'hri'] },
  { rank: '0', interpretation: 'Unknown', labels: ['unknown', 'not analysed', 'none'], trailingText: ', or a blank field' },
  { rank: '-1', interpretation: 'Contradictory', labels: ['contradictory', 'conflicting'] },
];

export const ABOUT_OPERATORS = [
  { pill: 'AND', variant: 'and', text: 'All specified mutations must be present.', example: 'A AND B' },
  { pill: 'OR', variant: 'or', text: 'At least one specified mutation must be present.', example: 'A OR B' },
  { pill: 'NOT', variant: 'not', text: 'The specified mutation must not be present.', example: 'A AND NOT B' },
  { pill: 'XOR', variant: 'xor', text: 'Exactly one specified mutation must be present.', example: 'A XOR B' },
];

export const ABOUT_SPONSORS = [
  {
    href: ABOUT_FUNDING_URL,
    label: 'Hans A. Krebs Program for Medical Scientists',
    src: 'https://uni-freiburg.de/med/wp-content/uploads/sites/9/fodek-hans-a-krebs-program-for-medical-scientist.png',
    alt: 'Hans A. Krebs Program for Medical Scientists logo',
  },
  {
    href: 'https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html',
    label: 'Institute of Virology, Medical Center – University of Freiburg',
    src: 'https://www.uniklinik-freiburg.de/fileadmin/_processed_/9/1/csm_Virologie_D__2021_1aa65478be.png',
    alt: 'Institute of Virology, Medical Center – University of Freiburg logo',
  },
];
