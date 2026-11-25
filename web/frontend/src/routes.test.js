import { describe, it, expect } from 'vitest';

import { ROUTE_META, SIDEBAR_SECTIONS } from './routes';

describe('route table', () => {
  it('defines the seven planned paths', () => {
    const paths = Object.keys(ROUTE_META);
    expect(paths).toEqual([
      '/',
      '/analysis',
      '/analysis/reports',
      '/databases',
      '/databases/mutations',
      '/databases/compare',
      '/about',
    ]);
  });

  it('exposes sidebar links matching valid ROUTE_META entries', () => {
    const sidebarPaths = SIDEBAR_SECTIONS.flatMap((section) => section.items.map((item) => item.path));
    expect(sidebarPaths).toEqual([
      '/analysis',
      '/analysis/reports',
      '/databases',
      '/databases/mutations',
      '/databases/compare',
    ]);
    for (const path of sidebarPaths) {
      expect(ROUTE_META[path]?.showsSidebar).toBe(true);
    }
  });

  it('marks workspace pages that show the database selector', () => {
    const withSelector = Object.fromEntries(
      Object.entries(ROUTE_META).map(([path, meta]) => [path, meta.showsDatabaseSelector]),
    );
    expect(withSelector).toEqual({
      '/': false,
      '/analysis': true,
      '/analysis/reports': false,
      '/databases': true,
      '/databases/mutations': true,
      '/databases/compare': false,
      '/about': false,
    });
  });

  it('marks pages that show the contextual sidebar', () => {
    const withSidebar = Object.fromEntries(
      Object.entries(ROUTE_META).map(([path, meta]) => [path, meta.showsSidebar]),
    );
    expect(withSidebar).toEqual({
      '/': false,
      '/analysis': true,
      '/analysis/reports': true,
      '/databases': true,
      '/databases/mutations': true,
      '/databases/compare': true,
      '/about': false,
    });
  });
});
