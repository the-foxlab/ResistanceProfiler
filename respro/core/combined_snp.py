"""Same-codon multi-SNP inference — Fréchet lower bounds and BAM read-backed co-occurrence."""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import pysam

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.codons import (
    annotate_snp,
    classify_snp_consequence,
    is_snp,
    reverse_complement,
    translate_codon,
)
from respro.core.frechet import DEFAULT_FRECHET_EPS, frechet_and_bound, frechet_factor_bounds
from respro.db.models import AnnotatedVariant, CodonState, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

_FRECHET_EPS = DEFAULT_FRECHET_EPS
_REF_MEMBER_ID = -1


def _encode_member_id(pos_idx: int, alt_idx: int) -> int:
    """Flat-encode ``(pos_idx, alt_idx)`` into a single int stored on ``CodonState.member_indices``."""
    return pos_idx * 100 + alt_idx


@dataclass(frozen=True)
class CandidateCodon:
    """One candidate codon state in the cartesian product of per-position {ref, ALTs} options."""

    alt_codon: str
    alt_aa: str
    member_indices: tuple[int, ...]
    choices: tuple[tuple[str, int], ...]
    q_values: tuple[float, ...]


@dataclass(frozen=True)
class BamCooccurrence:
    """Open BAM handle and per-feature CDS-to-query coordinate maps for exact codon counting."""

    bam: pysam.AlignmentFile
    contig: str
    cds_to_query_by_feature: dict[str, dict[int, int]]
    min_mapq: int
    min_depth: int
    match_strand_by_feature: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _LinkedFactor:
    """One multi-position phase factor driven by a single linking VCF record."""

    positions: tuple[int, ...]
    haplotypes: tuple[tuple[float, dict[int, frozenset[int]]], ...]
    residual_mass: float
    claimed_by_pos: dict[int, dict[int, float]]
    opt_freqs_by_pos: dict[int, dict[int, float]]


def enumerate_candidate_codon_states(
    variants_at_codon: list[dict],
    ref_codon: str,
) -> list[CandidateCodon]:
    """Enumerate the cartesian product of {ref, ALT_1, ...} across all variant positions of a codon."""
    if len(ref_codon) != 3:
        return []

    ref_bases = list(ref_codon.upper())
    positions: list[int] = []
    options_by_pos: list[list[tuple[str, float, int]]] = []
    seen: dict[int, int] = {}

    for spec in variants_at_codon:
        cp = spec['codon_pos']
        if not isinstance(cp, int) or cp < 0 or cp >= 3:
            return []
        if cp not in seen:
            seen[cp] = len(positions)
            positions.append(cp)
            options_by_pos.append([])
        pos_idx = seen[cp]
        for alt_idx, (alt_base, freq) in enumerate(spec['alts']):
            options_by_pos[pos_idx].append((alt_base.upper(), float(freq), alt_idx))

    if len(positions) < 2:
        return []

    # Append implicit reference option at its residual marginal frequency.
    for pos_idx, cp in enumerate(positions):
        ref_freq = max(0.0, 1.0 - sum(freq for _, freq, _ in options_by_pos[pos_idx]))
        options_by_pos[pos_idx].append((ref_bases[cp], ref_freq, _REF_MEMBER_ID))

    candidates: list[CandidateCodon] = []
    for combo in itertools.product(*options_by_pos):
        codon_bases = list(ref_bases)
        q_values: list[float] = []
        member_indices: list[int] = []
        choices: list[tuple[str, int]] = []
        for pos_idx, (base, freq, alt_idx) in enumerate(combo):
            codon_bases[positions[pos_idx]] = base
            q_values.append(freq)
            flat_id = _encode_member_id(pos_idx, alt_idx) if alt_idx >= 0 else _REF_MEMBER_ID
            if alt_idx >= 0:
                member_indices.append(flat_id)
            choices.append((base, flat_id))

        alt_codon = ''.join(codon_bases)
        candidates.append(CandidateCodon(
            alt_codon=alt_codon,
            alt_aa=translate_codon(alt_codon),
            member_indices=tuple(member_indices),
            choices=tuple(choices),
            q_values=tuple(q_values),
        ))

    return candidates


