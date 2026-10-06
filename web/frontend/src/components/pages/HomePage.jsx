import { Link, useNavigate } from 'react-router';

import { usePageTitle } from '../../hooks/usePageTitle';
import analyzeIconSrc from '../../assets/icon-analyze.svg';
import databaseIconSrc from '../../assets/icon-database.svg';
import reportIconSrc from '../../assets/reports.svg';
import uploadIconSrc from '../../assets/upload.svg';
import alignIconSrc from '../../assets/icon-align.svg';
import searchIconSrc from '../../assets/search.svg';
import shieldIconSrc from '../../assets/shield.svg';
import noSignIconSrc from '../../assets/no_sign.svg';

const CAPABILITY_CARDS = [
  {
    title: 'Analyze',
    text: 'Profile single samples or whole batches from VCF or consensus FASTA input, with optional BAM coverage annotation.',
    iconSrc: analyzeIconSrc,
    to: '/analysis',
    linkLabel: 'Open analysis',
  },
  {
    title: 'Explore databases',
    text: 'Browse resistance rules, inspect mutation statistics, and compare rule sets across databases.',
    iconSrc: databaseIconSrc,
    to: '/databases',
    linkLabel: 'Open databases',
  },
  {
    title: 'Report and share',
    text: 'Interactive HTML reports plus PDF, JSON and TSV exports. The CLI offers the same functionality for pipelines.',
    iconSrc: reportIconSrc,
    to: '/about',
    linkLabel: 'Learn more',
  },
];

const WORKFLOW_STEPS = [
  { title: 'Input', text: 'VCF or FASTA with a matching reference.', iconSrc: uploadIconSrc },
  { title: 'Match', text: 'Automatic reference and CDS matching.', iconSrc: alignIconSrc },
  { title: 'Detect', text: 'Nucleotide and amino-acid changes.', iconSrc: searchIconSrc },
  { title: 'Report', text: 'Interactive report with interpretations.', iconSrc: reportIconSrc },
];

function sumOrganisms(databases) {
  const organisms = new Set();
  for (const db of databases) {
    for (const organism of db.supported_organisms || []) {
      organisms.add(organism);
    }
  }
  return organisms.size;
}

export function HomePage({ logic }) {
  usePageTitle('ResPro | Home');
  const navigate = useNavigate();
  const { databases = [], runExampleProfile, onStartTour } = logic;

  const totalRules = databases.reduce((sum, db) => sum + (db.mutation_count || 0), 0);
  const totalOrganisms = sumOrganisms(databases);

  return (
    <div className="page-home">
      <section className="home-hero">
        <p className="home-hero-kicker">Pathogen-agnostic antiviral resistance profiling</p>
        <h1>Antiviral resistance profiling from sequence to report</h1>
        <p className="home-hero-lead">
          ResistanceProfiler matches your sequencing data against curated resistance
          databases and produces an interactive report with per-drug interpretations.
        </p>
        <div className="home-hero-actions">
          <Link to="/analysis" className="analyze-primary home-cta-primary">Launch analysis</Link>
          <Link to="/databases" className="home-cta-secondary">Explore databases</Link>
          {onStartTour && (
            <button type="button" className="home-cta-secondary" onClick={onStartTour}>
              Take a tour
            </button>
          )}
        </div>
        <div className="home-example-strip">
          <span>Just curious?</span>
          <button type="button" className="home-example-btn" onClick={() => runExampleProfile?.()}>
            Run an example sample
          </button>
          <span className="home-example-note">no upload needed — the result opens right away</span>
        </div>
      </section>

      <section className="home-cards" aria-label="What you can do">
        {CAPABILITY_CARDS.map((card) => (
          <article key={card.title} className="home-capability-card" tabIndex={0}>
            <span className="about-icon-mask home-card-icon" style={{ '--icon-src': `url(${card.iconSrc})` }} aria-hidden="true" />
            <h3>{card.title}</h3>
            <p>{card.text}</p>
            <Link to={card.to}>{card.linkLabel}</Link>
          </article>
        ))}
      </section>

      <section className="home-workflow" aria-label="How it works">
        <h2>How it works</h2>
        <ol className="home-workflow-track">
          {WORKFLOW_STEPS.map((step, index) => (
            <li key={step.title} className="home-workflow-step">
              <span className="about-icon-mask home-workflow-icon" style={{ '--icon-src': `url(${step.iconSrc})` }} aria-hidden="true" />
              <span className="home-workflow-number">{index + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="home-databases" aria-label="Available databases">
        <div className="home-section-head">
          <h2>Available databases</h2>
          <Link to="/databases">Open the database dashboard</Link>
        </div>
        <div className="home-stat-strip" aria-label="Database stats">
          <span className="home-stat"><b>{databases.length}</b> databases</span>
          <span className="home-stat"><b>{totalOrganisms}</b> pathogens</span>
          <span className="home-stat"><b>{totalRules}</b> rules</span>
        </div>
        {databases.length === 0 ? (
          <p className="home-db-empty">No databases are currently available.</p>
        ) : (
          <div className="home-db-grid">
            {databases.map((db) => (
              <article key={db.id} className="home-db-card" tabIndex={0}>
                <h3>{db.display_name || db.id}</h3>
                <p className="home-db-organisms">{(db.supported_organisms || []).join(', ') || '—'}</p>
                <p className="home-db-meta">
                  <b>{db.mutation_count ?? 0}</b> rules
                  {db.metadata?.maintainer_update && (
                    <span> · updated {db.metadata.maintainer_update}</span>
                  )}
                </p>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="home-notices" aria-label="Important notices">
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
    </div>
  );
}
