import { useEffect, useRef, useState } from 'react';
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
import { ThemeContext, useTheme } from '../hooks/useTheme';
import { AppFooter } from './AppFooter';
import { TourOverlay } from './tour/TourOverlay';
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

export function AppShell({ logic, children }) {
  const { pathname } = useLocation();
  const meta = ROUTE_META[pathname] || { showsSidebar: false, showsDatabaseSelector: false, breadcrumb: [] };
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [topBarVisible, setTopBarVisible] = useState(true);
  const topBarRef = useRef(null);
  const { preference: themePreference, resolved: themeResolved, setTheme } = useTheme();

  useEffect(() => {
    const topBar = topBarRef.current;
    if (!topBar) return undefined;

    if (!('IntersectionObserver' in window)) {
      const updateVisibility = () => {
        const bounds = topBar.getBoundingClientRect();
        setTopBarVisible(bounds.bottom >= bounds.height / 2);
      };
      window.addEventListener('scroll', updateVisibility, { passive: true });
      updateVisibility();
      return () => window.removeEventListener('scroll', updateVisibility);
    }

    const observer = new IntersectionObserver(([entry]) => {
      setTopBarVisible(entry.intersectionRatio >= 0.5);
    }, { threshold: 0.5 });
    observer.observe(topBar);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const updateScrollTopVisibility = () => {
      setShowScrollTop(window.scrollY > 240);
    };
    window.addEventListener('scroll', updateScrollTopVisibility, { passive: true });
    updateScrollTopVisibility();
    return () => window.removeEventListener('scroll', updateScrollTopVisibility);
  }, []);

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
        className={`sidebar-rail${collapsed ? ' collapsed' : ''}${mobileSidebarOpen ? ' mobile-open' : ''}${topBarVisible ? '' : ' header-hidden'}`}
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
          {SIDEBAR_SECTIONS.map((section) => {
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
                      onClick={() => setMobileSidebarOpen(false)}
                    >
                      <span className="sidebar-icon-mask" style={{ '--icon-src': `url(${SIDEBAR_ICONS[item.page]})` }} aria-hidden="true" />
                      <span className="sidebar-rail-text">{item.label}</span>
                    </NavLink>
                  ))}
                </div>
              </div>
            );
          })}
        </nav>
        <button
          type="button"
          className="mobile-sidebar-toggle"
          aria-label={mobileSidebarOpen ? 'Close sidebar' : 'Open sidebar'}
          aria-expanded={mobileSidebarOpen}
          aria-controls="sidebar-rail"
          onClick={() => setMobileSidebarOpen((open) => !open)}
        >
          {mobileSidebarOpen ? '«' : '»'}
        </button>
      </aside>
    )
    : null;

  return (
    <ThemeContext.Provider value={themeResolved}>
    <main className="dashboard-shell" id="main-content">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header ref={topBarRef} className="top-bar top-bar-full">
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
          <ThemeToggle preference={themePreference} onChange={setTheme} />
        </div>
      </header>

      <div className="dashboard-body">
        {sidebar}
        {mobileSidebarOpen && (
          <button
            type="button"
            className="mobile-sidebar-backdrop"
            aria-label="Close navigation"
            onClick={() => setMobileSidebarOpen(false)}
          />
        )}
        <div className="dashboard-main">
          {meta.heading && (
            <div className="workspace-subheader">
              <h1 className="workspace-heading">{meta.heading}</h1>
              {meta.showsDatabaseSelector && (
                <DatabaseSelectorBar
                  databases={logic.databases}
                  selectedDatabase={logic.selectedDatabase}
                  selectedDatabaseId={logic.selectedDatabaseId}
                  onDatabaseChange={logic.setSelectedDatabaseId}
                  selectId="topbar-db-select"
                  className="topbar-db-bar"
                />
              )}
            </div>
          )}
          <section className="panel-stack">
            {meta.description && <p className="workspace-description">{meta.description}</p>}
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
    </ThemeContext.Provider>
  );
}