def _assemble_linked_factor(
    rec_id: int,
    comp: list[int],
    alt_option_freqs: list[dict[int, float]],
    alt_option_links: list[dict[int, tuple[tuple[int, int, float], ...]]],
) -> _LinkedFactor | None:
    """Build and consistency-check one multi-position phase factor linked by ``rec_id``."""
    alt_ids = sorted({
        aid
        for p in comp
        for links in alt_option_links[p].values()
        for r, aid, _ in links
        if r == rec_id
    })
    haps: list[tuple[float, dict[int, frozenset[int]]]] = []
    claimed_by_pos: dict[int, dict[int, float]] = {p: {} for p in comp}
    opt_freqs_by_pos: dict[int, dict[int, float]] = {}

    for p in comp:
        freqs_p = dict(alt_option_freqs[p])
        freqs_p[_REF_MEMBER_ID] = max(0.0, 1.0 - sum(alt_option_freqs[p].values()))
        opt_freqs_by_pos[p] = freqs_p

    for aid in alt_ids:
        afs = [
            af
            for p in comp
            for links in alt_option_links[p].values()
            for r, a, af in links
            if r == rec_id and a == aid
        ]
        if not afs or max(afs) - min(afs) > 1e-6:
            return None
        h_af = max(afs)
        h_choices: dict[int, frozenset[int]] = {}
        for p in comp:
            matched_fids = frozenset(
                fid for fid, links in alt_option_links[p].items()
                if any(r == rec_id and a == aid for r, a, _ in links)
            )
            chosen = matched_fids or frozenset({_REF_MEMBER_ID})
            h_choices[p] = chosen
            for fid in chosen:
                claimed_by_pos[p][fid] = claimed_by_pos[p].get(fid, 0.0) + h_af
        haps.append((h_af, h_choices))

    total_hap_mass = sum(h_af for h_af, _ in haps)
    if total_hap_mass > 1.0 + 1e-6:
        return None
    for p in comp:
        for fid, claimed in claimed_by_pos[p].items():
            if claimed > opt_freqs_by_pos[p].get(fid, 0.0) + 1e-6:
                return None

    return _LinkedFactor(
        positions=tuple(comp),
        haplotypes=tuple(haps),
        residual_mass=max(0.0, 1.0 - total_hap_mass),
        claimed_by_pos=claimed_by_pos,
        opt_freqs_by_pos=opt_freqs_by_pos,
    )


def _connected_components(n_nodes: int, groups: list[list[int]]) -> list[list[int]]:
    """Return sorted connected components for ``0..n_nodes-1`` given clique ``groups``."""
    adj: dict[int, set[int]] = {p: set() for p in range(n_nodes)}
    for grp in groups:
        for p1 in grp:
            adj[p1].update(p2 for p2 in grp if p2 != p1)

    visited: set[int] = set()
    components: list[list[int]] = []
    for p in range(n_nodes):
        if p in visited:
            continue
        stack = [p]
        comp: list[int] = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            comp.append(cur)
            stack.extend(adj[cur] - visited)
        components.append(sorted(comp))
    return components


