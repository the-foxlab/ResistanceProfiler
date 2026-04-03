"""
Amino acid substitution similarity scoring using BLOSUM62.
"""

from __future__ import annotations

from Bio.Align.substitution_matrices import load as _load_matrix

# Load BLOSUM62 once at module level (standard bioinformatics convention).
_BLOSUM62 = _load_matrix('BLOSUM62')


def blosum62_score(aa1: str, aa2: str) -> float:
    """
    Return the BLOSUM62 substitution score between two amino acids.

    Returns 0.0 for unknown or non-standard residues.

    :param aa1: single-letter amino acid
    :param aa2: single-letter amino acid
    :return: BLOSUM62 score
    """
    try:
        return float(_BLOSUM62[aa1.upper(), aa2.upper()])
    except (KeyError, IndexError):
        return 0.0


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
    score = blosum62_score(observed_aa, rule_aa)
    if score >= 1:
        return 'high'
    if score >= 0:
        return 'moderate'
    return 'low'

