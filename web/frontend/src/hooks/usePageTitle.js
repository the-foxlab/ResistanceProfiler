import { useEffect } from 'react';

const BASE_TITLE = 'ResPro';

export function usePageTitle(title) {
  useEffect(() => {
    document.title = title || BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [title]);
}
