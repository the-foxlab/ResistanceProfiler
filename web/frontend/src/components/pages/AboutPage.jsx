import { useEffect, useRef, useState } from 'react';

import { useOptionalAboutLogic } from '../../DashboardContext';
import aboutScopeIconSrc from '../../assets/icon-scope.svg';
import okListIconSrc from '../../assets/ok_list.svg';
import networkIconSrc from '../../assets/network.svg';
import contactIconSrc from '../../assets/contact.svg';
import cliIconSrc from '../../assets/icon-cli.svg';
import databaseIconSrc from '../../assets/icon-database.svg';
import logicIconSrc from '../../assets/logic.svg';
import { FRONTEND_CONFIG, MAINTAINER_EMAIL } from '../../config';
import { PROJECT_LICENSE_URL } from '../../constants';
import { sanitizeEmailAddress } from '../../utils';
import {
  ABOUT_CLI_COMMANDS,
  ABOUT_DATABASES_URL,
  ABOUT_DOCKER_COMMAND,
  ABOUT_DOCS,
  ABOUT_FUNDING_URL,
  ABOUT_KNOWLEDGE_IDS,
  ABOUT_NOMENCLATURE_ROWS,
  ABOUT_OPERATORS,
  ABOUT_PHENOTYPE_RANKS,
  ABOUT_SCOPE_NOTES,
  ABOUT_SPONSORS,
  ABOUT_TOC,
  ABOUT_WORKFLOW_STEPS,
} from './about/aboutContent';

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

function AboutSectionCard({ as: Tag = 'section', id, className = '', iconSrc, title, docsLinks, children }) {
  return (
    <Tag className={`about-section-card${className ? ` ${className}` : ''}`} id={id} tabIndex={0}>
      <div className="about-section-title">
        {iconSrc ? (
          <span className="about-section-icon about-icon-mask" style={{ '--icon-src': `url(${iconSrc})` }} aria-hidden="true" />
        ) : null}
        <h3>{title}</h3>
      </div>
      {children}
      {docsLinks ? <AboutDocsLink links={docsLinks} /> : null}
    </Tag>
  );
}

