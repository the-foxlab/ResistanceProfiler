import { Link } from 'react-router';

import { usePageTitle } from '../../hooks/usePageTitle';
import analyzeIconSrc from '../../assets/icon-analyze.svg';
import databaseIconSrc from '../../assets/icon-database.svg';
import shieldIconSrc from '../../assets/shield.svg';
import noSignIconSrc from '../../assets/no_sign.svg';

const CAPABILITY_CARDS = [
  {
    title: 'Analyze',
    text: 'Profile single samples or whole batches from VCF or consensus FASTA input, with optional BAM coverage annotation.',
    iconSrc: analyzeIconSrc,
    links: [
      { to: '/analysis', label: 'Open analysis' },
      { to: '/analysis/reports', label: 'Session reports' },
    ],
  },
  {
    title: 'Explore databases',
    text: 'Browse resistance rules, inspect mutation statistics, and compare rule sets across databases.',
    iconSrc: databaseIconSrc,
    links: [
      { to: '/databases', label: 'Open databases' },
      { to: '/databases/mutations', label: 'Browse mutations' },
      { to: '/databases/compare', label: 'Compare databases' },
    ],
  },
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
  const { databases = [], onStartTour, setSelectedDatabaseId } = logic;

  const totalRules = databases.reduce((sum, db) => sum + (db.mutation_count || 0), 0);
  const totalOrganisms = sumOrganisms(databases);

  return (
    <div className="page-home">
      <section className="home-hero">
        <h1>Welcome to ResistanceProfiler!</h1>
        <p className="home-hero-lead">
          Antiviral resistance, analyzed consistently. Compare viral sequence data with openly available resistance databases.
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
      </section>

      <section className="home-context" aria-labelledby="home-context-title">
        <h2 id="home-context-title">Why ResistanceProfiler?</h2>
        <p>
          Antiviral resistance is a significant health concern: it can reduce the effectiveness of antiviral therapy, cause severe complications, and limit treatment options. Yet there is no generally accepted standard for analyzing viral genomes to identify mutations associated with reduced drug susceptibility. Pathogen-specific tools do exist, such as the{' '}
          <a href="https://hivdb.stanford.edu/" target="_blank" rel="noreferrer">
            Stanford HIV Drug Resistance Database
          </a>, but approaches are not standardized across pathogens. ResistanceProfiler was developed to standardize how resistance databases are organized and how viral sequence data are analyzed, making results easier to interpret and compare across pathogens and input formats.
        </p>
      </section>

      <section className="home-databases" aria-label="Available databases">
        <div className="home-section-head">
          <h2>Available databases</h2>
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
              <Link
                key={db.id}
                to="/databases"
                className="home-db-card"
                aria-label={`Open ${db.display_name || db.id} in Databases`}
                onClick={() => setSelectedDatabaseId?.(db.id)}
              >
                <h3>{db.display_name || db.id}</h3>
                <ul className="home-db-organisms" aria-label={`${db.display_name || db.id} supported species`}>
                  {db.supported_organisms?.length ? (
                    db.supported_organisms.map((organism, index) => (
                      <li key={`${db.id}-${index}`}>{organism}</li>
                    ))
                  ) : (
                    <li>Species not listed</li>
                  )}
                </ul>
                <footer className="home-db-meta">
                  <span><b>{db.mutation_count ?? 0}</b> rules</span>
                  {db.metadata?.maintainer_update && (
                    <span>Updated <time dateTime={db.metadata.maintainer_update}>{db.metadata.maintainer_update}</time></span>
                  )}
                </footer>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="home-cards" aria-label="What you can do">
        {CAPABILITY_CARDS.map((card) => (
          <article key={card.title} className="home-capability-card" tabIndex={0}>
            <span className="about-icon-mask home-card-icon" style={{ '--icon-src': `url(${card.iconSrc})` }} aria-hidden="true" />
            <h3>{card.title}</h3>
            <p>{card.text}</p>
            <div className="home-capability-links">
              {card.links.map((link) => (
                <Link key={link.to} to={link.to}>{link.label}</Link>
              ))}
            </div>
          </article>
        ))}
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
