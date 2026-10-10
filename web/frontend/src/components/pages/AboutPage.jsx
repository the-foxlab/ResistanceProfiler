import { useState } from 'react';

import aboutScopeIconSrc from '../../assets/icon-scope.svg';
import okListIconSrc from '../../assets/ok_list.svg';
import networkIconSrc from '../../assets/network.svg';
import contactIconSrc from '../../assets/contact.svg';
import cliIconSrc from '../../assets/icon-cli.svg';
import databaseIconSrc from '../../assets/icon-database.svg';
import logicIconSrc from '../../assets/logic.svg';
import { FRONTEND_CONFIG, MAINTAINER_EMAIL } from '../../config';

const ABOUT_TOC = [
  { id: 'about-scope', label: 'Project scope and how it works' },
  { id: 'about-nomenclature', label: 'Rule nomenclature' },
  { id: 'about-combinations', label: 'Rule combinations' },
  { id: 'about-algorithms', label: 'Interpretation algorithms' },
  { id: 'about-databases', label: 'Databases' },
  { id: 'about-cli', label: 'CLI and extended functionality' },
  { id: 'about-governance', label: 'Contributing, data usage, licensing' },
  { id: 'about-funding', label: 'Funding' },
];

const ABOUT_KNOWLEDGE_IDS = ['about-nomenclature', 'about-combinations', 'about-algorithms'];

const ABOUT_CLI_COMMANDS = [
  'respro databases --download db_name --output my_folder/',
  'respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db',
  'respro add --project project.db --rules more_rules.tsv --formula-rules combinatorial_rules.tsv',
  'respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/ --export json',
  'respro fasta --project project.db --fasta sample.fasta --output report/',
  'respro regenerate --project project.db --json report/sample.results.json --output report/',
];

const ABOUT_DOCKER_COMMAND = 'docker compose -f docker-compose.web.yml up --build';

const ABOUT_DOCS_ROOT = 'https://the-foxlab.github.io/ResistanceProfiler/';
const ABOUT_DOCS = {
  workflow: `${ABOUT_DOCS_ROOT}how-it-works/#pipeline-overview`,
  nomenclature: `${ABOUT_DOCS_ROOT}rules-format/#normalization-examples-input-canonical-interpretation`,
  combinations: `${ABOUT_DOCS_ROOT}how-it-works/#rule-matching`,
  algorithms: `${ABOUT_DOCS_ROOT}algorithms/#how-respro-evaluates-resistance`,
  databaseSetup: `${ABOUT_DOCS_ROOT}database-preparation/#create-a-new-project-database`,
  databaseDownload: `${ABOUT_DOCS_ROOT}cli-reference/#download-a-maintained-database`,
  cli: `${ABOUT_DOCS_ROOT}cli-reference/#profile-fasta-input`,
  webData: `${ABOUT_DOCS_ROOT}webapp/#data-and-filesystem`,
  databaseMetadata: `${ABOUT_DOCS_ROOT}database-preparation/#optional-metadata-json`,
};
const ABOUT_DATABASES_URL = 'https://github.com/the-foxlab/respro-databases';
const ABOUT_FUNDING_URL = 'https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/';

function AboutDocsLink({ links }) {
  return (
    <p className="about-docs-link">
      Related documentation:{' '}
      {links.map((link, index) => (
        <span key={link.href}>
          {index > 0 && ' · '}
          <a href={link.href} target="_blank" rel="noreferrer">{link.label}</a>
        </span>
      ))}
    </p>
  );
}

