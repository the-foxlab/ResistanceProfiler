import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUploadManager } from './useUploadManager';

// useUploadManager owns the upload-in-flight signal. Tests here verify the hook
// exposes ``isUploading`` and the setter that the upload hooks toggle.
describe('useUploadManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('exposes isUploading defaulting to false', () => {
    const { result } = renderHook(() => useUploadManager());

    expect(result.current.isUploading).toBe(false);
  });

  it('exposes beginUpload/endUpload that toggle isUploading', () => {
    const { result } = renderHook(() => useUploadManager());

    act(() => {
      result.current.setUploadProgress({ percent: 50, fileName: 'a.vcf' });
      result.current.beginUpload();
    });

    expect(result.current.isUploading).toBe(true);
    expect(result.current.uploadProgress.percent).toBe(50);
  });

  it('keeps isUploading true until every begun upload ends', () => {
    const { result } = renderHook(() => useUploadManager());

    act(() => {
      result.current.beginUpload();
      result.current.beginUpload();
    });
    expect(result.current.isUploading).toBe(true);

    act(() => {
      result.current.endUpload();
    });
    // One upload is still in flight, so the flag stays true.
    expect(result.current.isUploading).toBe(true);

    act(() => {
      result.current.endUpload();
    });
    expect(result.current.isUploading).toBe(false);
  });

  it('does not go negative when endUpload is called without a begin', () => {
    const { result } = renderHook(() => useUploadManager());

    act(() => {
      result.current.endUpload();
    });
    expect(result.current.isUploading).toBe(false);
  });
});
