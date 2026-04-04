(function () {
  const parseValue = (cell) => {
    const raw = (cell.dataset.sortValue || cell.textContent || '').trim();
    const num = Number(raw);
    if (!Number.isNaN(num) && raw !== '') {
      return { kind: 'num', value: num };
    }
    return { kind: 'txt', value: raw.toLowerCase() };
  };

  const resolveBadgeKey = (badge) => {
    const classKey = Array.from(badge.classList).find((cls) => cls.startsWith('badge-') && cls !== 'badge');
    if (classKey) {
      return classKey;
    }
    return `txt:${(badge.textContent || '').trim().toLowerCase()}`;
  };

  const allTableBadges = () => Array.from(document.querySelectorAll('table .badge'));

  const allTableRows = () => Array.from(document.querySelectorAll('table tbody tr'));

  const rowHasBadgeKey = (row, key) => Array.from(row.querySelectorAll('.badge')).some(
    (badge) => resolveBadgeKey(badge) === key,
  );

  const clearBadgeHighlight = () => {
    allTableBadges().forEach((badge) => {
      badge.classList.remove('badge-active');
    });
    allTableRows().forEach((row) => {
      row.classList.remove('row-badge-match');
      row.classList.remove('row-badge-muted');
    });
    document.body.dataset.activeBadgeKey = '';
  };

  const applyBadgeHighlight = (key) => {
    allTableBadges().forEach((badge) => {
      const same = resolveBadgeKey(badge) === key;
      badge.classList.toggle('badge-active', same);
    });
    allTableRows().forEach((row) => {
      const same = rowHasBadgeKey(row, key);
      row.classList.toggle('row-badge-match', same);
      row.classList.toggle('row-badge-muted', !same);
    });
    document.body.dataset.activeBadgeKey = key;
  };

  const toggleBadgeHighlight = (badge) => {
    const key = resolveBadgeKey(badge);
    if (document.body.dataset.activeBadgeKey === key) {
      clearBadgeHighlight();
      return;
    }
    applyBadgeHighlight(key);
  };

  allTableBadges().forEach((badge) => {
    badge.classList.add('badge-interactive');
    badge.tabIndex = 0;
    badge.setAttribute('role', 'button');
    badge.setAttribute('title', 'Click to highlight rows with matching badges');
  });

  document.addEventListener('click', (event) => {
    const badge = event.target instanceof Element ? event.target.closest('table .badge') : null;
    if (badge) {
      toggleBadgeHighlight(badge);
      return;
    }

    if (!event.target || !(event.target instanceof Element)) {
      return;
    }
    if (!event.target.closest('table') && document.body.dataset.activeBadgeKey) {
      clearBadgeHighlight();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      clearBadgeHighlight();
      return;
    }

    const isActivationKey = event.key === 'Enter' || event.key === ' ';
    if (!isActivationKey || !(event.target instanceof Element)) {
      return;
    }

    const badge = event.target.closest('table .badge');
    if (!badge) {
      return;
    }

    event.preventDefault();
    toggleBadgeHighlight(badge);
  });

  document.querySelectorAll('table.sortable').forEach((table) => {
    const tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    table.querySelectorAll('th').forEach((th, idx) => {
      th.classList.add('sortable-col');
      th.addEventListener('click', () => {
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const asc = th.dataset.order !== 'asc';
        table.querySelectorAll('th').forEach((head) => delete head.dataset.order);
        th.dataset.order = asc ? 'asc' : 'desc';

        rows.sort((a, b) => {
          const av = parseValue(a.children[idx]);
          const bv = parseValue(b.children[idx]);
          if (av.kind === 'num' && bv.kind === 'num') {
            return asc ? av.value - bv.value : bv.value - av.value;
          }
          if (av.value < bv.value) {
            return asc ? -1 : 1;
          }
          if (av.value > bv.value) {
            return asc ? 1 : -1;
          }
          return 0;
        });

        rows.forEach((row) => tbody.appendChild(row));
      });
    });
  });
})();