def _build_phase_factors(
    variants_at_codon: list[dict],
    n_pos: int,
) -> list[_LinkedFactor | int] | None:
    """Partition codon positions into disjoint singleton or single-record linked phase factors."""
    seen: dict[int, int] = {}
    alt_freqs: list[dict[int, float]] = []
    alt_links: list[dict[int, tuple[tuple[int, int, float], ...]]] = []

    for spec in variants_at_codon:
        cp = spec['codon_pos']
        if not isinstance(cp, int) or cp < 0 or cp >= 3:
            return None
        if cp not in seen:
            seen[cp] = len(alt_freqs)
            alt_freqs.append({})
            alt_links.append({})
        pos_idx = seen[cp]
        raw_links = spec.get('haplotype_links')
        for alt_idx, (_base, freq) in enumerate(spec['alts']):
            fid = _encode_member_id(pos_idx, alt_idx)
            alt_freqs[pos_idx][fid] = float(freq)
            alt_links[pos_idx][fid] = (
                tuple((int(r), int(a), float(af)) for r, a, af in raw_links[alt_idx])
                if raw_links is not None and alt_idx < len(raw_links)
                else ()
            )

    record_positions: dict[int, set[int]] = {}
    for pos_idx, links_by_fid in enumerate(alt_links):
        for links in links_by_fid.values():
            for rec_id, _alt_id, _af in links:
                if rec_id >= 0:
                    record_positions.setdefault(rec_id, set()).add(pos_idx)

    linking_records = {r for r, pset in record_positions.items() if len(pset) >= 2}
    if not linking_records:
        return None

    components = _connected_components(n_pos, [sorted(record_positions[r]) for r in linking_records])
    factors: list[_LinkedFactor | int] = []
    for comp in components:
        if len(comp) == 1:
            factors.append(comp[0])
            continue
        recs_in_comp = [r for r in linking_records if record_positions[r] & set(comp)]
        # Multiple distinct unphased records cannot establish joint phase across their union.
        if len(recs_in_comp) != 1:
            factors.extend(comp)
            continue
        linked = _assemble_linked_factor(recs_in_comp[0], comp, alt_freqs, alt_links)
        factors.extend(comp) if linked is None else factors.append(linked)
    return factors


def _factor_bounds_for_candidate(
    factor: _LinkedFactor | int,
    cand: CandidateCodon,
) -> tuple[float, float]:
    """Compute ``(lower, upper)`` frequency bounds for one phase factor under ``cand``."""
    if isinstance(factor, int):
        q = cand.q_values[factor]
        return q, q

    linked_mass = sum(
        h_af
        for h_af, h_choices in factor.haplotypes
        if all(cand.choices[p][1] in h_choices[p] for p in factor.positions)
    )
    unlinked_per_pos = [
        max(
            0.0,
            factor.opt_freqs_by_pos[p][cand.choices[p][1]]
            - factor.claimed_by_pos[p].get(cand.choices[p][1], 0.0),
        )
        for p in factor.positions
    ]
    n_in_factor = len(factor.positions)
    lower_unlinked = max(0.0, sum(unlinked_per_pos) - (n_in_factor - 1) * factor.residual_mass)
    upper_unlinked = min(unlinked_per_pos)
    return min(linked_mass + lower_unlinked, linked_mass + upper_unlinked), linked_mass + upper_unlinked


def _compute_codon_frechet_states(
    variants_at_codon: list[dict],
    ref_codon: str,
    min_fraction: float,
    eps: float = _FRECHET_EPS,
) -> list[CodonState]:
    """Compute conservative Fréchet intersection bounds for every candidate codon state."""
    candidates = enumerate_candidate_codon_states(variants_at_codon, ref_codon)
    if not candidates:
        return []

    factors = _build_phase_factors(variants_at_codon, len(candidates[0].q_values))
    states: list[CodonState] = []
    for cand in candidates:
        if factors is None:
            lower, upper, ff, accepted = frechet_and_bound(cand.q_values, min_fraction, eps)
        else:
            bounds = [_factor_bounds_for_candidate(f, cand) for f in factors]
            lower, upper, ff, accepted = frechet_factor_bounds(bounds, min_fraction, eps)
        states.append(CodonState(
            alt_codon=cand.alt_codon,
            alt_aa=cand.alt_aa,
            lower=lower,
            upper=upper,
            forced_fraction=ff,
            accepted=accepted,
            member_indices=cand.member_indices,
        ))
    return states