export function AboutPage({ logic: logicProp }) {
  const contextLogic = useOptionalAboutLogic();
  const logic = logicProp ?? contextLogic;
  const { contactEmail } = logic;
  const safeContactEmail = sanitizeEmailAddress(contactEmail);
  const contactAddress = safeContactEmail || MAINTAINER_EMAIL;
  const [copiedCommandKey, setCopiedCommandKey] = useState('');
  const [activeSectionId, setActiveSectionId] = useState('about-scope');
  const copyTimeoutRef = useRef(null);
  const copyTokenRef = useRef(0);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      copyTokenRef.current += 1;
      if (copyTimeoutRef.current !== null) {
        clearTimeout(copyTimeoutRef.current);
        copyTimeoutRef.current = null;
      }
    };
  }, []);

  const copyAboutCommand = async (content, key) => {
    if (!navigator?.clipboard?.writeText) return;
    const token = ++copyTokenRef.current;
    if (copyTimeoutRef.current !== null) {
      clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = null;
    }
    try {
      await navigator.clipboard.writeText(content);
      if (!isMountedRef.current || token !== copyTokenRef.current) return;
      setCopiedCommandKey(key);
      copyTimeoutRef.current = setTimeout(() => {
        copyTimeoutRef.current = null;
        if (isMountedRef.current) {
          setCopiedCommandKey((current) => (current === key ? '' : current));
        }
      }, 1600);
    } catch {
      if (!isMountedRef.current || token !== copyTokenRef.current) return;
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
        <AboutSectionCard
          id="about-scope"
          className="about-section-card-scope"
          iconSrc={aboutScopeIconSrc}
          title="Project Scope and How It Works"
          docsLinks={[{ href: ABOUT_DOCS.workflow, label: 'Pipeline overview and reference matching' }]}
        >
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
            {ABOUT_SCOPE_NOTES.map((note) => (
              <div key={note.title}>
                <h4>{note.title}</h4>
                <p>{note.text}</p>
              </div>
            ))}
          </div>
        </AboutSectionCard>
      )}

      {ABOUT_KNOWLEDGE_IDS.includes(activeSectionId) && (
        <section className="about-knowledge-grid about-knowledge-grid-single" aria-label="Resistance interpretation basics">
          {activeSectionId === 'about-nomenclature' && (
            <AboutSectionCard
              as="article"
              iconSrc={okListIconSrc}
              title="Rule Nomenclature Basics"
              docsLinks={[
                { href: ABOUT_DOCS.nomenclature, label: 'Mutation notation and normalization' },
                { href: ABOUT_DOCS.phenotypeNormalization, label: 'Phenotype rank vocabulary' },
              ]}
            >
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
                    {ABOUT_NOMENCLATURE_ROWS.map((row) => (
                      <tr key={row.type}>
                        <td>{row.type}</td>
                        <td><span className="about-inline-pill">{row.example}</span></td>
                        <td>{row.meaning}</td>
                      </tr>
                    ))}
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
                    {ABOUT_PHENOTYPE_RANKS.map((row) => (
                      <tr key={row.rank}>
                        <td>{row.rank}</td>
                        <td>{row.interpretation}</td>
                        <td>
                          {row.labels.map((label, idx) => (
                            <span key={label}>
                              {idx > 0 ? ', ' : ''}
                              <code>{label}</code>
                            </span>
                          ))}
                          {row.trailingText || ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                Labels are case-insensitive and whitespace is trimmed. Bare values <code>1</code>–<code>5</code> are also
                accepted and resolve to that rank's standard label. Ranks 1–5 run from least to most severe; unknown and
                contradictory are special categories.
              </p>
            </AboutSectionCard>
          )}

          {activeSectionId === 'about-combinations' && (
            <AboutSectionCard
              as="article"
              id="about-combinations"
              iconSrc={networkIconSrc}
              title="Rule Combinations"
              docsLinks={[{ href: ABOUT_DOCS.combinations, label: 'Combination rule matching' }]}
            >
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
                {ABOUT_OPERATORS.map((op) => (
                  <div key={op.pill} className="about-operator-row">
                    <span className={`about-operator-pill about-operator-pill-${op.variant}`}>{op.pill}</span>
                    <p>{op.text}</p>
                    <code>{op.example}</code>
                  </div>
                ))}
              </div>
              <p className="about-note-inline">
                A single rule maps one amino-acid event to its curated interpretation. A formula rule evaluates its
                expression across member rules; for example, <span className="about-inline-pill">A AND B</span> requires
                both members and sufficient co-occurrence evidence. A sample can still match and report A's single rule
                even when the formula rule does not fire. Parentheses can group conditions, and the report identifies
                the rules that contributed to an interpretation.
              </p>
            </AboutSectionCard>
          )}

          {activeSectionId === 'about-algorithms' && (
            <AboutSectionCard
              as="article"
              id="about-algorithms"
              className="about-section-card-algorithms"
              iconSrc={logicIconSrc}
              title="Supported Interpretation Algorithms"
              docsLinks={[{ href: ABOUT_DOCS.algorithms, label: 'Interpretation algorithms' }]}
            >
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
            </AboutSectionCard>
          )}
        </section>
      )}

      {activeSectionId === 'about-databases' && (
        <AboutSectionCard
          id="about-databases"
          className="about-section-card-databases"
          iconSrc={databaseIconSrc}
          title="Databases"
          docsLinks={[
            { href: ABOUT_DATABASES_URL, label: 'Maintained database repository' },
            { href: ABOUT_DOCS.databaseDownload, label: 'Download a maintained database' },
            { href: ABOUT_DOCS.databaseSetup, label: 'Create a project database' },
          ]}
        >
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
        </AboutSectionCard>
      )}

      {activeSectionId === 'about-cli' && (
        <AboutSectionCard
          id="about-cli"
          iconSrc={cliIconSrc}
          title="CLI and Extended Functionality"
          docsLinks={[
            { href: ABOUT_DOCS.cli, label: 'FASTA profiling command' },
            { href: ABOUT_DOCS.cliVcf, label: 'VCF profiling command' },
          ]}
        >
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
        </AboutSectionCard>
      )}

      {activeSectionId === 'about-governance' && (
        <AboutSectionCard
          id="about-governance"
          className="about-governance-section"
          iconSrc={contactIconSrc}
          title="Contributing, Data Use and Licensing"
          docsLinks={[
            { href: ABOUT_DOCS.webData, label: 'Web-app data storage and retention' },
            { href: ABOUT_DOCS.databaseMetadata, label: 'Database metadata and licensing' },
          ]}
        >
          <div className="about-governance-grid">
            <div>
              <h4>Contributing and contact</h4>
              <p>
                Contributions include rule datasets, bug reports, reproducible examples, and code improvements. Open an
                issue or pull request on{' '}
                <a href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer">GitHub</a>{' '}
                with the database and software versions and a minimal example where possible. For direct contact, email{' '}
                <a href={`mailto:${contactAddress}`}>
                  {safeContactEmail ? contactAddress : 'Jonas Fuchs'}
                </a>.
              </p>
            </div>
            <div>
              <h4>Data use</h4>
              <p>
                In the web app, uploaded files and generated results are stored temporarily and are removed as soon as you close the session (browser tab). 
                CIGAR strings and sequence checksums are stored to ensure rapid reference matching if the exact same sequence is uploaded again. This is for the sole purpose of avoiding redundant processing. Importantly, no
                other metadata or identifiers such as sample IDs are stored with these cigar strings. 
                Nevertheless, avoid patient names or other direct identifiers.
              </p>
            </div>
            <div>
              <h4>Licensing and attribution</h4>
              <p>
                © 2026 Jonas Fuchs, University Medical Center Freiburg. ResistanceProfiler source code, including the
                web app, is licensed under the GNU Affero General Public License version 3.0 only (AGPL-3.0-only);
                see the{' '}
                <a href={PROJECT_LICENSE_URL} target="_blank" rel="noreferrer">full license</a>. Reference sequences,
                resistance rules, and publication-linked datasets may have separate licenses and citation requirements.
                Check the selected database's metadata and upstream terms before redistribution, publication, or clinical
                use; users are responsible for complying with those terms.
              </p>
            </div>
          </div>
        </AboutSectionCard>
      )}

      {activeSectionId === 'about-funding' && (
        <AboutSectionCard id="about-funding" className="about-funding-card" title="Supported by">
          <p className="about-section-lead">
            This work is supported by the Hans A. Krebs Program for Medical Scientists at the University of Freiburg and
            the Institute of Virology Freiburg.
          </p>
          <div className="about-supported-logos">
            {ABOUT_SPONSORS.map((sponsor) => (
              <a
                key={sponsor.label}
                href={sponsor.href}
                target="_blank"
                rel="noreferrer"
                aria-label={sponsor.label}
                className="about-supported-logo"
              >
                <img
                  src={sponsor.src}
                  alt={sponsor.alt}
                  className="about-sponsor-logo"
                  referrerPolicy="no-referrer"
                  onError={(e) => { e.currentTarget.style.display = 'none'; }}
                />
              </a>
            ))}
          </div>
          <p className="about-docs-link">
            <a href={ABOUT_FUNDING_URL} target="_blank" rel="noreferrer">Hans A. Krebs Program information</a>
          </p>
        </AboutSectionCard>
      )}
    </article>
  );
}
