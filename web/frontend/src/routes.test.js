import { describe, it, expect } from 'vitest';

import { ROUTES, ROUTE_META } from './routes';

describe('route table', () => {
  it('defines the seven planned paths', () => {
    const paths = ROUTES.map((route) => route.path);
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
