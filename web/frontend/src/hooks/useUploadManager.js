import { useCallback, useMemo, useState, useSyncExternalStore } from 'react';

const INITIAL_PROGRESS = { percent: 0, fileName: '' };

export function createProgressStore(initial = INITIAL_PROGRESS) {
  let current = {
    percent: initial?.percent ?? 0,
    fileName: initial?.fileName ?? '',
  };
  const listeners = new Set();

  const getSnapshot = () => current;
  const subscribe = (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  };
  const setProgress = (nextOrUpdater) => {
    const next = typeof nextOrUpdater === 'function' ? nextOrUpdater(current) : nextOrUpdater;
    const normalized = {
      percent: next?.percent ?? 0,
      fileName: next?.fileName ?? '',
    };
    if (normalized.percent === current.percent && normalized.fileName === current.fileName) {
      return;
    }
    current = normalized;
    for (const listener of listeners) {
      listener();
    }
  };

  return { getSnapshot, subscribe, setProgress };
}

export const uploadProgressStore = createProgressStore();

const NOOP_SUBSCRIBE = () => () => {};

export function useUploadManager({ syncProgress = true, progressStore: externalStore = null } = {}) {
  const progressStore = useMemo(
    () => externalStore || createProgressStore(),
    [externalStore],
  );
  // A counter rather than a boolean so overlapping uploads (e.g. a VCF and its
  // reference) keep the submit button gated until the last one finishes.
  const [activeUploads, setActiveUploads] = useState(0);

  const subscribe = useCallback(
    (listener) => (syncProgress ? progressStore.subscribe(listener) : NOOP_SUBSCRIBE()),
    [progressStore, syncProgress],
  );

  const uploadProgress = useSyncExternalStore(
    subscribe,
    progressStore.getSnapshot,
    progressStore.getSnapshot,
  );

  const setUploadProgress = useCallback(
    (nextOrUpdater) => progressStore.setProgress(nextOrUpdater),
    [progressStore],
  );

  const beginUpload = useCallback(() => {
    setActiveUploads((count) => count + 1);
  }, []);

  const endUpload = useCallback(() => {
    setActiveUploads((count) => (count > 0 ? count - 1 : 0));
  }, []);

  return {
    uploadProgress,
    setUploadProgress,
    progressStore,
    isUploading: activeUploads > 0,
    beginUpload,
    endUpload,
  };
}