const ABOUT_WORKFLOW_STEPS = [
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

export function AboutPage({ logic }) {
  const { contactEmail } = logic;
  // Deployment-configurable contact address (RESPRO_WEB_CONTACT_EMAIL); falls back
  // to the maintainer address so the About tab always shows a contact.
  const contactAddress = contactEmail || MAINTAINER_EMAIL;
  const [copiedCommandKey, setCopiedCommandKey] = useState('');
  const [activeSectionId, setActiveSectionId] = useState('about-scope');

  const copyAboutCommand = async (content, key) => {
    if (!navigator?.clipboard?.writeText) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setCopiedCommandKey(key);
      setTimeout(() => {
        setCopiedCommandKey((current) => (current === key ? '' : current));
      }, 1600);
    } catch {
      setCopiedCommandKey('');
    }
  };

  return (
    <article className="about-tile">
      <header className="about-intro">
        <h1>About ResistanceProfiler</h1>
        <p>
          ResistanceProfiler compares pathogen samples with curated reference databases to help interpret known
          antiviral resistance markers. It supports research and does not replace accredited clinical diagnostics.
        </p>
      </header>

      <nav className="about-toc" aria-label="On this page">
        <ul>
          {ABOUT_TOC.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                aria-pressed={activeSectionId === entry.id}
                onClick={() => setActiveSectionId(entry.id)}
              >
                {entry.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {activeSectionId === 'about-scope' && (
      <section className="about-section-card about-section-card-scope" id="about-scope" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${aboutScopeIconSrc})` }} aria-hidden="true" />
          <h3>Project Scope and How It Works</h3>
        </div>
        <p className="about-section-lead">
          ResPro maps a sample to the reference sequences and annotated coding features in a project database, then
          compares the resulting amino-acid changes with that database's resistance rules. The database therefore
          determines both the coordinate context and the evidence available for interpretation. A rule match is an
          interpretation of curated evidence.
        </p>
        <ol className="about-workflow" aria-label="ResistanceProfiler workflow">
          {ABOUT_WORKFLOW_STEPS.map((step, index) => (
            <li key={step.title} className="about-workflow-step">
              <span className="about-workflow-number" aria-hidden="true">{index + 1}</span>
              <div className="about-workflow-copy">
                <h4>{step.title}</h4>
                <p>{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
        <div className="about-scope-notes">
          <div>
            <h4>What you provide</h4>
            <p>A consensus sequence (FASTA), or a VCF plus the exact reference used to call its variants. An optional BAM provides aligned reads for coverage assessment and direct evidence about which variants occur in the same codon.</p>
          </div>
          <div>
            <h4>What you get</h4>
            <p>A per-drug interpretation summary, matched changes and their reported frequencies, and coverage notes identifying regions with insufficient or unavailable evidence. Results can be exported for review.</p>
          </div>
          <div>
            <h4>What it does not do</h4>
            <p>ResPro does not validate the sequencing assay or replace an accredited diagnostic. Interpretation is limited by reference matching, sequence quality, coverage, and the scope and currency of the selected rules; review these factors before drawing clinical conclusions.</p>
          </div>
        </div>
        <AboutDocsLink links={[{ href: ABOUT_DOCS.workflow, label: 'Pipeline overview and reference matching' }]} />
      </section>
      )}

      {ABOUT_KNOWLEDGE_IDS.includes(activeSectionId) && (
      <section className="about-knowledge-grid about-knowledge-grid-single" aria-label="Resistance interpretation basics">
        {activeSectionId === 'about-nomenclature' && (
        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${okListIconSrc})` }} aria-hidden="true" />
            <h3>Rule Nomenclature Basics</h3>
          </div>
          <p className="about-section-lead">
            ResPro rule labels describe amino-acid-level events. In <span className="about-inline-pill">A123V</span>,
            A is the reference residue, 123 is its position, and V is the alternate residue. Positions are relative
            to the annotated protein sequence for the reference used by the project database.
          </p>
          <p className="about-section-lead">
            Each rule is associated with an annotated feature (usually a protein-coding gene), a reference, and an
            amino-acid event. A match contributes the rule's curated phenotype, score, or other interpretation to
            the report.
          </p>
          <div className="about-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Example</th>
                  <th>Meaning</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Substitution</td>
                  <td><span className="about-inline-pill">A123V</span></td>
                  <td>Position 123 changed from A to V.</td>
                </tr>
                <tr>
                  <td>Anchored deletion</td>
                  <td><span className="about-inline-pill">VG215V</span></td>
                  <td>G is deleted after the reference V at position 215.</td>
                </tr>
                <tr>
                  <td>Anchored insertion</td>
                  <td><span className="about-inline-pill">V215VG</span></td>
                  <td>G is inserted after the reference V at position 215.</td>
                </tr>
                <tr>
                  <td>Frameshift</td>
                  <td><span className="about-inline-pill">L201LfsX</span></td>
                  <td>Reading-frame shift after the reference L at position 201.</td>
                </tr>
              </tbody>
            </table>
          </div>
          <h4>Phenotype ranks and accepted labels</h4>
          <p>
            Rules can include an in-vitro <code>phenotype</code>, a clinically oriented <code>clinical_phenotype</code>,
            or both. ResPro maps either field to the same severity rank  and report
            colours.
          </p>
          <div className="about-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Interpretation</th>
                  <th>Accepted labels</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>1</td>
                  <td>Susceptible</td>
                  <td><code>susceptible</code>, <code>sensitive</code>, <code>normal inhibition</code>, <code>ni</code>, <code>normal</code></td>
                </tr>
                <tr>
                  <td>2</td>
                  <td>Potential low-level resistance</td>
                  <td><code>potential low-level resistance</code>, <code>possibly resistant</code>, <code>suspected reduced</code></td>
                </tr>
                <tr>
                  <td>3</td>
                  <td>Low-level resistance</td>
                  <td><code>low-level resistance</code>, <code>reduced susceptibility</code>, <code>limited susceptibility</code></td>
                </tr>
                <tr>
                  <td>4</td>
                  <td>Intermediate</td>
                  <td><code>intermediate</code>, <code>intermediate resistance</code>, <code>reduced inhibition</code>, <code>ri</code></td>
                </tr>
                <tr>
                  <td>5</td>
                  <td>Resistant</td>
                  <td><code>resistant</code>, <code>high-level resistance</code>, <code>highly reduced inhibition</code>, <code>hri</code></td>
                </tr>
                <tr>
                  <td>0</td>
                  <td>Unknown</td>
                  <td><code>unknown</code>, <code>not analysed</code>, <code>none</code>, or a blank field</td>
                </tr>
                <tr>
                  <td>-1</td>
                  <td>Contradictory</td>
                  <td><code>contradictory</code>, <code>conflicting</code></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>
            Labels are case-insensitive and whitespace is trimmed. Bare values <code>1</code>–<code>5</code> are also
            accepted and resolve to that rank's standard label. Ranks 1–5 run from least to most severe; unknown and
            contradictory are special categories.
          </p>
          <AboutDocsLink links={[
            { href: ABOUT_DOCS.nomenclature, label: 'Mutation notation and normalization' },
            { href: `${ABOUT_DOCS_ROOT}rules-format/#phenotype-normalization`, label: 'Phenotype rank vocabulary' },
          ]} />
        </article>
        )}

        {activeSectionId === 'about-combinations' && (
        <article className="about-section-card" id="about-combinations" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${networkIconSrc})` }} aria-hidden="true" />
            <h3>Rule Combinations</h3>
          </div>
          <p className="about-section-lead">
            Sometimes one change alone is not enough to interpret a result. A combination rule checks a small set
            of changes together, using familiar logic such as AND (both) or OR (either).
          </p>
          <p className="about-threshold-note">
            <strong>How is co-occurrence assessed?</strong> For an AND rule, ResPro estimates a conservative lower bound on the fraction carrying
            all required changes, then compares it with the maximum possible overlap given the member frequencies.
            By default, this guaranteed-to-maximum overlap ratio must reach <span className="about-inline-pill">2/3</span>.
            Here ResPro tests whether the evidence supports that the changes cooccur together solely based on their observed frequencies.
          </p>
          <div className="about-operator-list">
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-and">AND</span>
              <p>All specified mutations must be present.</p>
              <code>A AND B</code>
            </div>
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-or">OR</span>
              <p>At least one specified mutation must be present.</p>
              <code>A OR B</code>
            </div>
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-not">NOT</span>
              <p>The specified mutation must not be present.</p>
              <code>A AND NOT B</code>
            </div>
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-xor">XOR</span>
              <p>Exactly one specified mutation must be present.</p>
              <code>A XOR B</code>
            </div>
          </div>
          <p className="about-note-inline">
            A single rule maps one amino-acid event to its curated interpretation. A formula rule evaluates its
            expression across member rules; for example, <span className="about-inline-pill">A AND B</span> requires
            both members and sufficient co-occurrence evidence. A sample can still match and report A's single rule
            even when the formula rule does not fire. Parentheses can group conditions, and the report identifies
            the rules that contributed to an interpretation.
          </p>
          <AboutDocsLink links={[{ href: ABOUT_DOCS.combinations, label: 'Combination rule matching' }]} />
        </article>
        )}

        {activeSectionId === 'about-algorithms' && (
        <article className="about-section-card about-section-card-algorithms" id="about-algorithms" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${logicIconSrc})` }} aria-hidden="true" />
            <h3>Supported Interpretation Algorithms</h3>
          </div>
          <p className="about-section-lead">
            A rule describes a known change or combination. An interpretation algorithm brings matching rules
            together to produce an overall result for each drug. The project database determines which approach to use.
          </p>
          <div className="about-operator-list">
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-and">effect_as_resistant</span>
              <p>
                Adds a <span className="about-inline-pill">resistant</span> phenotype hit when a configured consequence
                (frameshift, stop gained or lost, start lost, insertion, or deletion) is observed in the specified
                feature and reference. This is an explicit database-level interpretation rule.
              </p>
            </div>
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-or">drug_interpretation</span>
              <p>
                Aggregates matched evidence into a drug-level assessment using the method configured for that
                database: the most severe phenotype label, a summed score, an IC50 value, or a fold-change value
                compared with configured thresholds.
              </p>
            </div>
          </div>
          <p className="about-note-inline">
            These methods are configured with the project database. If
            multiple methods are configured, the report shows their assessments and combines them using the most
            severe inferred rank for the final call.
          </p>
          <AboutDocsLink links={[{ href: ABOUT_DOCS.algorithms, label: 'Interpretation algorithms' }]} />
        </article>
        )}
      </section>
      )}

      {activeSectionId === 'about-databases' && (
      <section className="about-section-card about-section-card-databases" id="about-databases" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${databaseIconSrc})` }} aria-hidden="true" />
          <h3>Databases</h3>
        </div>
        <p className="about-section-lead">
          Every analysis compares your sample against a project database. You can download ready-made databases that
          are converted from well-known, publicly maintained antiviral resistance resources, or build your own from
          your own reference sequences and rules.
        </p>
        <ul>
          <li><strong>Converted, not re-curated.</strong> Each maintained database is an automated conversion of an upstream source (for example Stanford HIVdb or HerpesDRG) into ResPro's rule format. The scientific interpretations stay with the upstream curators.</li>
          <li><strong>Updates are traceable.</strong> Source-specific automation checks upstream data and can open a pull request with regenerated files when it changes. Check the repository history and database metadata for the source and update dates.</li>
          <li><strong>Transparent provenance.</strong> Every database ships metadata — source, version and date, license, and publication reference — so you always know which evidence an interpretation rests on.</li>
          <li><strong>Conversion limits are documented.</strong> Entries that cannot be converted are recorded with reasons in the database repository, rather than silently treated as supported rules.</li>
          <li><strong>Your own rules are welcome.</strong> For local guidelines or in-house evidence, build a project database from GenBank references and a rules table with <code>respro init</code>. In the web app, maintained databases can also be downloaded and updated automatically at startup (server configuration).</li>
        </ul>
        <p className="about-threshold-note">
          ResistanceProfiler does not independently curate or validate the source interpretations. Confirm the
          database, source version, update date, and applicable license before research or clinical use; the software
          is not a substitute for local validation or clinical review.
        </p>
        <AboutDocsLink links={[
          { href: ABOUT_DATABASES_URL, label: 'Maintained database repository' },
          { href: ABOUT_DOCS.databaseDownload, label: 'Download a maintained database' },
          { href: ABOUT_DOCS.databaseSetup, label: 'Create a project database' },
        ]} />
      </section>
      )}

      {activeSectionId === 'about-cli' && (
      <section className="about-section-card about-cli-card" id="about-cli" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${cliIconSrc})` }} aria-hidden="true" />
          <h3>CLI and Extended Functionality</h3>
        </div>
        <p className="about-section-lead">
          The command-line interface (CLI) runs the same profiling workflows from a terminal. It can download or
          build a project database, analyze FASTA or VCF inputs, and export results. It is particularly useful for
          repeatable analyses, batch processing, and integration with existing bioinformatics pipelines; the web
          app is available for interactive use.
        </p>
        <div className="about-cli-grid">
          <article className="about-terminal" tabIndex={0}>
            <div className="about-terminal-header">
              <p>respro-cli</p>
              <button
                type="button"
                className="about-copy-btn"
                onClick={() => copyAboutCommand(ABOUT_CLI_COMMANDS.join('\n'), 'cli')}
                aria-label="Copy CLI commands"
              >
                {copiedCommandKey === 'cli' ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre>
              {ABOUT_CLI_COMMANDS.map((line) => (
                <code key={line}><span className="about-terminal-prompt">$</span> {line}</code>
              ))}
            </pre>
          </article>
          <article className="about-cli-side" tabIndex={0}>
            <div className="about-cli-side-card">
              <h4>Regenerate reports from JSON</h4>
              <p>
                When a run is saved as JSON, the report files can be regenerated later without
                repeating the profiling step. This is useful when changing report outputs or recovering a report.
              </p>
            </div>
            <div className="about-cli-side-card">
              <h4>Run ResPro WebApp locally</h4>
              <div className="about-mini-command">
                <code>{ABOUT_DOCKER_COMMAND}</code>
                <button
                  type="button"
                  className="about-copy-btn"
                  onClick={() => copyAboutCommand(ABOUT_DOCKER_COMMAND, 'docker')}
                  aria-label="Copy Docker startup command"
                >
                  {copiedCommandKey === 'docker' ? 'Copied' : 'Copy'}
                </button>
              </div>
              <p>Open <strong>{FRONTEND_CONFIG.ui.explorerUrl}</strong> after startup.</p>
            </div>
          </article>
        </div>
        <p className="about-note-inline">
          The web app provides an interactive workflow; the CLI makes inputs, database selection, and exports
          explicit in commands that can be recorded and repeated. Both use the same project database and rule logic.
        </p>
        <AboutDocsLink links={[
          { href: ABOUT_DOCS.cli, label: 'FASTA profiling command' },
          { href: `${ABOUT_DOCS_ROOT}cli-reference/#profile-vcf-input`, label: 'VCF profiling command' },
        ]} />
      </section>
      )}

      {activeSectionId === 'about-governance' && (
      <section className="about-section-card about-governance-section" id="about-governance" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${contactIconSrc})` }} aria-hidden="true" />
          <h3>Contributing, Data Use and Licensing</h3>
        </div>
        <div className="about-governance-grid">
          <div>
            <h4>Contributing and contact</h4>
            <p>
              Contributions include rule datasets, bug reports, reproducible examples, and code improvements. Open an
              issue or pull request on{' '}
              <a href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer">GitHub</a>{' '}
              with the database and software versions and a minimal example where possible. For direct contact, email{' '}
              <a href={`mailto:${contactAddress}`}>
                {contactEmail ? contactAddress : 'Jonas Fuchs'}
              </a>.
            </p>
          </div>
          <div>
            <h4>Data use</h4>
            <p>
              In the web app, uploaded files and generated results are stored temporarily and are removed as soon as you close the session. 
              CIGAR strings from mapped fasta sequences are stored to ensure rapid reference matching if the exact same sequence is uploaded again. This is for the sole purpose of avoiding redundant processing. Importantly, no
              other metadata or identifiers such as sample IDs are stored with these cigar strings. 
              Nevertheless, avoid patient names or other direct identifiers.
            </p>
          </div>
          <div>
            <h4>Licensing and attribution</h4>
            <p>
              ResistanceProfiler source code is released under the GNU Affero General Public License v3.0. Reference
              sequences, resistance rules, and publication-linked datasets may have separate licenses and citation
              requirements. Check the selected database's metadata and upstream terms before redistribution,
              publication, or clinical use; users are responsible for complying with those terms.
            </p>
          </div>
        </div>
        <AboutDocsLink links={[
          { href: ABOUT_DOCS.webData, label: 'Web-app data storage and retention' },
          { href: ABOUT_DOCS.databaseMetadata, label: 'Database metadata and licensing' },
        ]} />
      </section>
      )}

      {activeSectionId === 'about-funding' && (
      <section className="about-section-card about-funding-card" id="about-funding" tabIndex={0}>
        <div className="about-section-title">
          <h3>Supported by</h3>
        </div>
        <p className="about-section-lead">
          This work is supported by the Hans A. Krebs Program for Medical Scientists at the University of Freiburg and
          the Institute of Virology Freiburg.
        </p>
        <div className="about-supported-logos">
          <a
            href="https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/"
            target="_blank"
            rel="noreferrer"
            aria-label="Hans A. Krebs Program for Medical Scientists"
            className="about-supported-logo"
          >
            <img
              src="https://uni-freiburg.de/med/wp-content/uploads/sites/9/fodek-hans-a-krebs-program-for-medical-scientist.png"
              alt="Hans A. Krebs Program for Medical Scientists logo"
              className="about-sponsor-logo"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
          </a>
          <a
            href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html"
            target="_blank"
            rel="noreferrer"
            aria-label="Institute of Virology, Medical Center – University of Freiburg"
            className="about-supported-logo"
          >
            <img
              src="https://www.uniklinik-freiburg.de/fileadmin/_processed_/9/1/csm_Virologie_D__2021_1aa65478be.png"
              alt="Institute of Virology, Medical Center – University of Freiburg logo"
              className="about-sponsor-logo"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
          </a>
        </div>
        <p className="about-docs-link">
          <a href={ABOUT_FUNDING_URL} target="_blank" rel="noreferrer">Hans A. Krebs Program information</a>
        </p>
      </section>
      )}
    </article>
  );
}
