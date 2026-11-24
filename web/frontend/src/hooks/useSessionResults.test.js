import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { setArtifactBundleLimit } from '../api';
import { useSessionResults } from './useSessionResults';

describe('useSessionResults — artifact tracking and bundles', () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      blob: async () => new Blob(['zip-bytes']),
    }));
    global.URL.createObjectURL = vi.fn(() => 'blob:mock');
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    setArtifactBundleLimit(100);
  });

  afterEach(() => {
    setArtifactBundleLimit(100);
    vi.restoreAllMocks();
  });

  it('tracks every artifact id the backend returned for cleanup', () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.addResultArtifactPaths({ artifact_ids: ['id-html', 'id-json'] });
    });

    expect(result.current.reportPaths).toEqual(['id-html', 'id-json']);
  });

  it('bundles the artifact_ids across every session result', async () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([
        { artifact_ids: ['a-html', 'a-json'] },
        { artifact_ids: ['b-html', 'b-json'] },
      ]);
    });

    await act(async () => {
      await result.current.downloadAllSessionArtifacts(() => {});
    });

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      artifact_ids: ['a-html', 'a-json', 'b-html', 'b-json'],
    });
  });

  it('bundles only the selected results', async () => {
    const { result } = renderHook(() => useSessionResults());

    act(() => {
      result.current.setSessionResults([
        { artifact_ids: ['a-html'] },
        { artifact_ids: ['b-html'] },
      ]);
      result.current.setSelectedResultIndices(new Set([1]));
    });

    await act(async () => {
      await result.current.downloadSelectedArtifacts(() => {});
    });

    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      artifact_ids: ['b-html'],
    });
  });
});