def _extract_read_codon_key(
    read: pysam.AlignedSegment,
    codon_query_positions: list[tuple[int, int]],
    complement_bases: bool,
) -> tuple[tuple[int, str], ...] | None:
    """Read coding-orientation bases at all variant positions from one spanning alignment."""
    ref_to_query = {
        ref_pos: query_pos
        for query_pos, ref_pos in read.get_aligned_pairs(matches_only=True)
        if ref_pos is not None and query_pos is not None
    }
    seq = read.query_sequence or ''
    bases: list[tuple[int, str]] = []
    for codon_pos, qpos in codon_query_positions:
        qidx = ref_to_query.get(qpos)
        if qidx is None:
            return None
        base = seq[qidx].upper()
        if complement_bases:
            base = reverse_complement(base)
        bases.append((codon_pos, base))
    return tuple(bases)


def compute_codon_cooccurrence(
    bam: pysam.AlignmentFile,
    contig: str,
    codon_query_positions: list[tuple[int, int]],
    candidate_states: list[CandidateCodon],
    complement_bases: bool,
    min_mapq: int,
    min_depth: int,
) -> list[CodonState]:
    """Count spanning BAM molecules per candidate codon state (paired-end deduplicated by qname)."""
    if not codon_query_positions or not candidate_states:
        return []

    qpositions = [qp for _, qp in codon_query_positions]
    min_qpos, max_qpos = min(qpositions), max(qpositions)

    candidate_keys = {
        tuple((codon_query_positions[i][0], cand.choices[i][0]) for i in range(len(codon_query_positions))): cand
        for cand in candidate_states
    }

    molecules: dict[str, list[tuple[tuple[int, str], ...]]] = {}
    for read in bam.fetch(contig, min_qpos, max_qpos + 1):
        # Primary, QC-passing, non-duplicate reads only so each query_name group has <= 2 mates.
        if read.is_secondary or read.is_supplementary or read.is_qcfail or read.is_duplicate:
            continue
        if (read.mapping_quality or 0) < min_mapq:
            continue
        if read.reference_start > min_qpos or read.reference_end is None or read.reference_end < max_qpos + 1:
            continue
        key = _extract_read_codon_key(read, codon_query_positions, complement_bases)
        if key is not None:
            molecules.setdefault(read.query_name or '', []).append(key)

    # Paired-end dedup: agreeing mates count once; disagreeing mates are rejected.
    counted: list[tuple[tuple[int, str], ...]] = []
    for codons in molecules.values():
        if len(codons) == 2 and codons[0] != codons[1]:
            continue
        counted.append(codons[0])

    spanning_molecules = len(counted)
    if spanning_molecules < min_depth:
        return []

    counts: dict[CandidateCodon, int] = {}
    for key in counted:
        matched = candidate_keys.get(key)
        if matched is not None:
            counts[matched] = counts.get(matched, 0) + 1

    return [
        CodonState(
            alt_codon=cand.alt_codon,
            alt_aa=cand.alt_aa,
            lower=count / spanning_molecules,
            upper=count / spanning_molecules,
            forced_fraction=1.0,
            accepted=True,
            member_indices=cand.member_indices,
        )
        for cand, count in counts.items()
    ]


def plan_combined_snp_groups(
    variants: list[VariantCall],
    features: list[FeatureRecord],
) -> dict[tuple[int, int], list[int]]:
    """Group variant indices by ``(feature_id, codon_idx)`` for codons with >= 2 SNPs."""
    grouped: dict[tuple[int, int], list[int]] = {}
    for idx, var in enumerate(variants):
        if not is_snp(var.ref, var.alt):
            continue
        for feature in features:
            if not feature.contains(var.pos):
                continue
            codon_idx = feature.codon_index(var.pos)
            if codon_idx is not None and codon_idx >= 0:
                grouped.setdefault((feature.id, codon_idx), []).append(idx)

    return {key: sorted(idxs) for key, idxs in grouped.items() if len(idxs) >= 2}


