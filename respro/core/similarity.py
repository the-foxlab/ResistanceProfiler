"""
Amino acid substitution similarity scoring using BLOSUM62.
"""

from __future__ import annotations

import logging
from Bio.Align.substitution_matrices import load as _load_matrix

logger = logging.getLogger(__name__)

def classify_similarity(observed_aa: str, rule_aa: str) -> str:
    """
    Classify amino acid similarity based on BLOSUM62 score.

    Thresholds:
    - score >= 1  → 'high'     (biochemically similar substitution)
    - score >= 0  → 'moderate' (neutral substitution)
    - score < 0   → 'low'     (dissimilar substitution)

    :param observed_aa: observed alternate amino acid
    :param rule_aa: amino acid from the resistance rule
    :return: similarity class string
    """
    try:
        score = _load_matrix('BLOSUM62')[observed_aa.upper(), rule_aa.upper()]
    except (KeyError, IndexError):
        # Non-standard tokens (e.g. 'fsX', '*') are not in the matrix
        logger.debug('BLOSUM62 matrix does not contain %s/%s — defaulting to low', observed_aa, rule_aa)
        return 'low'
    if score >= 1:
        return 'high'
    if score >= 0:
        return 'moderate'
    return 'low'

