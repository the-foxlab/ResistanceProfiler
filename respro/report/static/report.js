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
    };

    applyMutationSort('asc');

    if (mutationSortButton) {
      mutationSortButton.addEventListener('click', function () {
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
  }
});
