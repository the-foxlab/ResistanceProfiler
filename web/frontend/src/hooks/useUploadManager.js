import { useState } from 'react';

export function useUploadManager() {
  const [uploadProgress, setUploadProgress] = useState({
    percent: 0,
    fileName: '',
  });
  // A counter rather than a boolean so overlapping uploads (e.g. a VCF and its
  // reference) keep the submit button gated until the last one finishes.
  const [activeUploads, setActiveUploads] = useState(0);

  const beginUpload = () => {
    setActiveUploads((count) => count + 1);
  };

  const endUpload = () => {
    setActiveUploads((count) => (count > 0 ? count - 1 : 0));
  };

  return {
    uploadProgress,
    setUploadProgress,
    isUploading: activeUploads > 0,
    beginUpload,
    endUpload,
  };
}
