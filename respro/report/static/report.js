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
      openModal(plotModal);
    });
    plotCloseButton.addEventListener('click', function () {
      closeModal(plotModal);
    });
    plotBackdrop.addEventListener('click', function () {
      closeModal(plotModal);
    });
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

  const mutationTable = document.querySelector('.mutation-table');
  if (!mutationTable) {
    return;
  }

  const mutationTbody = mutationTable.querySelector('tbody');
  const mutationSortButton = mutationTable.querySelector('.mutation-sort-button');
  const mutationSearchInput = document.getElementById('mutation-search-input');
  const mutationToolbar = document.querySelector('.mutation-toolbar');
  const mutationFilterMenus = Array.from(document.querySelectorAll('.mutation-filter-menu'));
  const mutationResetButton = document.querySelector('.mutation-reset-button');
  const mutationDownloadButton = document.querySelector('.mutation-download-button');

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
        values.forEach(function (value) {
          const label = document.createElement('label');
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.className = 'mutation-filter-option';
          input.value = value;
          input.checked = true;
          input.dataset.field = config.field;

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
        applyMutationFilter();
      });
    }

    if (mutationDownloadButton) {
      mutationDownloadButton.addEventListener('click', function () {
        closeAllMutationFilterMenus();

        const headers = ['Feature', 'Nt change', 'AA change', 'Consequence', 'Allele freq', 'In database'];
        const lines = [headers.join('\t')];

        collectMutationRowPairs().forEach(function (pair) {
          const row = pair.row;
          if (row.hidden) {
            return;
          }

          const feature = (row.children[0]?.textContent || '').trim();
          const ntChange = (row.children[1]?.textContent || '').trim();
          const aaChange = (row.children[2]?.textContent || '').trim();
          const consequence = (row.children[3]?.textContent || '').trim();
          const alleleFreq = (row.children[4]?.textContent || '').trim();
          const inDatabase = ((row.getAttribute('data-database-values') || 'None')
            .split('|')
            .map(function (value) {
              return value.trim();
            })
            .filter(Boolean)
            .join('; '));

          const fields = [feature, ntChange, aaChange, consequence, alleleFreq, inDatabase]
            .map(function (value) {
              return value.replace(/\t/g, ' ').replace(/\n/g, ' ');
            });
          lines.push(fields.join('\t'));
        });

        const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
        const blobUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = 'mutations.filtered.tsv';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(blobUrl);
      });
    }

    applyMutationFilter();
  }
});
