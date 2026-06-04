import { useState } from 'react';

export function useUploadManager() {
  const [uploadProgress, setUploadProgress] = useState({
    percent: 0,
    fileName: '',
  });

  return {
    uploadProgress,
    setUploadProgress,
  };
}
