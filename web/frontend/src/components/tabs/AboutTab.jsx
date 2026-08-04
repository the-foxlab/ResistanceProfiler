import { useState } from 'react';

import aboutIconSrc from '../../assets/icon-about.svg';
import analyzeIconSrc from '../../assets/icon-analyze.svg';
import aboutScopeIconSrc from '../../assets/icon-scope.svg';
import aboutAlignIconSrc from '../../assets/icon-align.svg';
import uploadIconSrc from '../../assets/upload.svg';
import okListIconSrc from '../../assets/ok_list.svg';
import shieldIconSrc from '../../assets/shield.svg';
import noSignIconSrc from '../../assets/no_sign.svg';
import networkIconSrc from '../../assets/network.svg';
import contactIconSrc from '../../assets/contact.svg';
import licenseIconSrc from '../../assets/license.svg';
import cliIconSrc from '../../assets/icon-cli.svg';
import aboutIllustrationSrc from '../../assets/about.png';
import databaseIconSrc from '../../assets/icon-database.svg';
import mutationsIconSrc from '../../assets/search.svg';
import reportIconSrc from '../../assets/reports.svg';
import logicIconSrc from '../../assets/logic.svg';
import { FRONTEND_CONFIG } from '../../config';

const ABOUT_CLI_COMMANDS = [
  'respro databases --download db_name --output my_folder/',
  'respro init --name "My Project" --genbank refs.gb --rules rules.tsv --output project.db',
  'respro add --project project.db --rules more_rules.tsv --formula-rules combinatorial_rules.tsv',
  'respro vcf --project project.db --vcf sample.vcf --ref-fasta ref.fasta --output report/ --export json',
  'respro fasta --project project.db --fasta sample.fasta --output report/',
  'respro regenerate --project project.db --json report/sample.results.json --output report/',
];

const ABOUT_DOCKER_COMMAND = 'docker compose -f docker-compose.web.yml up --build';

const ABOUT_WORKFLOW_STEPS = [
  {
    title: 'Input',
    text: 'Consensus FASTA or VCF plus matching reference FASTA; optional BAM for coverage. The VCF may be multi-chrom and the reference FASTA multi-record (one record per CHROM) to profile segmented viruses or multiple targets in one run.',
    iconSrc: uploadIconSrc,
  },
  {
    title: 'Reference matching',
    text: 'Automatic sequence feature matching using minimap.',
    iconSrc: aboutAlignIconSrc,
  },
  {
    title: 'Mutation detection',
    text: 'Nucleotide and amino-acid changes are identified.',
    iconSrc: mutationsIconSrc,
  },
  {
    title: 'Rule evaluation',
    text: 'Single and combination rules are matched.',
    iconSrc: okListIconSrc,
  },
  {
    title: 'Report generation',
    text: 'Structured results, JSON exports, plots, and HTML reports.',
    iconSrc: reportIconSrc,
  },
];

