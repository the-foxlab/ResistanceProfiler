import { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router';

import logoSrc from '../assets/logo.svg';
import aboutIconSrc from '../assets/icon-about.svg';
import databaseIconSrc from '../assets/icon-database.svg';
import mutationsIconSrc from '../assets/search.svg';
import homeIconSrc from '../assets/home.svg';
import reportIconSrc from '../assets/reports.svg';
import compareIconSrc from '../assets/icon-venn.svg';
import { DatabaseSelectorBar } from './DatabaseSelectorBar';
import { ThemeToggle } from './ThemeToggle';
import { useTheme } from '../hooks/useTheme';
import { AppFooter } from './AppFooter';
import { TourOverlay } from './tour/TourOverlay';
import { useTour } from './tour/TourContext';
import { ROUTE_META, SIDEBAR_SECTIONS } from '../routes';

const SIDEBAR_ICONS = {
  analyze: homeIconSrc,
  results: reportIconSrc,
  database: databaseIconSrc,
  mutations: mutationsIconSrc,
  compare: compareIconSrc,
};

const TOP_NAV = [
  { path: '/', label: 'Home', end: true },
  { path: '/analysis', label: 'Analysis' },
  { path: '/databases', label: 'Databases' },
  { path: '/about', label: 'About' },
];

const COLLAPSE_STORAGE_KEY = 'respro.sidebar.collapsed';

function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function AppShell({ logic, onStartTour, children }) {
  const { pathname } = useLocation();
  const meta = ROUTE_META[pathname] || { showsSidebar: false, showsDatabaseSelector: false, breadcrumb: [] };
  const { startTour } = useTour();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const { preference: themePreference, resolved: themeResolved, setTheme } = useTheme();

  // Close the off-canvas drawer whenever the route changes.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    const updateScrollTopVisibility = () => {
      setShowScrollTop(window.scrollY > 240);
    };
    window.addEventListener('scroll', updateScrollTopVisibility, { passive: true });
    updateScrollTopVisibility();
    return () => window.removeEventListener('scroll', updateScrollTopVisibility);
  }, []);

  // Close the off-canvas drawer on Escape while it is open. Bound only when
  // the drawer is open so it never swallows Escape intended for other widgets
  // (e.g. the plot modal in AnalyzeTab manages its own Escape listener).
  useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setMobileNavOpen(false);
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [mobileNavOpen]);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, current ? '0' : '1');
      } catch {
        // Storage unavailable: collapse is session-only.
      }
      return !current;
    });
  };

  const sidebar = meta.showsSidebar
    ? (
      <aside
        id="sidebar-rail"
        className={`sidebar-rail ${mobileNavOpen ? 'open' : ''} ${collapsed ? 'collapsed' : ''}`}
        aria-label="Section navigation"
      >
        <nav className="sidebar-rail-nav">
          <button
            type="button"
            className="sidebar-collapse-btn"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            onClick={toggleCollapsed}
          >
            {collapsed ? '»' : '«'}
          </button>
          {SIDEBAR_SECTIONS.map((section, index) => {
            const isActiveSection = pathname.startsWith(section.basePath);
            if (!isActiveSection) {
              return null;
            }
            return (
              <div key={section.label} className="sidebar-rail-group">
                <div className="sidebar-rail-section" aria-label={section.label}>
                  {section.items.map((item) => (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      end
                      className={({ isActive }) => `sidebar-rail-link ${isActive ? 'active' : ''}`}
                      aria-label={item.label}
                      data-tour-target={`sidebar-${item.page}`}
                    >
                      <span className="sidebar-icon-mask" style={{ '--icon-src': `url(${SIDEBAR_ICONS[item.page]})` }} aria-hidden="true" />
                      <span className="sidebar-rail-text">{item.label}</span>
                    </NavLink>
                  ))}
                </div>
                {index < SIDEBAR_SECTIONS.length - 1 && <div className="sidebar-rail-divider" role="presentation" />}
              </div>
            );
          })}
        </nav>
      </aside>
    )
    : null;

  return (
    <main className="dashboard-shell" id="main-content">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="top-bar top-bar-full">
        <button
          type="button"
          className={`mobile-menu-btn ${mobileNavOpen ? 'open' : ''}`}
          aria-label="Toggle navigation"
          aria-expanded={mobileNavOpen}
          aria-controls="sidebar-rail"
          onClick={() => setMobileNavOpen((v) => !v)}
        >
          <span className="mobile-menu-bar" aria-hidden="true" />
          <span className="mobile-menu-bar" aria-hidden="true" />
          <span className="mobile-menu-bar" aria-hidden="true" />
        </button>
        <Link to="/" className="top-bar-brand-block" aria-label="ResistanceProfiler home">
          <div className="brand-logo-wrap">
            <img className="brand-logo" src={logoSrc} alt="ResistanceProfiler logo" />
          </div>
        </Link>
        <nav className="top-bar-nav" aria-label="Primary">
          {TOP_NAV.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              className={({ isActive }) => `top-bar-nav-link ${isActive ? 'active' : ''}`}
            >
              {({ isActive }) => (
                <span aria-current={isActive ? 'page' : undefined}>{item.label}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="top-bar-actions">
          <Link to="/analysis" className="analyze-primary top-bar-launch">
            Launch
          </Link>
          {onStartTour && (
            <button
              type="button"
              className="top-bar-help"
              title="Take a guided tour of the app"
              aria-label="Start the guided tour"
              onClick={onStartTour}
            >
              ?
            </button>
          )}
          <ThemeToggle preference={themePreference} onChange={setTheme} />
        </div>
      </header>

      <div className="dashboard-body">
        {sidebar}
        {/* Scrim behind the off-canvas drawer on mobile. Hidden on desktop and
            whenever the drawer is closed via CSS (.is-open). Clicking it
            dismisses the drawer, mirroring a modal overlay. */}
        {sidebar && (
          <div
            className={`mobile-nav-backdrop ${mobileNavOpen ? 'is-open' : ''}`}
            aria-hidden="true"
            onClick={() => setMobileNavOpen(false)}
          />
        )}
        <div className="dashboard-main">
          {meta.breadcrumb.length > 0 && (
            <nav className="breadcrumb-row" aria-label="Breadcrumb">
              {meta.breadcrumb.map((crumb, index) => (
                <span key={crumb} className="breadcrumb-item">
                  {index > 0 && <span className="breadcrumb-sep" aria-hidden="true">›</span>}
                  <span className="breadcrumb-crumb">{crumb}</span>
                </span>
              ))}
            </nav>
          )}
          {meta.showsDatabaseSelector && (
            <div className="workspace-subheader">
              <DatabaseSelectorBar
                databases={logic.databases}
                selectedDatabase={logic.selectedDatabase}
                selectedDatabaseId={logic.selectedDatabaseId}
                onDatabaseChange={logic.setSelectedDatabaseId}
                selectId="topbar-db-select"
                className="topbar-db-bar"
              />
            </div>
          )}
          <section className="panel-stack">
            {children}
          </section>
          <AppFooter
            legalLink={logic.legalLink}
            contactEmail={logic.contactEmail}
            cliVersion={logic.cliVersion}
            webVersion={logic.webVersion}
          />
        </div>
      </div>
      <button
        type="button"
        className={`scroll-top-button ${showScrollTop ? 'is-visible' : ''}`}
        title="Scroll to top"
        aria-label="Scroll to top"
        onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      >
        ↑
      </button>
      <TourOverlay />
    </main>
  );
}
