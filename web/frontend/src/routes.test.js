import { describe, it, expect } from 'vitest';

import { ROUTES, ROUTE_META, resolveRouteTitle } from './routes';

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
      '/analysis/reports': true,
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

describe('resolveRouteTitle', () => {
  it('maps each route to its ResPro | <Page> title', () => {
    expect(resolveRouteTitle('/')).toBe('ResPro | Home');
    expect(resolveRouteTitle('/analysis')).toBe('ResPro | Analysis');
    expect(resolveRouteTitle('/analysis/reports')).toBe('ResPro | Reports');
    expect(resolveRouteTitle('/databases')).toBe('ResPro | Databases');
    expect(resolveRouteTitle('/databases/mutations')).toBe('ResPro | Browse Mutations');
    expect(resolveRouteTitle('/databases/compare')).toBe('ResPro | Compare Databases');
    expect(resolveRouteTitle('/about')).toBe('ResPro | About');
  });

  it('falls back to the app name for unknown paths', () => {
    expect(resolveRouteTitle('/nope')).toBe('ResPro');
  });
});
