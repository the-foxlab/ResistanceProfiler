"""
Combined same-codon SNP inference — Fréchet lower-bound estimation and (planned)
BAM read-backed exact co-occurrence.

Hosts the two strategies for resolving multiple SNPs falling in one codon behind
one seam. The Fréchet path (no BAM) computes sharp probability bounds from the
marginal allele frequencies; the BAM path (planned) will measure exact
co-occurrence directly from aligned reads. :func:`annotate_variants` in
``respro.core.annotation`` is the orchestrator and delegates co-codon groups here.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass

import pysam

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.annotation import (
    _annotate_snp,
    _classify_snp_consequence,
    _is_snp,
    reverse_complement,
    translate_codon,
)
from respro.db.models import AnnotatedVariant, CodonState, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

# Import-time default for the Fréchet-bound acceptance tolerance (lower > eps).
# The annotation path threads the per-invocation value ([codon] frechet_epsilon)
# into _compute_codon_frechet_states via cfg; this alias is the fallback default
# and is kept for backward-compat with annotation.py's re-export and tests.
_FRECHET_EPS = CLI_CONFIG.codon.frechet_epsilon


@dataclass(frozen=True)
class CandidateCodon:
    """One candidate codon state in the cartesian product of per-position options.

    Built by :func:`enumerate_candidate_codon_states` from the VCF member SNPs.
    Shared by the Fréchet path (which computes bounds from marginal frequencies)
    and the BAM path (which counts observed molecules), so both strategies reason
    over an identical state space.

    :ivar alt_codon: the 3-base codon (coding orientation) with the chosen option
        at each variant-bearing position and the reference base elsewhere.
    :ivar alt_aa: translation of ``alt_codon``.
    :ivar member_indices: the flat-encoded member IDs carried by this candidate
        (``pos_idx * 100 + alt_idx`` for each carried ALT; the reference option
        at a position contributes nothing). Matches the encoding used in
        :class:`CodonState.member_indices`.
    :ivar choices: per variant-bearing position (ordered by position index), the
        ``(base, member_id)`` choice — ``member_id`` is ``-1`` for the reference
        option, otherwise ``pos_idx * 100 + alt_idx``.
    :ivar q_values: per variant-bearing position, the marginal frequency of the
        chosen option (an ALT frequency, or the residual reference frequency
        ``1 - sum(alt_freqs)``). Used by the Fréchet bounds; ignored by the BAM
        path (which measures frequencies directly).
    """

    alt_codon: str
    alt_aa: str
    member_indices: tuple[int, ...]
    choices: tuple[tuple[str, int], ...]
    q_values: tuple[float, ...]


@dataclass(frozen=True)
class BamCooccurrence:
    """Context for BAM read-backed codon co-occurrence resolution.

    Carries the open BAM handle and per-feature CDS-to-query coordinate maps
    needed to translate internal CDS codon positions into the query-reference
    coordinates the BAM is aligned against. Passed through
    :func:`annotate_variants` into :func:`_annotate_combined_snp_codon`, which
    switches from the Fréchet lower-bound path to the exact observed-frequency
    path when this context is present.

    :ivar bam: open ``pysam.AlignmentFile`` (caller manages lifecycle).
    :ivar contig: BAM contig name to fetch (the query reference name).
    :ivar cds_to_query_by_feature: ``{feature_name: {cds_pos: query_pos}}`` for
        each feature being annotated, built from the matched
        :class:`FeatureMatch` via ``_build_query_to_cds_map``. ``cds_pos`` is
        0-based relative to the feature's CDS start.
    :ivar min_mapq: minimum read mapping quality (from ``[codon]`` config).
    :ivar min_depth: minimum spanning-molecule count; below this the codon
        falls back to single events (no combined call).
    """

    bam: pysam.AlignmentFile
    contig: str
    cds_to_query_by_feature: dict[str, dict[int, int]]
    min_mapq: int
    min_depth: int


def enumerate_candidate_codon_states(
    variants_at_codon: list[dict],
    ref_codon: str,
) -> list[CandidateCodon]:
    """Build the cartesian product of {ref base, each ALT} at every variant position.

    For a codon with ``k`` distinct variant-bearing nucleotide positions, each
    position offers the reference base (at its residual frequency
    ``1 - sum(alt_freqs)``) plus each ALT base (at its marginal frequency). The
    cartesian product of these per-position option lists yields every candidate
    codon state. This is the **same** state space the Fréchet enumerator builds
    internally; factoring it out lets the BAM path (which counts observed
    molecules per candidate) reason over an identical set.

    :param variants_at_codon: list of ``{'codon_pos': int, 'alts': [(base, freq), ...]}``
        dicts, one per distinct variant-bearing position. ``codon_pos`` is 0-based
        within the codon; ``alts`` lists each ALT base (coding orientation) with its
        marginal allele frequency. The reference option is implicit.
    :param ref_codon: the internal reference codon (3 bases, coding orientation).
    :return: one :class:`CandidateCodon` per enumerated state, or an empty list
        when the input is not combinable (single variant position, positions out
        of range, invalid ref codon length, or no ALTs).
    """
    if len(ref_codon) != 3:
        return []

    ref_bases = list(ref_codon.upper())
    positions: list[int] = []
    # options_by_pos_idx: one entry per distinct position; each option is
    # (base, freq, member_id) where member_id identifies the ALT (or -1 for ref).
    options_by_pos_idx: list[list[tuple[str, float, int]]] = []
    seen_positions: dict[int, int] = {}  # codon_pos -> index into positions
    for v in variants_at_codon:
        cp = v['codon_pos']
        if not isinstance(cp, int) or cp < 0 or cp >= 3:
            return []
        if cp not in seen_positions:
            seen_positions[cp] = len(positions)
            positions.append(cp)
            options_by_pos_idx.append([])
        pos_idx = seen_positions[cp]
        for member_id, (alt_base, freq) in enumerate(v['alts']):
            options_by_pos_idx[pos_idx].append(
                (alt_base.upper(), float(freq), member_id)
            )

    if len(positions) < 2:
        return []

    # Add the implicit reference option (residual frequency) to each position.
    # member_id = -1 signals the reference base (not a variant member).
    for pos_idx, cp in enumerate(positions):
        alt_freq_sum = sum(freq for _, freq, _ in options_by_pos_idx[pos_idx])
        ref_freq = max(0.0, 1.0 - alt_freq_sum)
        ref_base = ref_bases[cp]
        options_by_pos_idx[pos_idx].append((ref_base, ref_freq, -1))

    candidates: list[CandidateCodon] = []
    for combo in itertools.product(*options_by_pos_idx):
        codon_bases = list(ref_bases)
        q_values: list[float] = []
        member_indices: list[int] = []
        choices: list[tuple[str, int]] = []
        for pos_idx, (base, freq, member_id) in enumerate(combo):
            cp = positions[pos_idx]
            codon_bases[cp] = base
            q_values.append(freq)
            # Flat-encode the member ID (pos_idx * 100 + alt_idx) so choices
            # and member_indices use the same encoding; -1 for the ref option.
            flat_id = pos_idx * 100 + member_id if member_id >= 0 else -1
            if member_id >= 0:
                member_indices.append(flat_id)
            choices.append((base, flat_id))

        alt_codon = ''.join(codon_bases)
        alt_aa = translate_codon(alt_codon)
        candidates.append(CandidateCodon(
            alt_codon=alt_codon,
            alt_aa=alt_aa,
            member_indices=tuple(member_indices),
            choices=tuple(choices),
            q_values=tuple(q_values),
        ))

    return candidates


def _compute_codon_frechet_states(
    variants_at_codon: list[dict],
    ref_codon: str,
    min_fraction: float,
    eps: float = _FRECHET_EPS,
) -> list[CodonState]:
    """
    Compute the sharp Fréchet intersection bounds for every candidate codon state.

    For a codon with ``k`` distinct variant-bearing nucleotide positions (k = 2 or 3),
    enumerate every possible exact codon state. Each position carries one or more
    ALT bases (the multiallelic case) plus the implicit reference option at the
    residual frequency ``1 - sum(freqs)``. The cartesian product of all per-position
    options yields every candidate codon. For each state compute the Fréchet bounds:

    - ``q_i`` = the frequency of the option chosen at position ``i`` (an ALT
      frequency, or the residual reference frequency).
    - ``lower = max(0.0, sum(q) - (k - 1))`` — minimum guaranteed co-occurrence.
    - ``upper = min(q)`` — maximum possible co-occurrence.
    - ``forced_fraction = lower / upper`` if ``upper > 0`` else ``0.0`` — the minimum
      fraction of the rarest required condition forced into this codon state.

    A state is ``accepted`` when ``lower > eps`` and ``forced_fraction >= min_fraction``.

    This is a conservative codon-level inference based on the minimum guaranteed
    intersection of the corresponding viral subpopulations. It does NOT infer
    physical phase and is NOT a probability that variants are linked. The
    ``min_fraction`` threshold is an explicit conservative interpretation policy:
    passing means the guaranteed portion is at least twice the potentially unshared
    portion (at 2/3).

    Multiallelic positions (≥2 ALTs at the same codon position, e.g. VCF
    multiallelic sites or IUPAC-reference) are handled by modelling the position
    as a multi-way choice: the reference base at its residual frequency plus each
    ALT at its marginal frequency. The mutual exclusivity of the ALTs at one
    position is implicit — each candidate codon state picks exactly one option per
    position.

    :param variants_at_codon: list of ``{'codon_pos': int, 'alts': [(base, freq), ...]}``
        dicts, one per distinct variant-bearing position. ``codon_pos`` is 0-based
        within the codon; ``alts`` lists each ALT base (coding orientation) with its
        marginal allele frequency. The reference option is implicit.
    :param ref_codon: the internal reference codon (3 bases, coding orientation).
    :param min_fraction: acceptance threshold for ``forced_fraction`` (e.g. 2/3),
        loaded from ``defaults.toml`` ([codon] section).
    :param eps: numerical tolerance; states with ``lower <= eps`` are rejected.
    :return: list of :class:`CodonState` for every enumerated candidate state
        (accepted or not), or an empty list when the input is not combinable
        (single variant position, positions out of range, or no ALTs).
    """
    candidates = enumerate_candidate_codon_states(variants_at_codon, ref_codon)
    if not candidates:
        return []

    k = len(candidates[0].q_values)  # number of distinct variant positions
    accepted_states: list[CodonState] = []
    for cand in candidates:
        q_values = list(cand.q_values)
        lower = max(0.0, sum(q_values) - (k - 1))
        upper = min(q_values)
        forced_fraction = lower / upper if upper > 0 else 0.0
        accepted = lower > eps and forced_fraction >= min_fraction - eps

        accepted_states.append(CodonState(
            alt_codon=cand.alt_codon,
            alt_aa=cand.alt_aa,
            lower=lower,
            upper=upper,
            forced_fraction=forced_fraction,
            accepted=accepted,
            member_indices=cand.member_indices,
        ))

    return accepted_states


def compute_codon_cooccurrence(
    bam: pysam.AlignmentFile,
    contig: str,
    codon_query_positions: list[tuple[int, int]],
    candidate_states: list[CandidateCodon],
    strand: str,
    min_mapq: int,
    min_depth: int,
) -> list[CodonState]:
    """Measure exact same-codon SNP co-occurrence from aligned reads.

    For a codon with multiple variant-bearing query positions, count how many
    spanning molecules realise each candidate codon state (from
    :func:`enumerate_candidate_codon_states`). A molecule is a single-end read
    or a paired-end pair deduplicated by ``query_name``. Returns one
    :class:`CodonState` per candidate with a non-zero count, with
    ``lower = upper = count / spanning_molecules`` and
    ``forced_fraction = 1.0`` (an observed point estimate, not a bound).

    Algorithm:
      1. ``bam.fetch(contig, start, end)`` over the codon's query span.
      2. Filter reads: ``mapping_quality >= min_mapq`` AND the read spans all
         variant query positions
         (``reference_start <= min_pos`` and ``reference_end >= max_pos + 1``).
      3. For each spanning read, read the base at each variant query position
         via ``get_aligned_pairs(matches_only=True)``; reverse-complement when
         ``strand == '-'``.
      4. Paired-end dedup: group spanning reads by ``query_name``. A group of 2
         (paired mates) that agree on the codon counts as **one** molecule; if
         they disagree the molecule is **rejected** (counted on neither). A
         group of 1 (single-end or one mate non-spanning) counts once.
      5. Match each counted molecule's codon against ``candidate_states``;
         tally per candidate. Codons matching no candidate are "other" and
         produce no ``CodonState``.
      6. If ``spanning_molecules < min_depth``, return ``[]`` (caller falls
         back to single events).

    Pure w.r.t. the BAM handle (caller opens/closes).

    :param bam: open ``pysam.AlignmentFile`` (caller manages lifecycle).
    :param contig: BAM contig name to fetch.
    :param codon_query_positions: list of ``(codon_pos, query_pos)`` for each
        variant-bearing codon position. ``codon_pos`` is 0-based within the
        codon; ``query_pos`` is the 0-based position in the query reference the
        BAM is aligned against.
    :param candidate_states: candidates from :func:`enumerate_candidate_codon_states`.
    :param strand: feature strand ('+' or '-'); bases are reverse-complemented
        for '-' so codons are in coding orientation.
    :param min_mapq: minimum mapping quality; reads below this are excluded.
    :param min_depth: minimum spanning-molecule count; below this returns ``[]``.
    :return: one :class:`CodonState` per candidate with ``count > 0``, or ``[]``
        when spanning evidence is below ``min_depth``.
    """
    if not codon_query_positions or not candidate_states:
        return []

    # Build a quick lookup: codon_pos -> query_pos.
    variant_query_positions = [qp for _, qp in codon_query_positions]
    min_qpos = min(variant_query_positions)
    max_qpos = max(variant_query_positions)

    # Map each candidate by its tuple of (codon_pos -> base) choices so we can
    # match a read's per-position bases quickly. Only the variant-bearing
    # positions matter for matching (non-variant positions are always ref).
    candidate_keys: dict[tuple[tuple[int, str], ...], CandidateCodon] = {}
    for cand in candidate_states:
        # cand.choices is ordered by position index; each entry is (base, member_id).
        # Reconstruct (codon_pos, base) for the variant positions only.
        # The choices tuple aligns to the positions in the order they were
        # enumerated; we need to map back to codon_pos. Use the candidate's
        # member_indices to identify carried ALTs, but simpler: the choices
        # correspond position-by-position to the codon_query_positions order.
        # Build the key from (codon_pos, base) for each variant position.
        key = tuple(
            (codon_query_positions[i][0], cand.choices[i][0])
            for i in range(len(codon_query_positions))
        )
        candidate_keys[key] = cand

    # Collect spanning reads: query_name -> list of codon-base-tuples.
    # Each codon-base-tuple is ((codon_pos, base), ...) in codon_query_positions
    # order, in coding orientation.
    molecules: dict[str, list[tuple[tuple[int, str], ...]]] = {}
    for read in bam.fetch(contig, min_qpos, max_qpos + 1):
        # Only primary alignments are assessed. Secondary (0x100) and
        # supplementary (0x800) records share a query_name with the primary but
        # represent an alternative mapping of the same read or a chimeric split
        # of one molecule — counting them would either double-count a single
        # molecule or, for a supplementary that disagrees with its primary on
        # the codon, wrongly reject the primary via the paired-end agreement
        # rule. Filtering them here keeps each query_name group to at most the
        # two mates of a proper pair, so the dedup branches below are
        # well-defined.
        if read.is_secondary or read.is_supplementary:
            continue
        # Skip reads that failed platform/vendor quality checks (QCFAIL,
        # 0x200) and PCR/optical duplicates (0x400). These do not represent
        # independent molecules and would inflate the co-occurrence frequency.
        if read.is_qcfail or read.is_duplicate:
            continue
        if (read.mapping_quality or 0) < min_mapq:
            continue
        # Spanning check: read must cover all variant query positions.
        if read.reference_start > min_qpos:
            continue
        ref_end = read.reference_end
        if ref_end is None or ref_end < max_qpos + 1:
            continue

        # Build ref_pos -> query_pos map for this read's matched bases.
        ref_to_query: dict[int, int] = {}
        for query_pos, ref_pos in read.get_aligned_pairs(matches_only=True):
            if ref_pos is not None and query_pos is not None:
                ref_to_query[ref_pos] = query_pos

        # Read the base at each variant query position.
        bases: list[tuple[int, str]] = []
        ok = True
        for codon_pos, qpos in codon_query_positions:
            qidx = ref_to_query.get(qpos)
            if qidx is None:
                # Read has a gap/deletion at this position — not a clean match.
                ok = False
                break
            base = (read.query_sequence or '')[qidx].upper()
            if strand == '-':
                base = reverse_complement(base)
            bases.append((codon_pos, base))
        if not ok:
            continue

        key = tuple(bases)
        qname = read.query_name or ''
        molecules.setdefault(qname, []).append(key)

    # Deduplicate paired-end: one molecule per query_name.
    counted: list[tuple[tuple[int, str], ...]] = []
    for qname, codons in molecules.items():
        if len(codons) == 1:
            # Single-end or only one mate spans.
            counted.append(codons[0])
        elif len(codons) == 2:
            # Paired mates: count once if they agree, reject if they disagree.
            if codons[0] == codons[1]:
                counted.append(codons[0])
            # else: disagree → reject (count neither)
        else:
            # More than 2 primary records with the same qname spanning the codon
            # should not occur in a well-formed BAM (a proper pair has at most
            # two primary records, one per mate; secondary/supplementary records
            # are filtered above). Count the first only as a defensive fallback
            # rather than silently inflating the denominator.
            counted.append(codons[0])

    spanning_molecules = len(counted)
    if spanning_molecules < min_depth:
        return []

    # Tally per candidate.
    counts: dict[CandidateCodon, int] = {}
    for key in counted:
        matched: CandidateCodon | None = candidate_keys.get(key)
        if matched is None:
            continue  # "other" — produces no CodonState
        counts[matched] = counts.get(matched, 0) + 1

    states: list[CodonState] = []
    for cand, count in counts.items():
        freq = count / spanning_molecules
        states.append(CodonState(
            alt_codon=cand.alt_codon,
            alt_aa=cand.alt_aa,
            lower=freq,
            upper=freq,
            forced_fraction=1.0,
            accepted=True,
            member_indices=cand.member_indices,
        ))

    return states


def _plan_combined_snp_groups(
    variants: list[VariantCall],
    features: list[FeatureRecord],
) -> dict[tuple[int, int], list[int]]:
    """
    Return codon groups that should be evaluated as combined SNP events.

    A codon with two or more SNPs at distinct codon positions is a candidate.
    The Fréchet test in :func:`_annotate_combined_snp_codon` decides whether any
    combined state is actually emitted; grouping here only collects co-codon SNPs.

    :param variants: input variant list
    :param features: feature records
    :return: {(feature_id, codon_idx): [variant_index, ...]}
    """
    grouped: dict[tuple[int, int], list[int]] = {}
    for idx, var in enumerate(variants):
        if not _is_snp(var.ref, var.alt):
            continue
        for feature in features:
            if not feature.contains(var.pos):
                continue
            codon_idx = feature.codon_index(var.pos)
            if codon_idx is None or codon_idx < 0:
                continue
            key = (feature.id, codon_idx)
            grouped.setdefault(key, []).append(idx)

    planned: dict[tuple[int, int], list[int]] = {}
    for key, member_indices in grouped.items():
        if len(member_indices) < 2:
            continue
        planned[key] = sorted(member_indices)
    return planned


def _annotate_combined_snp_codon(
    variants: list[VariantCall],
    feature: FeatureRecord,
    bam_cooccurrence: BamCooccurrence | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> list[AnnotatedVariant]:
    """
    Annotate multiple SNPs in one codon as per-SNP combined-state events.

    For each member SNP, emit one :class:`AnnotatedVariant` carrying its own
    ``allele_freq``/``ref``/``alt``, a single-exchange ``alt_codon``/``alt_aa``
    (codon with only that SNP applied), and a ``combined_states`` list of the
    accepted codon states that include this member.

    Two resolution strategies:

    - **BAM path** (``bam_cooccurrence`` is not None): candidate codon states are
      enumerated from the VCF member SNPs (same state space as the Fréchet path),
      then exact co-occurrence frequencies are measured directly from the aligned
      reads via :func:`compute_codon_cooccurrence`. ``freq_method='observed'``.
      When the spanning-molecule count is below ``min_depth`` (thin evidence), the
      codon falls back to single events (no combined call, ``freq_method='observed'``)
      — we looked for co-occurrence in the raw data and found none.
    - **Fréchet path** (``bam_cooccurrence`` is None): conservative lower-bound
      inference from marginal allele frequencies via
      :func:`_compute_codon_frechet_states`. ``freq_method='estimated'``.

    Forced-overlap exception (Fréchet path): when a member's single-exchange state
    is Fréchet-rejected (``lower <= eps``) and there is an accepted all-carried
    state with ``forced_fraction == 1.0`` (the Fréchet overlap is 100 %, i.e. a
    co-member is at frequency 1.0), the member's single ``alt_codon``/``alt_aa``
    is replaced by that forced all-carried state. The promoted state is dropped
    from ``combined_states`` to avoid showing the same effect twice. On the BAM
    path every observed state has ``forced_fraction == 1.0``, so a member whose
    single-exchange codon was not observed is promoted to the strongest observed
    state that carries it.

    Falls back to plain single-SNP annotation (no ``combined_states``) when the
    resolver returns no states (BAM thin evidence, multiallelic same-position, or
    no accepted state for any member).

    :param variants: SNPs from the same codon (same feature)
    :param feature: feature containing the codon
    :param bam_cooccurrence: BAM context for exact observed co-occurrence; when
        None the Fréchet lower-bound path is used.
    :return: one annotation per member SNP (combined), or per-member single-SNP
        annotations on fallback
    """
    if not variants:
        raise ValueError('Combined SNP annotation requires at least one variant')

    seq_cds = feature.nt_sequence.upper()
    anchor = sorted(variants, key=lambda v: v.pos)[0]
    codon_idx = feature.codon_index(anchor.pos)
    if codon_idx is None:
        raise ValueError(
            f'Combined SNP annotation requires coding codon index for feature {feature.name!r} '
            f'at genomic position {anchor.pos}'
        )
    codon_start = feature.codon_start + (codon_idx * 3)
    internal_codon = seq_cds[codon_start:codon_start + 3]
    ref_aa = translate_codon(internal_codon)

    # Resolve each member's coding-orientation ALT base and codon position.
    # Group ALTs by codon position so multiallelic positions are modelled as
    # multiple options at one position (not rejected).
    members: list[tuple[VariantCall, int, str, int]] = []  # (var, codon_pos, alt_base, member_index)
    alts_by_pos: dict[int, list[tuple[str, float, int]]] = {}  # codon_pos -> [(alt_base, freq, alt_idx)]
    pos_order: list[int] = []
    for var in sorted(variants, key=lambda v: v.pos):
        codon_pos = feature.codon_position_in_codon(var.pos)
        if codon_pos is None:
            raise ValueError(
                f'Combined SNP annotation requires coding codon position for feature {feature.name!r} '
                f'at genomic position {var.pos}'
            )
        alt_base = reverse_complement(var.alt) if feature.strand == '-' else var.alt.upper()
        if codon_pos not in alts_by_pos:
            alts_by_pos[codon_pos] = []
            pos_order.append(codon_pos)
        alt_idx = len(alts_by_pos[codon_pos])
        alts_by_pos[codon_pos].append((alt_base, float(var.allele_freq), alt_idx))
        # Encode the member index as pos_idx * 100 + alt_idx (matches the helper's
        # encoding so member_indices in CodonState can be matched back).
        pos_idx = pos_order.index(codon_pos)
        member_index = pos_idx * 100 + alt_idx
        members.append((var, codon_pos, alt_base, member_index))

    # Build the input specs: one entry per distinct position, with a list of
    # (alt_base, freq) options (the multiallelic case has >1 option).
    member_specs = [
        {'codon_pos': cp, 'alts': [(ab, f) for ab, f, _ in alts_by_pos[cp]]}
        for cp in pos_order
    ]

    # Resolve codon states: BAM observed path or Fréchet estimated path.
    freq_method: str
    if bam_cooccurrence is not None:
        freq_method = 'observed'
        candidates = enumerate_candidate_codon_states(member_specs, internal_codon)
        cds_to_query = bam_cooccurrence.cds_to_query_by_feature.get(feature.name, {})
        # Translate each variant-bearing codon position's CDS position to a
        # query position via the feature's cds_to_query map.
        codon_query_positions: list[tuple[int, int]] = []
        for cp in pos_order:
            cds_pos = codon_start + cp
            qpos = cds_to_query.get(cds_pos)
            if qpos is None:
                # The codon position is not covered by the alignment (a gap in
                # the query-to-CDS map). Cannot measure co-occurrence → fall
                # back to single events.
                states = []
                break
            codon_query_positions.append((cp, qpos))
        else:
            states = compute_codon_cooccurrence(
                bam=bam_cooccurrence.bam,
                contig=bam_cooccurrence.contig,
                codon_query_positions=codon_query_positions,
                candidate_states=candidates,
                strand=feature.strand,
                min_mapq=bam_cooccurrence.min_mapq,
                min_depth=bam_cooccurrence.min_depth,
            )
    else:
        freq_method = 'estimated'
        min_fraction = cfg.codon.min_cooccurrence_codon_fraction
        states = _compute_codon_frechet_states(
            member_specs, internal_codon, min_fraction, eps=cfg.codon.frechet_epsilon,
        )

    if not states:
        # Uncombinable input, or BAM thin evidence (spanning molecules < min_depth).
        # Single-event fallback is always 'observed' (no combined estimate).
        return _combined_fallback_single_snp(variants, feature)

    accepted = [s for s in states if s.accepted]
    # The all-ref candidate (member_indices == ()) is always accepted when the
    # marginal ALT frequencies are low (its lower = 1 - sum(alt_freqs), and
    # forced_fraction >= min_fraction). That does NOT justify a combined call —
    # a combined event requires at least one accepted state that *carries* a
    # member ALT. When none does, fall back to single-SNP annotations.
    if not any(s.accepted and s.member_indices for s in states):
        # No accepted state carries any member: single-SNP fallback.
        return _combined_fallback_single_snp(variants, feature)

    # Forced-overlap promotion state per member: the accepted state with
    # ``forced_fraction == 1.0`` that carries this member and the most co-members.
    # For biallelic codons (one ALT per position) this is the unique all-carried
    # state (every member applied). For multiallelic positions a state cannot
    # carry two ALTs at the same position (they are mutually exclusive), so the
    # "all-carried" concept is per-member: the state carrying this member's ALT
    # plus one ALT at every other position. We pick the accepted forced state
    # with the longest member_indices (maximal co-carried set) that includes
    # this member — generalising the biallelic all-carried lookup, which matched
    # by ``len(member_indices) == len(members)`` and silently failed for
    # multiallelic codons (len(members) > len(positions)).
    forced_states: dict[int, CodonState | None] = {}
    eps = cfg.codon.frechet_epsilon
    for _, _, _, member_index in members:
        forced_candidates = [
            s for s in accepted
            if member_index in s.member_indices
            and abs(s.forced_fraction - 1.0) <= eps
        ]
        # Pick the forced state carrying the most co-members (maximal co-carried
        # set); break ties by the highest lower bound so a freq-1.0 co-member
        # promotes to the strongest guaranteed state.
        forced_states[member_index] = max(
            forced_candidates, key=lambda s: (len(s.member_indices), s.lower),
        ) if forced_candidates else None

    annotations: list[AnnotatedVariant] = []
    for var, codon_pos, alt_base, member_index in members:
        # Single-exchange codon: apply only this member's SNP.
        single_bases = list(internal_codon)
        single_bases[codon_pos] = alt_base
        single_codon = ''.join(single_bases)
        single_aa = translate_codon(single_codon)

        # Check whether this member's single-exchange state is Fréchet-rejected.
        single_state = next(
            (s for s in states
             if len(s.member_indices) == 1 and s.member_indices[0] == member_index),
            None,
        )
        single_rejected = single_state is None or single_state.lower <= eps

        # Forced-overlap exception: replace single with the per-member forced state.
        forced_state = forced_states.get(member_index)
        promoted = False
        if single_rejected and forced_state is not None:
            single_codon = forced_state.alt_codon
            single_aa = forced_state.alt_aa
            promoted = True

        consequence = _classify_snp_consequence(ref_aa, single_aa, codon_idx)

        # combined_states: accepted states that include this member, deduped by
        # alt_aa with lower summed. Drop the promoted forced state (it is now
        # the single) to avoid showing the same effect twice.
        member_states = [
            s for s in accepted
            if member_index in s.member_indices
            and not (promoted and s is forced_state)
        ]
        combined_states = _dedupe_states_by_aa(member_states)

        # single_exchange_aa_freq: the amino-acid frequency of the single-exchange
        # codon shown in this row (the Fréchet lower bound on the population
        # share of that exact codon). This is distinct from the nucleotide
        # frequency (variant.allele_freq): when promoted, the single IS the
        # forced state, so use its lower; otherwise use the solo state's
        # own Fréchet lower (0 when the single-exchange amino acid is
        # Fréchet-impossible, i.e. guaranteed absent despite a high nucleotide
        # frequency).
        if promoted and forced_state is not None:
            single_exchange_aa_freq = forced_state.lower
        elif single_state is not None:
            single_exchange_aa_freq = max(0.0, single_state.lower)
        else:
            single_exchange_aa_freq = 0.0

        annotations.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=internal_codon,
            alt_codon=single_codon,
            ref_aa=ref_aa,
            alt_aa=single_aa,
            consequence=consequence,
            is_combined_codon_event=True,
            combined_member_count=len(members),
            combined_states=combined_states,
            single_exchange_aa_freq=single_exchange_aa_freq,
            freq_method=freq_method,
        ))

    return annotations


def _dedupe_states_by_aa(states: list[CodonState]) -> list[CodonState]:
    """Deduplicate CodonStates by alt_aa, summing lower for duplicates."""
    by_aa: dict[str, CodonState] = {}
    for s in states:
        if s.alt_aa in by_aa:
            prev = by_aa[s.alt_aa]
            by_aa[s.alt_aa] = CodonState(
                alt_codon=prev.alt_codon,
                alt_aa=prev.alt_aa,
                lower=prev.lower + s.lower,
                upper=max(prev.upper, s.upper),
                forced_fraction=max(prev.forced_fraction, s.forced_fraction),
                accepted=True,
                member_indices=prev.member_indices,
            )
        else:
            by_aa[s.alt_aa] = s
    return list(by_aa.values())


def _combined_fallback_single_snp(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """Emit plain single-SNP annotations for each member (no combined_states)."""
    seq_cds = feature.nt_sequence.upper()
    cds_codons = [list(seq_cds[i:i + 3]) for i in range(feature.codon_start, len(seq_cds), 3)]
    annotations: list[AnnotatedVariant] = []
    for var in sorted(variants, key=lambda v: v.pos):
        cds_pos = feature.genomic_to_cds_position(var.pos)
        if cds_pos is None:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        coding_pos = cds_pos - feature.codon_start
        if coding_pos < 0:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        mut_codon_idx = coding_pos // 3
        frame_offset = coding_pos % 3
        if mut_codon_idx >= len(cds_codons):
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        mut = reverse_complement(var.alt) if feature.strand == '-' else var.alt
        ann = _annotate_snp(var, feature, cds_codons, mut_codon_idx, frame_offset, mut)
        annotations.append(ann)
    return annotations