def _prepare_codon_members(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> tuple[list[tuple[VariantCall, int, str, int]], list[int], list[dict]]:
    """Build per-member metadata and candidate-enumerator specs for co-codon SNPs."""
    members: list[tuple[VariantCall, int, str, int]] = []
    alts_by_pos: dict[int, list[tuple[str, float, tuple[tuple[int, int, float], ...]]]] = {}
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
        alts_by_pos[codon_pos].append((alt_base, float(var.allele_freq), tuple(var.haplotype_links)))
        member_index = _encode_member_id(pos_order.index(codon_pos), alt_idx)
        members.append((var, codon_pos, alt_base, member_index))

    member_specs = [
        {
            'codon_pos': cp,
            'alts': [(ab, freq) for ab, freq, _ in alts_by_pos[cp]],
            'haplotype_links': [links for _, _, links in alts_by_pos[cp]],
        }
        for cp in pos_order
    ]
    return members, pos_order, member_specs


def _resolve_codon_states(
    member_specs: list[dict],
    pos_order: list[int],
    internal_codon: str,
    codon_start: int,
    feature_name: str,
    bam_cooccurrence: BamCooccurrence | None,
    cfg: CliConfig,
) -> tuple[list[CodonState], str]:
    """Dispatch to BAM read-backed co-occurrence (``'observed'``) or Fréchet bounds (``'estimated'``)."""
    if bam_cooccurrence is None:
        states = _compute_codon_frechet_states(
            member_specs,
            internal_codon,
            cfg.codon.min_cooccurrence_codon_fraction,
            eps=cfg.codon.frechet_epsilon,
        )
        return states, 'estimated'

    candidates = enumerate_candidate_codon_states(member_specs, internal_codon)
    cds_to_query = bam_cooccurrence.cds_to_query_by_feature.get(feature_name, {})
    codon_query_positions: list[tuple[int, int]] = []
    for cp in pos_order:
        qpos = cds_to_query.get(codon_start + cp)
        if qpos is None:
            return [], 'observed'
        codon_query_positions.append((cp, qpos))

    complement_bases = bam_cooccurrence.match_strand_by_feature.get(feature_name, '+') == '-'
    states = compute_codon_cooccurrence(
        bam=bam_cooccurrence.bam,
        contig=bam_cooccurrence.contig,
        codon_query_positions=codon_query_positions,
        candidate_states=candidates,
        complement_bases=complement_bases,
        min_mapq=bam_cooccurrence.min_mapq,
        min_depth=bam_cooccurrence.min_depth,
    )
    return states, 'observed'


def _annotate_combined_snp_codon(
    variants: list[VariantCall],
    feature: FeatureRecord,
    bam_cooccurrence: BamCooccurrence | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> list[AnnotatedVariant]:
    """Annotate co-codon SNPs using BAM exact co-occurrence or conservative Fréchet bounds."""
    if not variants:
        raise ValueError('Combined SNP annotation requires at least one variant')

    anchor = min(variants, key=lambda v: v.pos)
    codon_idx = feature.codon_index(anchor.pos)
    if codon_idx is None:
        raise ValueError(
            f'Combined SNP annotation requires coding codon index for feature {feature.name!r} '
            f'at genomic position {anchor.pos}'
        )
    codon_start = feature.codon_start + codon_idx * 3
    internal_codon = feature.nt_sequence.upper()[codon_start:codon_start + 3]
    ref_aa = translate_codon(internal_codon)

    members, pos_order, member_specs = _prepare_codon_members(variants, feature)
    states, freq_method = _resolve_codon_states(
        member_specs, pos_order, internal_codon, codon_start, feature.name, bam_cooccurrence, cfg,
    )
    if not states or not any(s.accepted and s.member_indices for s in states):
        return _combined_fallback_single_snp(variants, feature)

    accepted = [s for s in states if s.accepted]
    eps = cfg.codon.frechet_epsilon

    # Strongest forced (forced_fraction == 1.0) accepted state per member for forced-overlap promotion.
    forced_by_member: dict[int, CodonState | None] = {}
    for _, _, _, member_index in members:
        forced_candidates = [
            s for s in accepted
            if member_index in s.member_indices and abs(s.forced_fraction - 1.0) <= eps
        ]
        forced_by_member[member_index] = (
            max(forced_candidates, key=lambda s: (s.lower, len(s.member_indices)))
            if forced_candidates
            else None
        )

    annotations: list[AnnotatedVariant] = []
    for var, codon_pos, alt_base, member_index in members:
        single_bases = list(internal_codon)
        single_bases[codon_pos] = alt_base
        single_codon = ''.join(single_bases)
        single_aa = translate_codon(single_codon)

        single_state = next(
            (s for s in states if len(s.member_indices) == 1 and s.member_indices[0] == member_index),
            None,
        )
        single_rejected = single_state is None or single_state.lower <= eps

        forced_state = forced_by_member.get(member_index)
        promoted = single_rejected and forced_state is not None
        if promoted and forced_state is not None:
            single_codon = forced_state.alt_codon
            single_aa = forced_state.alt_aa

        member_states = [
            s for s in accepted
            if member_index in s.member_indices and not (promoted and s is forced_state)
        ]
        combined_states = _dedupe_states_by_aa(member_states)

        if promoted and forced_state is not None:
            single_aa_freq = forced_state.lower
        elif single_state is not None:
            single_aa_freq = max(0.0, single_state.lower)
        else:
            single_aa_freq = 0.0

        # Fold synonymous combined codons encoding the same AA as single_aa into single_aa_freq.
        other_same_aa = sum(s.lower for s in member_states if s.alt_aa == single_aa and s is not single_state)
        if single_aa_freq > 0 and other_same_aa > 0:
            single_aa_freq += other_same_aa
            combined_states = [s for s in combined_states if s.alt_aa != single_aa]

        annotations.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=internal_codon,
            alt_codon=single_codon,
            ref_aa=ref_aa,
            alt_aa=single_aa,
            consequence=classify_snp_consequence(ref_aa, single_aa, codon_idx),
            is_combined_codon_event=True,
            combined_member_count=len(members),
            combined_states=combined_states,
            single_exchange_aa_freq=single_aa_freq,
            freq_method=freq_method,
        ))
    return annotations