export function AboutTab({ setActiveMode }) {
  const [copiedCommandKey, setCopiedCommandKey] = useState('');

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
    <article className="card about-tile">
      <section className="about-hero" tabIndex={0}>
        <div className="about-hero-content">
          <p className="about-hero-kicker">Pathogen-agnostic antiviral resistance profiling</p>
          <h2>About ResistanceProfiler</h2>
          <p>
            ResistanceProfiler is a pathogen-agnostic antiviral resistance framework with a CLI-first core and a
            web frontend for interactive analysis.
          </p>
          <div className="about-hero-actions">
            <button type="button" onClick={() => setActiveMode('analyze')}>Start analysis</button>
            <a
              className="about-hero-link"
              href="https://the-foxlab.github.io/ResistanceProfiler/"
              target="_blank"
              rel="noreferrer"
            >
              View documentation
            </a>
            <a className="about-hero-link about-hero-link-secondary" href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer">
              GitHub
            </a>
          </div>
        </div>
        <div className="about-hero-visual" aria-hidden="true">
          <img src={aboutIllustrationSrc} alt="" className="about-hero-image" />
        </div>
      </section>

      <section className="about-notice-grid" aria-label="Important notices">
        <article className="about-notice-card about-notice-card-research" tabIndex={0}>
          <span className="about-notice-icon" aria-hidden="true">
            <span className="about-icon-mask" style={{ '--icon-src': `url(${shieldIconSrc})` }} />
          </span>
          <div>
            <h3>Research use only</h3>
            <p>
              This software supports exploratory interpretation and does not replace accredited clinical diagnostics.
            </p>
          </div>
        </article>
        <article className="about-notice-card about-notice-card-database" tabIndex={0}>
          <span className="about-notice-icon" aria-hidden="true">
            <span className="about-icon-mask" style={{ '--icon-src': `url(${noSignIconSrc})` }} />
          </span>
          <div>
            <h3>No database curation</h3>
            <p>
              We do not maintain or curate resistance databases ourselves. We only provide up-to-date converted
              <a href="https://github.com/the-foxlab/respro-databases" target="_blank" rel="noreferrer"> versions</a> of openly available databases and are not responsible for their content or maintenance.
            </p>
          </div>
        </article>
      </section>

      <section className="about-section-card about-section-card-scope" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${aboutScopeIconSrc})` }} aria-hidden="true" />
          <h3>Project Scope and How It Works</h3>
        </div>
        <p className="about-section-lead">
          References and rules are matched during database creation to ensure internal consistency. Mutations are
          stored in a project database, and new sequences are compared against internal references to identify
          resistance patterns. The reference is determined automatically from <a href="https://github.com/lh3/minimap2" target="_blank" rel="noreferrer">mappy-based</a> CDS matching, and the sequence with the highest identity is selected.
        </p>
        <div className="about-workflow" role="list" aria-label="ResistanceProfiler workflow">
          <div className="about-workflow-track" aria-hidden="true">
            {ABOUT_WORKFLOW_STEPS.map((step, index) => (
              <span key={step.title} className="about-workflow-point">
                <span className="about-workflow-number">{index + 1}</span>
              </span>
            ))}
          </div>
          <div className="about-workflow-cards">
            {ABOUT_WORKFLOW_STEPS.map((step) => (
              <article key={step.title} className="about-workflow-step" role="listitem" tabIndex={0}>
                <span className="about-workflow-icon about-icon-mask" style={{ '--icon-src': `url(${step.iconSrc})` }} aria-hidden="true" />
                <div className="about-workflow-copy">
                  <h4>{step.title}</h4>
                  <p>{step.text}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="about-knowledge-grid" aria-label="Resistance interpretation basics">
        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${okListIconSrc})` }} aria-hidden="true" />
            <h3>Rule Nomenclature Basics</h3>
          </div>
          <p className="about-section-lead">
            Rules are amino-acid-centric. A notation such as <span className="about-inline-pill">A123V</span> means
            reference amino acid A at position 123 changes to V.
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
                  <td>The G after position 215 is deleted.</td>
                </tr>
                <tr>
                  <td>Anchored insertion</td>
                  <td><span className="about-inline-pill">V215VG</span></td>
                  <td>Insertion of G after the V at position 215.</td>
                </tr>
                <tr>
                  <td>Frameshift</td>
                  <td><span className="about-inline-pill">L201Lfsx</span></td>
                  <td>Reading-frame shift after the L at position 201.</td>
                </tr>
                <tr>
                  <td>Phenotype</td>
                  <td><span className="about-inline-pill">sensitive / resistant</span></td>
                  <td>Captures in-vitro susceptibility interpretation.</td>
                </tr>
                <tr>
                  <td>Clinical phenotype</td>
                  <td><span className="about-inline-pill">sensitive / resistant</span></td>
                  <td>Captures treatment-oriented interpretation where available.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${networkIconSrc})` }} aria-hidden="true" />
            <h3>Rule Combinations</h3>
          </div>
          <p className="about-section-lead">
            Combination rules allow interpretation based on boolean logic across multiple mutation members. They
            are defined separately from single rules and evaluated with operators such as and, or, not, and xor.
          </p>
          <p className="about-threshold-note">
            Combination members are evaluated based on a fixed member allele-frequency
            (<span className="about-inline-pill">AF &gt; 0.75</span> by default).
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
            Single rules represent one mutation-to-interpretation mapping. Combination rules fire only when
            their formula conditions are satisfied.
          </p>
        </article>

        <article className="about-section-card about-section-card-algorithms" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${logicIconSrc})` }} aria-hidden="true" />
            <h3>Supported Interpretation Algorithms</h3>
          </div>
          <p className="about-section-lead">
            Interpretation algorithms extend rule evaluation with additional logic. They are configured per
            project in the metadata JSON at initialisation time.
          </p>
          <div className="about-operator-list">
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-and">effect_as_resistant</span>
              <p>
                Configured high-impact variant effects (frameshift, stop_gained, stop_lost, start_lost,
                insertion, deletion) observed in a feature/reference pair are interpreted as
                  <span className="about-inline-pill">phenotype='resistant'</span> for the configured drug.
                  This algorithm does not set <span className="about-inline-pill">clinical_phenotype</span>.
              </p>
            </div>
            <div className="about-operator-row">
              <span className="about-operator-pill about-operator-pill-or">drug_interpretation</span>
              <p>
                Combines matched rules into one overall drug result. Depending on the database, this can be
                based on phenotype labels, scores, IC50 values, or fold-change cutoffs.
              </p>
            </div>
          </div>
        </article>
      </section>

      <section className="about-section-card about-cli-card" tabIndex={0}>
        <div className="about-section-title">
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${cliIconSrc})` }} aria-hidden="true" />
          <h3>CLI and Extended Functionality</h3>
        </div>
        <p className="about-section-lead">
          The CLI is the primary interface and includes project creation, rule curation, profiling, and export.
          The same functionality as the web app can be achieved through the CLI, enabling direct integration into
          existing workflows and pipelines.
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
                Profiling runs can emit a JSON dump of the result payload, which can later be used to regenerate
                report artifacts.
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
      </section>

      <section className="about-bottom-grid" aria-label="Project information and governance">
        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${contactIconSrc})` }} aria-hidden="true" />
            <h3>Contributing and Contact</h3>
          </div>
          <p>
            Contributions are very welcome, especially curated rule datasets, bug reports, reproducible test
            cases, and code improvements. Open an issue or submit a pull request on{' '}
            <a href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer">GitHub</a>{' '}
            to get in touch. For direct contact, please{' '}
            <a href="mailto:jonas.fuchs@uniklinik-freiburg.de">email Jonas Fuchs</a>.
          </p>
        </article>
        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${databaseIconSrc})` }} aria-hidden="true" />
            <h3>Data usage</h3>
          </div>
          <ul>
            <li>Session-scoped uploads and reports are cleaned up automatically when a browser tab closes.</li>
            <li>No data is stored on remote servers.</li>
            <li>Avoid naming results with sensitive information such as patient identifiers or names.</li>
          </ul>
        </article>
        <article className="about-section-card" tabIndex={0}>
          <div className="about-section-title">
            <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${licenseIconSrc})` }} aria-hidden="true" />
            <h3>Licensing</h3>
          </div>
          <ul>
            <li>ResistanceProfiler source code is released under the GNU Affero General Public License v3.0.</li>
            <li>External references, rules, and publication-linked datasets may have separate licenses or citation requirements.</li>
            <li>Users are responsible for compliant use of third-party data in their own environments.</li>
          </ul>
        </article>
      </section>

      <section className="about-supported-strip" tabIndex={0}>
        <p>Supported by</p>
        <div className="about-supported-logos">
          <a
            href="https://uni-freiburg.de/med/forschung/qualifizierung-nach-der-promotion/medical-scientist/"
            target="_blank"
            rel="noreferrer"
            aria-label="Sponsor page"
            className="about-supported-logo"
          >
            <img
              src="https://uni-freiburg.de/med/wp-content/uploads/sites/9/fodek-hans-a-krebs-program-for-medical-scientist.png"
              alt="Sponsor logo"
              className="about-sponsor-logo"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
          </a>
        </div>
      </section>
    </article>
  );
}
