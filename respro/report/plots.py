"""
Publication-ready plots — lollipop and gene track visualizations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from respro.db.models import GeneRecord
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)

# Colour palette
_COLOURS = {
    'missense': '#e74c3c',
    'synonymous': '#95a5a6',
    'nonsense': '#2c3e50',
    'stop_loss': '#8e44ad',
    'unknown': '#bdc3c7',
    'resistance_hit': '#e67e22',
}


def lollipop_plot(
    result: ProfilingResult,
    genes: list[GeneRecord],
    output_path: Path,
    fmt: str = 'svg',
) -> Path:
    """
    Create a lollipop-style mutation plot along the genome.

    :param result: profiling result with annotated variants
    :param genes: gene records for drawing the gene track
    :param output_path: file path for the saved figure
    :param fmt: output format (svg, pdf, png)
    :return: path to saved figure
    """
    output_path = Path(output_path)
    cds = result.cds_annotations
    if not cds:
        logger.warning('No CDS variants to plot')
        return output_path

    fig, (ax_lollipop, ax_gene) = plt.subplots(
        2, 1, figsize=(14, 5), height_ratios=[3, 1],
        sharex=True, layout='constrained',
    )

    # ----- Lollipop panel -----
    for ann in cds:
        x = ann.variant.pos + 1
        y = ann.variant.allele_freq
        colour = _COLOURS.get('resistance_hit' if ann.is_resistance_hit else ann.consequence, '#bdc3c7')
        ax_lollipop.vlines(x, 0, y, colors=colour, linewidth=0.8, alpha=0.7)
        ax_lollipop.scatter(x, y, color=colour, s=30, zorder=3, edgecolors='white', linewidths=0.4)

        if ann.is_resistance_hit:
            label = f'{ann.ref_aa}{ann.codon_pos + 1}{ann.alt_aa}'
            ax_lollipop.annotate(
                label, (x, y),
                textcoords='offset points', xytext=(4, 6),
                fontsize=7, color=colour, fontweight='bold',
            )

    ax_lollipop.set_ylabel('Allele frequency')
    ax_lollipop.set_ylim(-0.02, 1.05)
    ax_lollipop.set_title(
        f'{result.project_name} — {result.sample_name or result.vcf_path}',
        fontsize=11, fontweight='bold',
    )

    # Legend
    handles = [
        mpatches.Patch(color=_COLOURS['resistance_hit'], label='Resistance hit'),
        mpatches.Patch(color=_COLOURS['missense'], label='Missense'),
        mpatches.Patch(color=_COLOURS['synonymous'], label='Synonymous'),
        mpatches.Patch(color=_COLOURS['nonsense'], label='Nonsense'),
    ]
    ax_lollipop.legend(handles=handles, loc='upper right', fontsize=7, framealpha=0.8)

    # ----- Gene track panel -----
    gene_colours = plt.cm.Set2(np.linspace(0, 1, max(len(genes), 1)))
    for i, gene in enumerate(genes):
        colour = gene_colours[i % len(gene_colours)]
        gene_left = gene.start + 1
        gene_width = gene.end - gene.start
        ax_gene.barh(
            0, gene_width, left=gene_left,
            height=0.5, color=colour, edgecolor='black', linewidth=0.5,
        )
        mid = gene_left + (gene_width / 2)
        ax_gene.text(mid, 0, gene.name, ha='center', va='center', fontsize=8, fontweight='bold')

    ax_gene.set_ylim(-0.5, 0.5)
    ax_gene.set_xlabel('Genomic position')
    ax_gene.set_yticks([])
    ax_gene.spines['top'].set_visible(False)
    ax_gene.spines['left'].set_visible(False)
    ax_gene.spines['right'].set_visible(False)

    fig.savefig(output_path, format=fmt, dpi=150, bbox_inches='tight')
    plt.close(fig)

    logger.info('Lollipop plot saved to %s', output_path)
    return output_path




