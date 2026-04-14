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
    if (badge.classList.contains('drug-badge')) {
      return `drug:${(badge.textContent || '').trim().toLowerCase()}`;
    }

    const classKey = Array.from(badge.classList).find(
      (cls) => cls.startsWith('badge-') && cls !== 'badge' && cls !== 'badge-interactive',
    );
    if (classKey) {
      return classKey;
    }
    return `txt:${(badge.textContent || '').trim().toLowerCase()}`;
  };

  const allTableBadges = () => Array.from(document.querySelectorAll('table .badge'));

  const allTableRows = () => Array.from(document.querySelectorAll('table tbody tr.data-row'));

  const rowHasBadgeKey = (row, key) => Array.from(row.querySelectorAll('.badge')).some(
    (badge) => resolveBadgeKey(badge) === key,
  );

  const normalize = (text) => (text || '').trim().toLowerCase();
  const NON_FILTERABLE_COLUMNS = new Set(['ref', 'in database']);

  const installTableFilterControls = (table) => {
    const wrapper = table.closest('.table-wrapper');
    const tbody = table.tBodies[0];
    if (!wrapper || !tbody) {
      return;
    }

    const headerCells = Array.from(table.querySelectorAll('thead th'));
    if (!headerCells.length) {
      return;
    }

    const controls = document.createElement('div');
    controls.className = 'table-controls';

    const label = document.createElement('label');
    label.textContent = 'Filter:';

    const select = document.createElement('select');
    const allOption = document.createElement('option');
    allOption.value = '-1';
    allOption.textContent = 'All columns';
    select.appendChild(allOption);

    const filterableColumnIndexes = [];

    headerCells.forEach((th, idx) => {
      const headerText = normalize(th.textContent);
      if (NON_FILTERABLE_COLUMNS.has(headerText)) {
        return;
      }

      const option = document.createElement('option');
      option.value = String(idx);
      option.textContent = normalize(th.textContent) ? th.textContent.trim() : `Column ${idx + 1}`;
      select.appendChild(option);
      filterableColumnIndexes.push(idx);
    });

    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = 'contains...';

    const reset = document.createElement('button');
    reset.type = 'button';
    reset.textContent = 'Reset';

    controls.appendChild(label);
    controls.appendChild(select);
    controls.appendChild(input);
    controls.appendChild(reset);
    wrapper.insertBefore(controls, table);

    const applyFilter = () => {
      const query = normalize(input.value);
      const selected = Number(select.value);
      Array.from(tbody.querySelectorAll('tr.data-row')).forEach((row) => {
        const rowId = row.dataset.rowId || '';
        const detailRow = rowId ? tbody.querySelector(`tr.detail-row[data-for="${rowId}"]`) : null;

        if (!query) {
          row.style.display = '';
          if (detailRow && !row.classList.contains('expanded')) {
            detailRow.style.display = 'none';
          }
          return;
        }

        const cells = Array.from(row.children);
        let haystack = '';
        if (selected >= 0 && selected < cells.length) {
          haystack = normalize(cells[selected].textContent);
        } else {
          haystack = normalize(
            filterableColumnIndexes
              .filter((idx) => idx >= 0 && idx < cells.length)
              .map((idx) => cells[idx].textContent)
              .join(' '),
          );
        }

        const matches = haystack.includes(query);
        row.style.display = matches ? '' : 'none';
        if (detailRow) {
          if (!matches) {
            detailRow.style.display = 'none';
          } else if (row.classList.contains('expanded')) {
            detailRow.style.display = '';
          }
        }
      });
    };

    select.addEventListener('change', applyFilter);
    input.addEventListener('input', applyFilter);
    reset.addEventListener('click', () => {
      select.value = '-1';
      input.value = '';
      applyFilter();
    });
  };

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
    installTableFilterControls(table);

    const tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    table.querySelectorAll('th').forEach((th, idx) => {
      th.classList.add('sortable-col');
      th.addEventListener('click', () => {
        const rows = Array.from(tbody.querySelectorAll('tr.data-row'));
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

        rows.forEach((row) => {
          tbody.appendChild(row);
          const rowId = row.dataset.rowId || '';
          const detailRow = rowId ? tbody.querySelector(`tr.detail-row[data-for="${rowId}"]`) : null;
          if (detailRow) {
            tbody.appendChild(detailRow);
          }
        });
      });
    });

    tbody.addEventListener('click', (event) => {
      if (!(event.target instanceof Element)) {
        return;
      }
      if (event.target.closest('.badge, a, button, input, select')) {
        return;
      }

      const row = event.target.closest('tr.expandable-row');
      if (!row) {
        return;
      }
      const rowId = row.dataset.rowId || '';
      if (!rowId) {
        return;
      }

      const detailRow = tbody.querySelector(`tr.detail-row[data-for="${rowId}"]`);
      if (!detailRow) {
        return;
      }

      const nextState = !row.classList.contains('expanded');
      row.classList.toggle('expanded', nextState);
      detailRow.classList.toggle('open', nextState);
      detailRow.style.display = nextState ? '' : 'none';
    });
  });
  document.querySelectorAll('.section-toggle').forEach((heading) => {
    heading.setAttribute('role', 'button');
    heading.tabIndex = 0;
    const toggle = () => heading.closest('.section').classList.toggle('open');
    heading.addEventListener('click', toggle);
    heading.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });

  // Auto-expand a collapsed section when an in-page anchor link targets it.
  document.addEventListener('click', (e) => {
    const link = e.target instanceof Element ? e.target.closest('a[href^="#"]') : null;
    if (!link) return;
    const id = link.getAttribute('href').slice(1);
    const target = id ? document.getElementById(id) : null;
    if (!target) return;
    const section = target.closest('.section');
    if (section && !section.classList.contains('open')) {
      section.classList.add('open');
    }
  });
})();

