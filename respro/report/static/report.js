(function () {
  const parseValue = (cell) => {
    const raw = (cell.dataset.sortValue || cell.textContent || '').trim();
    const num = Number(raw);
    if (!Number.isNaN(num) && raw !== '') {
      return { kind: 'num', value: num };
    }
    return { kind: 'txt', value: raw.toLowerCase() };
  };

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

