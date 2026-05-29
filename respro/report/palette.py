"""
Shared report colour palette for mutation consequences and genome/feature track elements.
"""

from __future__ import annotations

import matplotlib.patches as mpatches

MUTATION_COLOURS = {
    'missense': '#f39c12',
    'synonymous': '#27ae60',
    'stop_gained': '#1d1e1f',
    'stop_loss': '#3e0c8d',
    'start_loss': '#9e821d',
    'frameshift': '#e74c3c',
    'insertion': '#16a085',
    'deletion': '#2980b9',
    'inframe_complex': '#831b71',
    'unknown': '#bdc3c7',
}

# Non-covered region overlay
NON_COVERED_COLOUR = '#6b7280'

# Genome overview feature track colours
CDS_HIGHLIGHTED_COLOUR = "#627BAA"
MATPEPTIDE_HIGHLIGHTED_COLOUR = "#398588"
FEATURE_INTRON_COLOUR = "#b2b2b2"  # muted blue-grey for intron / non-coding intervals within split genes
FEATURE_DEFAULT_COLOUR = '#d9dde3'
FEATURE_HIGHLIGHTED_EDGE = 'black'
FEATURE_DEFAULT_EDGE = "#a2a2a2"
FEATURE_BASELINE_COLOUR = 'dimgrey'

# Phenotype badge colours — key is the raw phenotype value
PHENOTYPE_COLOURS = {
    'resistant':      '#e74c3c',
    'intermediate':   '#f39c12',
    'sensitive':      '#27ae60',
    'contradictory':  "#334142",
    'unknown':        '#bdc3c7'
}

# Allele-frequency bin badge colours
AF_BIN_COLOURS = {
    'high':         '#e74c3c',
    'intermediate': '#f39c12',
    'low':          '#bdc3c7',
}

# Similarity badge colours
SIMILARITY_COLOURS = {
    'high':     '#e74c3c',
    'moderate': '#f39c12',
    'low':      '#bdc3c7',
}


def mutation_legend_patches(effects: set[str] | None = None) -> list[mpatches.Patch]:
    """
    Build matplotlib Patch legend handles for mutation consequences.

    :param effects: optional subset of effect names to include; defaults to all
    :return: list of Patch objects, one per mutation colour
    """
    selected = effects if effects is not None else set(MUTATION_COLOURS)
    return [
        mpatches.Patch(facecolor=colour, label=effect.capitalize().replace('_', ' '))
        for effect, colour in MUTATION_COLOURS.items()
        if effect in selected
    ]
