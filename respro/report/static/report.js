/**
 * Report tab switching logic.
 */

document.addEventListener('DOMContentLoaded', function () {
  const tabButtons = document.querySelectorAll('.tab-button');
  const tabContents = document.querySelectorAll('.tab-content');
  const plotModal = document.getElementById('plot-modal');
  const plotOpenButton = document.getElementById('plot-modal-open');
  const plotCloseButton = document.getElementById('plot-modal-close');
  const plotBackdrop = document.getElementById('plot-modal-backdrop');
  const plotImage = plotModal ? plotModal.querySelector('.plot-modal-image') : null;
  const structureModal = document.getElementById('drug-structure-modal');
  const structureButtons = document.querySelectorAll('.drug-structure-button');
  const structureBackdrop = document.getElementById('drug-structure-modal-backdrop');
  const structureTitle = document.getElementById('drug-structure-modal-title');
  const structureImg = document.getElementById('drug-structure-modal-img');
  const sequenceModal = document.getElementById('feature-sequence-modal');
  const sequenceButtons = document.querySelectorAll('.feature-sequence-button');
  const sequenceBackdrop = document.getElementById('feature-sequence-modal-backdrop');
  const sequenceTitle = document.getElementById('feature-sequence-modal-title');
  const sequenceBlock = document.getElementById('feature-sequence-modal-sequence');
  const reportMain = document.querySelector('.report-main');
  const scrollTopButton = document.getElementById('scroll-top-button');

  tabButtons.forEach(button => {
    button.addEventListener('click', function () {
      const tabId = this.getAttribute('data-tab');

      // Remove active class from all buttons and contents
      tabButtons.forEach(btn => btn.classList.remove('active'));
      tabContents.forEach(content => content.classList.remove('active'));

      // Add active class to clicked button
      this.classList.add('active');

      // Add active class to corresponding content
      const contentElement = document.getElementById(`tab-${tabId}`);
      if (contentElement) {
        contentElement.classList.add('active');
      }
    });
  });

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  }

  if (plotModal && plotOpenButton && plotCloseButton && plotBackdrop) {
    plotOpenButton.addEventListener('click', function () {
      if (window.parent !== window && plotImage) {
        // Embedded in the webapp shell: delegate the modal to the parent so
        // the plot escapes the iframe. Send the image data in the payload so
        // the parent does not need cross-origin contentDocument access (which
        // throws in dev mode where the report is served from a different
        // origin than the Vite dev server).
        window.parent.postMessage({
          type: 'respro:open-plot',
          src: plotImage.currentSrc || plotImage.src,
          alt: plotImage.alt || 'Resistance plot',
        }, window.location.origin);
        return;
      }
      openModal(plotModal);
    });
    plotCloseButton.addEventListener('click', function () {
      closeModal(plotModal);
    });
    plotBackdrop.addEventListener('click', function () {
      closeModal(plotModal);
    });
  }

  // ── Hosted height sync ──────────────────────────────────────────────────
  // When embedded in the webapp iframe, keep the parent's frame height in
  // sync with this document's content. The parent cannot read
  // contentDocument across origins (dev mode), so the report reports its
  // own height. A ResizeObserver catches late layout shifts (images, tab
  // switches) that arrive after the initial load.
  if (window.parent !== window && typeof ResizeObserver !== 'undefined') {
    var postReportHeight = function () {
      var height = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight
      );
      if (height > 0) {
        window.parent.postMessage({ type: 'respro:report-height', height: height }, window.location.origin);
      }
    };
    var heightObserver = new ResizeObserver(postReportHeight);
    heightObserver.observe(document.body);
    heightObserver.observe(document.documentElement);
    window.addEventListener('load', postReportHeight);
    postReportHeight();
  }

  if (sequenceModal && sequenceBackdrop && sequenceTitle && sequenceBlock) {
    const toggleNt = document.getElementById('seq-toggle-nt');
    const toggleAa = document.getElementById('seq-toggle-aa');

    function activateToggle(activeBtn, inactiveBtn) {
      if (activeBtn) { activeBtn.classList.add('seq-toggle-btn--active'); }
      if (inactiveBtn) { inactiveBtn.classList.remove('seq-toggle-btn--active'); }
    }

    sequenceButtons.forEach(button => {
      button.addEventListener('click', function () {
        const ntSeq = this.getAttribute('data-nt-sequence') || '';
        const aaSeq = this.getAttribute('data-aa-sequence') || '';
        sequenceTitle.textContent = this.getAttribute('data-feature-title') || 'Feature sequence';

        const showNt = Boolean(ntSeq);
        sequenceBlock.textContent = showNt ? ntSeq : aaSeq;
        activateToggle(showNt ? toggleNt : toggleAa, showNt ? toggleAa : toggleNt);

        if (toggleNt) {
          toggleNt.disabled = !ntSeq;
          toggleNt.onclick = function () {
            if (ntSeq) {
              sequenceBlock.textContent = ntSeq;
              activateToggle(toggleNt, toggleAa);
            }
          };
        }
        if (toggleAa) {
          toggleAa.disabled = !aaSeq;
          toggleAa.onclick = function () {
            if (aaSeq) {
              sequenceBlock.textContent = aaSeq;
              activateToggle(toggleAa, toggleNt);
            }
          };
        }

        openModal(sequenceModal);
      });
    });

    sequenceBackdrop.addEventListener('click', function () {
      closeModal(sequenceModal);
    });
  }

  if (structureModal && structureBackdrop && structureTitle && structureImg) {
    structureButtons.forEach(button => {
      button.addEventListener('click', function () {
        const url = this.getAttribute('data-structure-url') || '';
        const title = this.getAttribute('data-drug-title') || 'Structure';
        structureTitle.textContent = title;
        structureImg.src = url;
        structureImg.alt = 'Chemical structure of ' + title;
        openModal(structureModal);
      });
    });
    structureBackdrop.addEventListener('click', function () {
      closeModal(structureModal);
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') {
      return;
    }

    if (plotModal && plotModal.classList.contains('is-open')) {
      closeModal(plotModal);
    }

    if (structureModal && structureModal.classList.contains('is-open')) {
      closeModal(structureModal);
    }

    if (sequenceModal && sequenceModal.classList.contains('is-open')) {
      closeModal(sequenceModal);
    }
  });

  if (scrollTopButton) {
    const threshold = 240;

    const getScrollTop = function () {
      const mainScrollTop = reportMain ? reportMain.scrollTop : 0;
      const windowScrollTop = window.scrollY || document.documentElement.scrollTop || 0;
      return Math.max(mainScrollTop, windowScrollTop);
    };

    const updateScrollTopButton = function () {
      const shouldShow = getScrollTop() > threshold;
      scrollTopButton.classList.toggle('is-visible', shouldShow);
    };

    const scrollHandler = function () {
      updateScrollTopButton();
    };

    if (reportMain) {
      reportMain.addEventListener('scroll', scrollHandler, { passive: true });
    }
    window.addEventListener('scroll', scrollHandler, { passive: true });

    scrollTopButton.addEventListener('click', function () {
      if (reportMain) {
        reportMain.scrollTo({ top: 0, behavior: 'smooth' });
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    updateScrollTopButton();
  }

  // ── Info panel tooltips (position: fixed to escape table scroll container) ─
  document.querySelectorAll('.db-hit-freq-info').forEach(function (trigger) {
    const panel = trigger.querySelector('.db-hit-info-panel');
    if (!panel) { return; }
    trigger.addEventListener('mouseenter', function () {
      const rect = trigger.getBoundingClientRect();
      const panelWidth = 352; // 22rem at 16px base
      const leftRaw = rect.left - 64; // align left of panel ~4rem left of trigger
      const left = Math.max(8, Math.min(leftRaw, window.innerWidth - panelWidth - 8));
      panel.style.left = left + 'px';
      panel.style.top = (rect.bottom + 6) + 'px';
    });
  });

  const mutationTab = document.getElementById('tab-all-mutations');
  const mutationTable = mutationTab && mutationTab.querySelector('.mutation-table');
  if (!mutationTable) {
    return;
  }

  const mutationTbody = mutationTable.querySelector('tbody');
  const mutationSortButton = mutationTable.querySelector('.mutation-sort-button');
  const mutationSearchInput = document.getElementById('mutation-search-input');
  const mutationToolbar = mutationTab.querySelector('.mutation-toolbar');
  const mutationFilterMenus = Array.from(mutationTab.querySelectorAll('.mutation-filter-menu'));
  const mutationResetButton = mutationTab.querySelector('.mutation-reset-button');
  const mutationDownloadButton = mutationTab.querySelector('.mutation-download-button');

  if (mutationTbody) {
    mutationTbody.querySelectorAll('.mutation-row--expandable').forEach(function (row) {
      const toggleRowAlignment = function (event) {
        if (event && event.target && event.target.closest('a, button')) {
          return;
        }
        const rowId = row.getAttribute('data-alignment-row');
        if (!rowId) {
          return;
        }
        const alignmentRow = document.getElementById(rowId);
        if (!alignmentRow) {
          return;
        }
        const isOpen = !alignmentRow.hidden;
        alignmentRow.hidden = isOpen;
        row.setAttribute('aria-expanded', String(!isOpen));
      };

      row.addEventListener('click', toggleRowAlignment);
      row.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') {
          return;
        }
        event.preventDefault();
        toggleRowAlignment(event);
      });
    });

    const collectMutationRowPairs = function () {
      const rows = Array.from(mutationTbody.querySelectorAll('.mutation-row'));
      return rows.map(function (row) {
        const nextRow = row.nextElementSibling;
        const alignmentRow = nextRow && nextRow.classList.contains('mutation-alignment-row')
          ? nextRow
          : null;
        return { row: row, alignmentRow: alignmentRow };
      });
    };

    const getFieldValue = function (row, fieldName) {
      return (row.getAttribute(`data-${fieldName}-value`) || '').trim();
    };

    const getFieldValues = function (row, fieldName) {
      if (fieldName === 'database') {
        const raw = (row.getAttribute('data-database-values') || '').trim();
        if (!raw) {
          return [];
        }
        return raw.split('|').map(function (value) {
          return value.trim();
        }).filter(Boolean);
      }
      const value = getFieldValue(row, fieldName);
      return value ? [value] : [];
    };

    const buildMutationFilterMenus = function () {
      const fieldConfigs = [
        { field: 'feature', label: 'Feature' },
        { field: 'consequence', label: 'Consequence' },
        { field: 'database', label: 'In Database' },
      ];

      fieldConfigs.forEach(function (config) {
        const menu = document.querySelector(`.mutation-filter-menu[data-filter-group="${config.field}"]`);
        const optionsContainer = menu?.querySelector('.mutation-filter-options');
        if (!optionsContainer) {
          return;
        }

        const values = Array.from(
          new Set(
            collectMutationRowPairs().flatMap(function (pair) {
              return getFieldValues(pair.row, config.field);
            }).filter(Boolean),
          ),
        ).sort(function (left, right) {
          return left.localeCompare(right);
        });

        optionsContainer.textContent = '';

        if (values.length > 0) {
          const toggleAll = document.createElement('button');
          toggleAll.type = 'button';
          toggleAll.className = 'mutation-filter-toggle-all';
          toggleAll.textContent = 'Uncheck all';
          toggleAll.addEventListener('click', function () {
            const checkboxes = Array.from(optionsContainer.querySelectorAll('.mutation-filter-option'));
            const allChecked = checkboxes.every(function (cb) { return cb.checked; });
            checkboxes.forEach(function (cb) { cb.checked = !allChecked; });
            toggleAll.textContent = allChecked ? 'Check all' : 'Uncheck all';
            applyMutationFilter();
          });
          optionsContainer.appendChild(toggleAll);
        }

        values.forEach(function (value) {
          const label = document.createElement('label');
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.className = 'mutation-filter-option';
          input.value = value;
          input.checked = true;
          input.dataset.field = config.field;
          input.addEventListener('change', function () {
            const checkboxes = Array.from(optionsContainer.querySelectorAll('.mutation-filter-option'));
            const allChecked = checkboxes.every(function (cb) { return cb.checked; });
            const toggleBtn = optionsContainer.querySelector('.mutation-filter-toggle-all');
            if (toggleBtn) { toggleBtn.textContent = allChecked ? 'Uncheck all' : 'Check all'; }
          });

          label.appendChild(input);
          label.appendChild(document.createTextNode(value));
          optionsContainer.appendChild(label);
        });

        if (values.length === 0) {
          const empty = document.createElement('span');
          empty.className = 'mutation-filter-empty';
          empty.textContent = 'No values';
          optionsContainer.appendChild(empty);
        }
      });
    };

    buildMutationFilterMenus();

    const closeAllMutationFilterMenus = function (exceptMenu) {
      mutationFilterMenus.forEach(function (menu) {
        if (!exceptMenu || menu !== exceptMenu) {
          menu.open = false;
        }
      });
    };

    mutationFilterMenus.forEach(function (menu) {
      const summary = menu.querySelector('summary');
      if (!summary) {
        return;
      }
      summary.addEventListener('click', function (event) {
        event.preventDefault();
        const willOpen = !menu.open;
        closeAllMutationFilterMenus(menu);
        menu.open = willOpen;
      });
    });

    document.addEventListener('click', function (event) {
      if (!mutationToolbar || mutationToolbar.contains(event.target)) {
        return;
      }
      closeAllMutationFilterMenus();
    });

    if (mutationToolbar) {
      mutationToolbar.addEventListener('click', function (event) {
        if (!event.target.closest('.mutation-filter-menu')) {
          closeAllMutationFilterMenus();
        }
      });
    }

    const applyMutationSort = function (direction) {
      const pairs = collectMutationRowPairs();
      const factor = direction === 'desc' ? -1 : 1;
      pairs.sort(function (leftPair, rightPair) {
        const leftPos = Number(leftPair.row.getAttribute('data-nt-pos') || 0);
        const rightPos = Number(rightPair.row.getAttribute('data-nt-pos') || 0);
        if (leftPos !== rightPos) {
          return (leftPos - rightPos) * factor;
        }
        const leftNt = (leftPair.row.querySelector('.mutation-nt')?.textContent || '').trim();
        const rightNt = (rightPair.row.querySelector('.mutation-nt')?.textContent || '').trim();
        return leftNt.localeCompare(rightNt) * factor;
      });

      pairs.forEach(function (pair) {
        mutationTbody.appendChild(pair.row);
        if (pair.alignmentRow) {
          mutationTbody.appendChild(pair.alignmentRow);
        }
      });

      applyMutationFilter();
    };

    const applyMutationFilter = function () {
      const query = (mutationSearchInput?.value || '').trim().toLowerCase();

      const selectedValuesByField = new Map();
      mutationFilterMenus.forEach(function (menu) {
        const fieldName = menu.getAttribute('data-filter-group') || '';
        const checkedValues = Array.from(menu.querySelectorAll('.mutation-filter-option:checked')).map(function (input) {
          return input.value;
        });
        selectedValuesByField.set(fieldName, checkedValues);
      });

      collectMutationRowPairs().forEach(function (pair) {
        const row = pair.row;
        const alignmentRow = pair.alignmentRow;

        const searchableText = row.textContent?.toLowerCase() || '';
        const queryMatch = query.length === 0 || searchableText.includes(query);

        const fieldMatches = [
          {
            selected: selectedValuesByField.get('feature') || [],
            values: getFieldValues(row, 'feature'),
          },
          {
            selected: selectedValuesByField.get('consequence') || [],
            values: getFieldValues(row, 'consequence'),
          },
          {
            selected: selectedValuesByField.get('database') || [],
            values: getFieldValues(row, 'database'),
          },
        ];

        const selectedMatch = fieldMatches.every(function (field) {
          return field.values.some(function (value) {
            return field.selected.includes(value);
          });
        });

        const isMatch = queryMatch && selectedMatch;

        row.hidden = !isMatch;
        if (alignmentRow) {
          if (!isMatch) {
            alignmentRow.hidden = true;
            row.setAttribute('aria-expanded', 'false');
          }
          alignmentRow.style.display = isMatch ? '' : 'none';
        }
      });
    };

    applyMutationSort('asc');

    if (mutationSortButton) {
      mutationSortButton.addEventListener('click', function () {
        closeAllMutationFilterMenus();
        const currentDirection = this.getAttribute('data-sort-direction') || 'asc';
        const nextDirection = currentDirection === 'asc' ? 'desc' : 'asc';
        this.setAttribute('data-sort-direction', nextDirection);
        const indicator = this.querySelector('.mutation-sort-indicator');
        if (indicator) {
          indicator.textContent = nextDirection === 'asc' ? '↑' : '↓';
        }
        applyMutationSort(nextDirection);
      });
    }

    if (mutationSearchInput) {
      mutationSearchInput.addEventListener('input', function () {
        closeAllMutationFilterMenus();
        applyMutationFilter();
      });
    }

    document.querySelectorAll('.mutation-filter-option').forEach(function (field) {
      field.addEventListener('change', applyMutationFilter);
    });

    if (mutationResetButton) {
      mutationResetButton.addEventListener('click', function () {
        closeAllMutationFilterMenus();
        if (mutationSearchInput) {
          mutationSearchInput.value = '';
        }
        document.querySelectorAll('.mutation-filter-option').forEach(function (field) {
          field.checked = true;
        });
        mutationTab.querySelectorAll('.mutation-filter-toggle-all').forEach(function (btn) {
          btn.textContent = 'Uncheck all';
        });
        applyMutationFilter();
      });
    }

    if (mutationDownloadButton) {
      mutationDownloadButton.addEventListener('click', function () {
        closeAllMutationFilterMenus();

        const hasDbCol = !!mutationTab.querySelector('thead th:last-child') &&
          mutationTab.querySelector('thead th:last-child').textContent.trim() === 'In database';
        const headerTexts = Array.prototype.map.call(
          mutationTab.querySelectorAll('thead th'),
          function (th) { return th.textContent.trim(); }
        );
        const hasUserRefCol = headerTexts.indexOf('NT change user reference') !== -1;
        const headers = ['Feature', 'NT change stored reference'];
        if (hasUserRefCol) {
          headers.push('NT change user reference');
        }
        headers.push('AA change', 'Consequence', 'Variant frequency');
        if (hasDbCol) {
          headers.push('In database');
        }
        const lines = [headers.join('\t')];

        collectMutationRowPairs().forEach(function (pair) {
          const row = pair.row;
          if (row.hidden) {
            return;
          }

          const feature = (row.querySelector('.mutation-feature-cell, td:first-child')?.textContent || '').trim();
          const ntChangeStored = (row.querySelector('.mutation-nt')?.textContent || '').trim();
          const ntChangeUser = hasUserRefCol
            ? (row.querySelector('.mutation-nt-user')?.textContent || '').trim()
            : '';
          const aaChange = (row.querySelector('.mutation-aa')?.textContent || '').trim();
          const consequence = (row.querySelector('.mutation-consequence')?.textContent || '').trim();
          const alleleFreq = (row.querySelector('.mutation-freq')?.textContent || '').trim();

          const fields = [feature, ntChangeStored];
          if (hasUserRefCol) {
            fields.push(ntChangeUser);
          }
          fields.push(aaChange, consequence, alleleFreq);
          if (hasDbCol) {
            const inDatabase = ((row.getAttribute('data-database-values') || 'None')
              .split('|')
              .map(function (value) {
                return value.trim();
              })
              .filter(Boolean)
              .join('; '));
            fields.push(inDatabase);
          }
          lines.push(fields.map(function (value) {
            return value.replace(/\t/g, ' ').replace(/\n/g, ' ');
          }).join('\t'));
        });

        downloadTsv(lines, 'variant_profile.tsv');
      });
    }

    applyMutationFilter();
  }

  // ── Utility: TSV file download ────────────────────────────────────────────
  function downloadTsv(lines, filename) {
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/tab-separated-values;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // ── Generic faceted-cascade table filter ─────────────────────────────────
  // Shared by Database Hits and Similarity to Database Entries.
  //
  // opts:
  //   table          – <table> element
  //   rowSelector    – CSS selector for data rows inside tbody
  //   fieldConfigs   – [{ field, attr }]: filter-group name → row data-attribute
  //   searchInput    – search <input> (optional)
  //   filterMenus    – array of <details> filter menus
  //   toolbar        – toolbar container for outside-click scope
  //   resetButton, downloadButton – buttons (optional)
  //   onRowHidden    – function(row) called when a row is filtered out (optional)
  //   onDownload     – function(collectRows, table) called on download (optional)
  function createFacetedTable(opts) {
    if (!opts.table) { return; }
    const tbody = opts.table.querySelector('tbody');
    if (!tbody) { return; }

    const { fieldConfigs, filterMenus, toolbar, searchInput, resetButton, downloadButton } = opts;

    const collectRows = function () {
      return Array.from(tbody.querySelectorAll(opts.rowSelector));
    };

    // Build or rebuild the checkbox list for one filter.
    // checkedValues: Set of values to keep checked; null = check all.
    const buildOptions = function (config, rawValues, checkedValues) {
      const menu = document.querySelector(
        `.mutation-filter-menu[data-filter-group="${config.field}"]`,
      );
      const container = menu && menu.querySelector('.mutation-filter-options');
      if (!container) { return; }

      const values = Array.from(new Set(rawValues.filter(Boolean)))
        .sort(function (a, b) { return a.localeCompare(b); });

      container.textContent = '';

      if (values.length > 0) {
        const toggleAll = document.createElement('button');
        toggleAll.type = 'button';
        toggleAll.className = 'mutation-filter-toggle-all';
        toggleAll.textContent = 'Uncheck all';
        toggleAll.addEventListener('click', function () {
          const boxes = Array.from(container.querySelectorAll('.mutation-filter-option:not([disabled])'));
          const allChecked = boxes.every(function (cb) { return cb.checked; });
          boxes.forEach(function (cb) { cb.checked = !allChecked; });
          toggleAll.textContent = allChecked ? 'Check all' : 'Uncheck all';
          refreshCascade(config.field);
        });
        container.appendChild(toggleAll);
      }

      values.forEach(function (value) {
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'mutation-filter-option';
        input.value = value;
        input.checked = checkedValues === null || checkedValues.has(value);
        input.dataset.field = config.field;
        input.addEventListener('change', function () {
          const boxes = Array.from(container.querySelectorAll('.mutation-filter-option:not([disabled])'));
          const allChecked = boxes.every(function (cb) { return cb.checked; });
          const btn = container.querySelector('.mutation-filter-toggle-all');
          if (btn) { btn.textContent = allChecked ? 'Uncheck all' : 'Check all'; }
          refreshCascade(config.field);
        });
        label.appendChild(input);
        label.appendChild(document.createTextNode(value));
        container.appendChild(label);
      });

      if (values.length === 0) {
        const empty = document.createElement('span');
        empty.className = 'mutation-filter-empty';
        empty.textContent = 'No values';
        container.appendChild(empty);
      }
    };

    const buildFilterMenus = function () {
      fieldConfigs.forEach(function (config) {
        const rawValues = collectRows().map(function (row) {
          return (row.getAttribute(config.attr) || '').trim();
        });
        buildOptions(config, rawValues, null);
      });
    };

    // When one filter changes, enable/disable options in the other filters to
    // reflect what is still reachable — without rebuilding menus from scratch.
    const refreshCascade = function (changedField) {
      const selections = {};
      fieldConfigs.forEach(function (config) {
        selections[config.field] = new Set(
          Array.from(
            document.querySelectorAll(
              `.mutation-filter-menu[data-filter-group="${config.field}"] .mutation-filter-option:checked:not([disabled])`,
            ),
          ).map(function (cb) { return cb.value; }),
        );
      });

      fieldConfigs.forEach(function (config) {
        if (config.field === changedField) { return; }
        const menu = document.querySelector(`.mutation-filter-menu[data-filter-group="${config.field}"]`);
        const container = menu && menu.querySelector('.mutation-filter-options');
        if (!container) { return; }

        const candidateRows = collectRows().filter(function (row) {
          return fieldConfigs.every(function (c) {
            if (c.field === config.field) { return true; }
            const sel = selections[c.field];
            if (!sel || sel.size === 0) { return true; }
            return sel.has((row.getAttribute(c.attr) || '').trim());
          });
        });

        const available = new Set(
          candidateRows
            .map(function (row) { return (row.getAttribute(config.attr) || '').trim(); })
            .filter(Boolean),
        );

        Array.from(container.querySelectorAll('label')).forEach(function (label) {
          const input = label.querySelector('.mutation-filter-option');
          if (!input) { return; }
          const reachable = available.has(input.value);
          if (reachable && input.disabled) {
            input.disabled = false;
            input.checked = true;
            label.classList.remove('mutation-filter-option-disabled');
          } else if (!reachable && !input.disabled) {
            input.disabled = true;
            input.checked = false;
            label.classList.add('mutation-filter-option-disabled');
          }
        });

        const enabledBoxes = Array.from(container.querySelectorAll('.mutation-filter-option:not([disabled])'));
        const allChecked = enabledBoxes.length > 0 && enabledBoxes.every(function (cb) { return cb.checked; });
        const btn = container.querySelector('.mutation-filter-toggle-all');
        if (btn) { btn.textContent = allChecked ? 'Uncheck all' : 'Check all'; }
      });

      applyFilter();
    };

    const closeAllMenus = function (exceptMenu) {
      filterMenus.forEach(function (menu) {
        if (!exceptMenu || menu !== exceptMenu) { menu.open = false; }
      });
    };

    const applyFilter = function () {
      const query = (searchInput ? searchInput.value : '').trim().toLowerCase();
      const selections = {};
      fieldConfigs.forEach(function (config) {
        selections[config.field] = Array.from(
          document.querySelectorAll(
            `.mutation-filter-menu[data-filter-group="${config.field}"] .mutation-filter-option:checked:not([disabled])`,
          ),
        ).map(function (input) { return input.value; });
      });

      collectRows().forEach(function (row) {
        const queryMatch = query.length === 0 || (row.textContent || '').toLowerCase().includes(query);
        const fieldMatch = fieldConfigs.every(function (config) {
          const selected = selections[config.field] || [];
          return selected.length === 0 || selected.includes((row.getAttribute(config.attr) || '').trim());
        });
        row.hidden = !(queryMatch && fieldMatch);
        if (row.hidden && opts.onRowHidden) { opts.onRowHidden(row); }
      });
    };

    buildFilterMenus();

    filterMenus.forEach(function (menu) {
      const summary = menu.querySelector('summary');
      if (!summary) { return; }
      summary.addEventListener('click', function (event) {
        event.preventDefault();
        const willOpen = !menu.open;
        closeAllMenus(menu);
        menu.open = willOpen;
      });
    });

    document.addEventListener('click', function (event) {
      if (!toolbar || toolbar.contains(event.target)) { return; }
      closeAllMenus();
    });

    if (toolbar) {
      toolbar.addEventListener('click', function (event) {
        if (!event.target.closest('.mutation-filter-menu')) { closeAllMenus(); }
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        closeAllMenus();
        applyFilter();
      });
    }

    if (resetButton) {
      resetButton.addEventListener('click', function () {
        closeAllMenus();
        if (searchInput) { searchInput.value = ''; }
        buildFilterMenus();
        applyFilter();
      });
    }

    if (downloadButton && opts.onDownload) {
      downloadButton.addEventListener('click', function () {
        closeAllMenus();
        opts.onDownload(collectRows, opts.table);
      });
    }

    applyFilter();
  }

  // ── Database Hits table ───────────────────────────────────────────────────
  const dbHitTable = document.querySelector('.db-hit-table:not(.sim-table)');
  if (dbHitTable) {
    const dbHitTbody = dbHitTable.querySelector('tbody');

    // Toggle comment expansion row when clicking an expandable db-hit row.
    dbHitTbody.querySelectorAll('.db-hit-row--expandable').forEach(function (row) {
      const toggleComment = function (event) {
        if (event && event.target && event.target.closest('a, button')) { return; }
        const rowId = row.getAttribute('data-comment-row');
        if (!rowId) { return; }
        const commentRow = document.getElementById(rowId);
        if (!commentRow) { return; }
        const isOpen = !commentRow.hidden;
        commentRow.hidden = isOpen;
        row.setAttribute('aria-expanded', String(!isOpen));
      };
      row.addEventListener('click', toggleComment);
      row.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') { return; }
        event.preventDefault();
        toggleComment(event);
      });
    });

    const dbHitSearchInput = document.getElementById('db-hit-search-input');
    createFacetedTable({
      table:          dbHitTable,
      rowSelector:    '.db-hit-row',
      fieldConfigs: [
        { field: 'db-hit-drug-class', attr: 'data-drug-class-value' },
        { field: 'db-hit-drug',       attr: 'data-drug-value' },
        { field: 'db-hit-freq',       attr: 'data-freq-value' },
      ],
      searchInput:    dbHitSearchInput,
      filterMenus:    Array.from(document.querySelectorAll('.mutation-filter-menu[data-filter-group^="db-hit-"]')),
      toolbar:        dbHitSearchInput && dbHitSearchInput.closest('[role="region"]'),
      resetButton:    document.querySelector('.db-hit-reset-button'),
      downloadButton: document.querySelector('.db-hit-download-button'),
      onRowHidden: function (row) {
        const id = row.getAttribute('data-comment-row');
        if (!id) { return; }
        const commentRow = document.getElementById(id);
        if (commentRow) {
          commentRow.hidden = true;
          row.setAttribute('aria-expanded', 'false');
        }
      },
      onDownload: function (collectRows, table) {
        const hasPubs = (table.querySelector('thead th:last-child')?.textContent || '').trim() === 'References';
        const hasDrugClass = !!table.querySelector('thead th.db-hit-drug-class-th');
        const headers = [];
        if (hasDrugClass) { headers.push('Drug class'); }
        headers.push('Drug', 'Mutations', 'Drug sensitivity data', 'Frequency classification', 'Source');
        if (hasPubs) { headers.push('References'); }
        const lines = [headers.join('\t')];

        collectRows().forEach(function (row) {
          if (row.hidden) { return; }
          const drugClass = hasDrugClass ? (row.querySelector('.db-hit-drug-class-cell')?.textContent || '').trim() : '';
          const drug = (row.querySelector('.db-hit-drug-cell')?.textContent || '').trim();
          const mutGroups = Array.from(row.querySelectorAll('.db-hit-mut-group')).map(function (g) {
            return g.textContent.replace(/\s+/g, ' ').trim();
          }).join('; ');
          const metrics = Array.from(row.querySelectorAll('.db-hit-metric')).map(function (m) {
            return m.textContent.replace(/\s+/g, ' ').trim();
          }).join('; ');
          const freq = (row.getAttribute('data-freq-value') || '').trim();
          const source = (row.querySelector('.db-hit-source-cell')?.textContent || '').trim();
          const fields = [];
          if (hasDrugClass) { fields.push(drugClass); }
          fields.push(drug, mutGroups, metrics, freq, source);
          if (hasPubs) { fields.push((row.getAttribute('data-pub-urls') || '').trim()); }
          lines.push(fields.map(function (v) { return v.replace(/\t/g, ' ').replace(/\n/g, ' '); }).join('\t'));
        });

        downloadTsv(lines, 'database_hits.tsv');
      },
    });
  }

  // ── Similarity to Database Entries table ─────────────────────────────────
  const simTable = document.querySelector('.sim-table');
  if (simTable) {
    const simSearchInput = document.getElementById('sim-search-input');
    createFacetedTable({
      table:          simTable,
      rowSelector:    '.sim-row',
      fieldConfigs: [
        { field: 'sim-drug-class', attr: 'data-sim-drug-class-value' },
        { field: 'sim-drug',       attr: 'data-sim-drug-value' },
        { field: 'sim-freq',       attr: 'data-sim-freq-value' },
      ],
      searchInput:    simSearchInput,
      filterMenus:    Array.from(document.querySelectorAll('.mutation-filter-menu[data-filter-group^="sim-"]')),
      toolbar:        simSearchInput && simSearchInput.closest('[role="region"]'),
      resetButton:    document.querySelector('.sim-reset-button'),
      downloadButton: document.querySelector('.sim-download-button'),
      onDownload: function (collectRows, table) {
        const hasPubs = (table.querySelector('thead th:last-child')?.textContent || '').trim() === 'References';
        const hasDrugClass = !!table.querySelector('thead th.sim-drug-class-th');
        const headers = [];
        if (hasDrugClass) { headers.push('Drug class'); }
        headers.push('Drug', 'Mutation', 'Known Rule', 'Similarity to entry', 'Drug sensitivity data', 'Frequency classification', 'Source');
        if (hasPubs) { headers.push('References'); }
        const lines = [headers.join('\t')];

        collectRows().forEach(function (row) {
          if (row.hidden) { return; }
          const drugClass = hasDrugClass ? (row.querySelector('.sim-drug-class-cell')?.textContent || '').trim() : '';
          const drug = (row.querySelector('.sim-drug-cell')?.textContent || '').trim();
          const mutation = (row.querySelector('.sim-mutation-cell')?.textContent || '').trim();
          const knownRule = (row.querySelector('.sim-rule-cell')?.textContent || '').trim();
          const similarity = (row.querySelector('.sim-badge')?.textContent || '').trim();
          const metrics = Array.from(row.querySelectorAll('.db-hit-metric')).map(function (m) {
            return m.textContent.replace(/\s+/g, ' ').trim();
          }).join('; ');
          const freq = (row.getAttribute('data-sim-freq-value') || '').trim();
          const source = (row.querySelector('.sim-source-cell')?.textContent || '').trim();
          const fields = [];
          if (hasDrugClass) { fields.push(drugClass); }
          fields.push(drug, mutation, knownRule, similarity, metrics, freq, source);
          if (hasPubs) { fields.push((row.getAttribute('data-pub-urls') || '').trim()); }
          lines.push(fields.map(function (v) { return v.replace(/\t/g, ' ').replace(/\n/g, ' '); }).join('\t'));
        });

        downloadTsv(lines, 'similarity_entries.tsv');
      },
    });
  }

  // ── Summary tab: Drug interpretation download ───────────────────────────
  const summaryDrugTable = document.querySelector('.drug-interp-table');
  const summaryDrugDownloadButton = document.querySelector('.summary-drug-download-button');
  if (summaryDrugTable && summaryDrugDownloadButton) {
    summaryDrugDownloadButton.addEventListener('click', function () {
      const lines = [];
      const headers = Array.from(summaryDrugTable.querySelectorAll('thead th')).map(function (th) {
        const headerClone = th.cloneNode(true);
        headerClone.querySelectorAll('.db-hit-freq-info').forEach(function (tooltip) {
          tooltip.remove();
        });
        return (headerClone.textContent || '').replace(/\s+/g, ' ').trim();
      });
      const hasGroupHeaders = !!summaryDrugTable.querySelector('.drug-group-header-row');
      if (hasGroupHeaders) {
        headers.unshift('Drug class');
      }
      lines.push(headers.join('\t'));

      let currentGroup = '';
      Array.from(summaryDrugTable.querySelectorAll('tbody tr')).forEach(function (row) {
        if (row.classList.contains('drug-group-header-row')) {
          currentGroup = (row.textContent || '').replace(/\s+/g, ' ').trim();
          return;
        }
        const values = Array.from(row.querySelectorAll('td')).map(function (cell) {
          return (cell.textContent || '').replace(/\s+/g, ' ').trim();
        });
        if (!values.length) {
          return;
        }
        const fields = hasGroupHeaders ? [currentGroup].concat(values) : values;
        lines.push(fields.map(function (value) {
          return value.replace(/\t/g, ' ').replace(/\n/g, ' ');
        }).join('\t'));
      });

      downloadTsv(lines, 'drug_interpretation.tsv');
    });
  }

  // ── Summary tab: narrative translation ──────────────────────────────
  document.querySelectorAll('.summary-text-box').forEach(function (box) {
    const buttons = Array.from(box.querySelectorAll('.summary-lang-btn'));
    const paragraphs = Array.from(box.querySelectorAll('.summary-text[data-lang]'));
    if (!buttons.length || !paragraphs.length) {
      return;
    }

    const translationCache = {};
    const enParagraph = paragraphs.find(function (p) { return p.dataset.lang === 'en'; });

    const loadTranslation = async function (lang) {
      const targetParagraph = paragraphs.find(function (p) { return p.dataset.lang === lang; });
      if (!enParagraph || !targetParagraph) {
        return;
      }
      if (translationCache[lang]) {
        targetParagraph.textContent = translationCache[lang];
        return;
      }
      const source = (enParagraph.textContent || '').trim();
      if (!source) {
        targetParagraph.textContent = 'No source text available for translation.';
        return;
      }
      targetParagraph.textContent = 'Translation loading\u2026';
      const endpoint = 'https://translate.googleapis.com/translate_a/single';
      const params = new URLSearchParams({ client: 'gtx', sl: 'en', tl: lang, dt: 't', q: source });
      try {
        const response = await fetch(endpoint + '?' + params.toString());
        if (!response.ok) {
          throw new Error('Translation request failed with status ' + response.status);
        }
        const payload = await response.json();
        const segments = Array.isArray(payload) && Array.isArray(payload[0]) ? payload[0] : [];
        const translated = segments
          .map(function (seg) { return Array.isArray(seg) ? seg[0] : ''; })
          .join('')
          .trim();
        if (!translated) {
          throw new Error('Translation response was empty');
        }
        translationCache[lang] = translated;
        targetParagraph.textContent = translated;
      } catch (_err) {
        targetParagraph.textContent = 'Automatic translation unavailable. Please retry later.';
      }
    };

    const setLanguage = async function (lang) {
      if (lang !== 'en') {
        await loadTranslation(lang);
      }
      buttons.forEach(function (btn) {
        btn.classList.toggle('active', btn.dataset.lang === lang);
      });
      paragraphs.forEach(function (p) {
        p.hidden = p.dataset.lang !== lang;
      });
      box.dataset.summaryLang = lang;
    };

    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        void setLanguage(btn.dataset.lang || 'en');
      });
    });

    void setLanguage(box.dataset.summaryLang || 'en');
  });
});
