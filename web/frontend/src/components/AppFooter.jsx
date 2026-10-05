/**
 * App footer: optional project links, Legal notice and Contact links plus
 * version spans.
 *
 * All links share one row and each neighbour pair is separated by a ``·`` only
 * when both are present, so the separator count equals the number of adjacent
 * present items minus one. The order is:
 * GitHub · Fuchs Lab · Legal notice · Contact, then Core version · WebApp
 * version on the second row.
 */
import githubIconSrc from '../assets/icon-github.svg';
import websiteIconSrc from '../assets/website.svg';

export function AppFooter({ legalLink, contactEmail, cliVersion, webVersion }) {
  const projectLinks = (
    <>
      <a className="app-footer-icon-link" href="https://github.com/the-foxlab/ResistanceProfiler" target="_blank" rel="noreferrer" title="ResistanceProfiler on GitHub" aria-label="ResistanceProfiler on GitHub">
        <span
          className="page-link-icon app-footer-icon-mask"
          aria-hidden="true"
          style={{ '--icon-src': `url(${githubIconSrc})` }}
        />
        <span>GitHub</span>
      </a>
      <span className="app-footer-sep" aria-hidden="true">·</span>
      <a className="app-footer-icon-link" href="https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html" target="_blank" rel="noreferrer" title="Jonas Fuchs Team website" aria-label="Jonas Fuchs Team website">
        <img className="page-link-icon website-link-icon" src={websiteIconSrc} alt="" aria-hidden="true" />
        <span>Fuchs Lab</span>
      </a>
    </>
  );

  return (
    <footer className="app-footer">
      {(legalLink || contactEmail) ? (
        <div className="app-footer-group">
          {projectLinks}
          {legalLink && (
            <span className="app-footer-sep" aria-hidden="true">·</span>
          )}
          {legalLink && (
            <a href={legalLink} target="_blank" rel="noreferrer">Legal notice</a>
          )}
          {/* The project links always precede Contact, so its separator is
              unconditional (when Contact exists) — it must not depend on
              whether Legal notice is present. */}
          {contactEmail && (
            <span className="app-footer-sep" aria-hidden="true">·</span>
          )}
          {contactEmail && (
            <a href={`mailto:${contactEmail}`}>Contact</a>
          )}
        </div>
      ) : (
        <div className="app-footer-group">{projectLinks}</div>
      )}
      {(cliVersion || webVersion) && (
        <div className="app-footer-group">
          {cliVersion && (
            <span className="app-footer-version">respro CLI v{cliVersion}</span>
          )}
          {cliVersion && webVersion && (
            <span className="app-footer-sep" aria-hidden="true">·</span>
          )}
          {webVersion && (
            <span className="app-footer-version">respro WebApp v{webVersion}</span>
          )}
        </div>
      )}
    </footer>
  );
}
