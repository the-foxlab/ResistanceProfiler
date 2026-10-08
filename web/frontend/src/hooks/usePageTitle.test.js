import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';

import { usePageTitle } from './usePageTitle';

describe('usePageTitle', () => {
  it('sets document.title to the given value', () => {
    renderHook(() => usePageTitle('ResPro | Analysis'));
    expect(document.title).toBe('ResPro | Analysis');
  });

  it('restores nothing but updates again when the title changes', () => {
    const { rerender } = renderHook(({ title }) => usePageTitle(title), {
      initialProps: { title: 'ResPro | Home' },
    });
    rerender({ title: 'ResPro | About' });
    expect(document.title).toBe('ResPro | About');
  });

  it('cleans up on unmount so the next page can set its own title', () => {
    const { unmount } = renderHook(() => usePageTitle('ResPro | Databases'));
    unmount();
    expect(document.title).toBe('ResPro');
  });
});