# Public alias used by respro.core.annotation (keeps private name available for direct unit tests).
annotate_combined_snp_codon = _annotate_combined_snp_codon


def _dedupe_states_by_aa(states: list[CodonState]) -> list[CodonState]:
    """Merge accepted ``CodonState`` entries sharing ``alt_aa`` by summing their lower bounds."""
    by_aa: dict[str, CodonState] = {}
    for s in states:
        prev = by_aa.get(s.alt_aa)
        if prev is None:
            by_aa[s.alt_aa] = s
        else:
            by_aa[s.alt_aa] = CodonState(
                alt_codon=prev.alt_codon,
                alt_aa=prev.alt_aa,
                lower=prev.lower + s.lower,
                upper=max(prev.upper, s.upper),
                forced_fraction=max(prev.forced_fraction, s.forced_fraction),
                accepted=True,
                member_indices=prev.member_indices,
            )
    return list(by_aa.values())


def _combined_fallback_single_snp(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """Fall back to independent single-SNP annotations when no combined state is accepted."""
    coding_nt = feature.nt_sequence.upper()[feature.codon_start:]
    annotations: list[AnnotatedVariant] = []
    for var in sorted(variants, key=lambda v: v.pos):
        cds_pos = feature.genomic_to_cds_position(var.pos)
        coding_pos = None if cds_pos is None else cds_pos - feature.codon_start
        if coding_pos is None or coding_pos < 0:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        codon_idx, pos_in_codon = divmod(coding_pos, 3)
        internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
        if not internal_codon:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        coding_alt = reverse_complement(var.alt) if feature.strand == '-' else var.alt.upper()
        annotations.append(annotate_snp(var, feature, internal_codon, codon_idx, pos_in_codon, coding_alt))
    return annotations
