"""In-memory domain models shared across the profiling, persistence, and reporting layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

INTERNAL_FORMULA_COMPONENT_DRUG_NAME = '__formula_component__'

# NaN sentinel distinguishes "unset" (defaults to variant.allele_freq in __post_init__)
# from a genuine 0.0 (a combined-codon member whose single state is Fréchet-impossible).
_SINGLE_EXCHANGE_AA_FREQ_UNSET = float('nan')


def is_internal_formula_component_drug_name(drug_name: str) -> bool:
    """Return True when a drug name is the internal placeholder for formula-only members."""
    return (drug_name or '').strip().lower() == INTERNAL_FORMULA_COMPONENT_DRUG_NAME


@dataclass(frozen=True)
class Publication:
    """A deduplicated publication entry linked to one or more resistance rules."""

    id: int
    doi: str
    title: str
    pubmed_id: str
    raw_input: str
    first_author: str = ''
    year: str = ''
    journal: str = ''

    def to_dict(self) -> dict[str, str]:
        """Serialize publication metadata for JSON persistence and exports."""
        return {
            'doi': self.doi,
            'title': self.title,
            'pubmed_id': self.pubmed_id,
            'raw_input': self.raw_input,
            'first_author': self.first_author,
            'year': self.year,
            'journal': self.journal,
        }


@dataclass(frozen=True)
class CoverageGap:
    """A contiguous 0-based inclusive codon stretch that could not be assessed."""

    feature_name: str
    codon_start: int
    codon_end: int
    chrom: str = ''

    @classmethod
    def merge(
        cls,
        feature_name: str,
        codon_indices: list[int],
        *,
        chrom: str = '',
    ) -> list[CoverageGap]:
        """Collapse unordered 0-based codon indices into contiguous ``CoverageGap`` runs."""
        if not codon_indices:
            return []
        sorted_positions = sorted(set(codon_indices))
        gaps: list[CoverageGap] = []
        start = end = sorted_positions[0]
        for pos in sorted_positions[1:]:
            if pos == end + 1:
                end = pos
                continue
            gaps.append(cls(feature_name=feature_name, codon_start=start, codon_end=end, chrom=chrom))
            start = end = pos
        gaps.append(cls(feature_name=feature_name, codon_start=start, codon_end=end, chrom=chrom))
        return gaps


@dataclass(frozen=True)
class FeatureSegment:
    """One CDS exon segment as a 0-based ``[start, end)`` genomic interval."""

    segment_index: int
    start: int
    end: int


@dataclass
class FeatureRecord:
    """A coding feature (CDS or mat_peptide) loaded from the project database."""

    id: int
    reference_id: int
    name: str
    protein: str
    start: int
    end: int
    strand: str
    codon_start: int = 0
    nt_sequence: str = ''
    aa_sequence: str = ''
    feature_type: str = 'CDS'
    parent_feature_name: str = ''
    reference_accession: str = ''
    segments: tuple[FeatureSegment, ...] = field(default_factory=tuple)

    @property
    def _coding_segments(self) -> tuple[FeatureSegment, ...]:
        """Return CDS exon segments ordered 5'->3' in coding orientation."""
        if not self.segments:
            return (FeatureSegment(segment_index=0, start=self.start, end=self.end),)
        # Sort by genomic start so both ascending (join(complement(...))) and
        # descending (complement(join(...))) BioPython parts map consistently to 5'->3'.
        return tuple(sorted(self.segments, key=lambda seg: seg.start, reverse=(self.strand == '-')))

    @property
    def length_nt(self) -> int:
        """Total coding nucleotide length across all exon segments."""
        return sum(seg.end - seg.start for seg in self._coding_segments)

    def genomic_to_cds_position(self, pos: int) -> int | None:
        """Map a 0-based genomic position to its 0-based CDS offset, or None outside exons."""
        cds_offset = 0
        for segment in self._coding_segments:
            segment_length = segment.end - segment.start
            if segment.start <= pos < segment.end:
                if self.strand == '+':
                    return cds_offset + (pos - segment.start)
                return cds_offset + ((segment.end - 1) - pos)
            cds_offset += segment_length
        return None

    def cds_to_genomic_position(self, cds_pos: int) -> int | None:
        """Map a 0-based CDS offset to its 0-based genomic position, or None outside the CDS."""
        if cds_pos < 0:
            return None
        remaining = cds_pos
        for segment in self._coding_segments:
            segment_length = segment.end - segment.start
            if remaining < segment_length:
                if self.strand == '+':
                    return segment.start + remaining
                return (segment.end - 1) - remaining
            remaining -= segment_length
        return None

    def contains(self, pos: int) -> bool:
        """Return True if 0-based genomic ``pos`` falls inside any coding exon."""
        return self.genomic_to_cds_position(pos) is not None

    def codon_index(self, pos: int) -> int | None:
        """Return 0-based codon index for genomic ``pos``, or None outside the coding frame."""
        cds_pos = self.genomic_to_cds_position(pos)
        if cds_pos is None or cds_pos < self.codon_start:
            return None
        return (cds_pos - self.codon_start) // 3

    def codon_position_in_codon(self, pos: int) -> int | None:
        """Return 0-based nucleotide offset within its codon (0, 1, or 2), or None."""
        cds_pos = self.genomic_to_cds_position(pos)
        if cds_pos is None or cds_pos < self.codon_start:
            return None
        return (cds_pos - self.codon_start) % 3

    @property
    def display_name(self) -> str:
        """Preferred label for reports: protein name for mat_peptides when present, else name."""
        if self.feature_type == 'mat_peptide' and self.protein:
            return self.protein
        return self.name


@dataclass(frozen=True)
class IntronInterval:
    """One intron skipped between CDS exons in an unspliced-query alignment.

    ``query_start`` is the 0-based exon-only coding-orientation offset relative to the
    aligned region start; consumers indexing the unspliced region accumulate preceding
    intron lengths via ``full_intron_spans()``.
    """

    cds_junction_pos: int
    query_start: int
    length: int


@dataclass
class ResistanceRule:
    """An atomic resistance rule targeting one feature codon position."""

    id: int
    feature_name: str
    feature_id: int
    drug_name: str
    drug_id: int
    reference_identifier: str
    position: int
    reference: str
    mutation: str
    phenotype: str
    external_id: str = ''
    clinical_phenotype: str = ''
    ic50: str = ''
    fold_ic50: str = ''
    score: str = ''
    source: str = ''
    pubchem_url: str = ''
    description: str = ''
    comment: str = ''
    publications: list[Publication] = field(default_factory=list)
    is_internal_formula_component: bool = False


@dataclass
class ResistanceRuleSetMember:
    """One atomic member referenced inside a boolean formula resistance rule."""

    id: int
    rule_set_id: int
    feature_name: str
    feature_id: int
    reference_identifier: str
    position: int
    reference: str
    mutation: str
    external_id: str = ''


@dataclass
class ResistanceRuleSet:
    """A boolean formula resistance rule combining one or more atomic members."""

    id: int
    drug_name: str
    drug_id: int
    phenotype: str
    clinical_phenotype: str = ''
    ic50: str = ''
    fold_ic50: str = ''
    score: str = ''
    source: str = ''
    group_name: str = ''
    pubchem_url: str = ''
    description: str = ''
    comment: str = ''
    logic_expression: str = ''
    publications: list[Publication] = field(default_factory=list)
    members: list[ResistanceRuleSetMember] = field(default_factory=list)


@dataclass
class CodonState:
    """One candidate codon state from Fréchet lower-bound or BAM read-backed co-occurrence."""

    alt_codon: str
    alt_aa: str
    lower: float
    upper: float
    forced_fraction: float
    accepted: bool
    member_indices: tuple[int, ...]


@dataclass
class VariantCall:
    """A 0-based nucleotide variant in internal-reference coordinates.

    Carries optional provenance from VCF reference comparison (``user_*`` coords,
    ``source``, ``record_index``, ``alt_index``, ``haplotype_links``) and FASTA/reconciled
    codon context (``query_ref_codon`` bound to ``query_codon_feature_id``).
    """

    chrom: str
    pos: int
    ref: str
    alt: str
    allele_freq: float = 0.0
    depth: int = 0
    filter_status: str = 'PASS'
    query_ref_codon: str = ''
    query_codon_feature_id: int = 0
    user_chrom: str = ''
    user_pos: int = 0
    user_ref: str = ''
    user_alt: str = ''
    source: str = ''
    record_index: int = -1
    alt_index: int = -1
    haplotype_links: tuple[tuple[int, int, float], ...] = ()


@dataclass
class AnnotatedVariant:
    """A variant annotated with codon-aware amino-acid consequence and matched rules."""

    variant: VariantCall
    feature_name: str = ''
    codon_pos: int = 0
    ref_codon: str = ''
    alt_codon: str = ''
    ref_aa: str = ''
    alt_aa: str = ''
    consequence: str = ''
    is_combined_codon_event: bool = False
    combined_member_count: int = 1
    combined_states: list[CodonState] = field(default_factory=list)
    # Amino-acid frequency of the single-exchange codon (distinct from variant.allele_freq
    # for combined-codon members, where it may be 0.0 if the single state is impossible).
    single_exchange_aa_freq: float = _SINGLE_EXCHANGE_AA_FREQ_UNSET
    # Per matched rule.id: amino-acid frequency and matched alt_aa actually triggering the rule.
    rule_effect_aa_freq: dict[int, float] = field(default_factory=dict)
    rule_effect_alt: dict[int, str] = field(default_factory=dict)
    # 'observed' (single-SNP or BAM read-backed) vs 'estimated' (Fréchet lower bound).
    freq_method: str = 'observed'
    af_bin: str = ''
    is_fasta_mode: bool = False
    rule_matches: list[ResistanceRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Default unset single_exchange_aa_freq to nucleotide AF; NaN check preserves genuine 0.0.
        if self.single_exchange_aa_freq != self.single_exchange_aa_freq:
            self.single_exchange_aa_freq = self.variant.allele_freq

    @property
    def has_user_ref_coords(self) -> bool:
        """Return True when supplied-reference VCF coordinates are recorded."""
        return bool(self.variant.user_ref or self.variant.user_alt)

    @property
    def user_ref_coords(self) -> tuple[str, int, str, str]:
        """Return 0-based supplied-reference coordinates ``(chrom, pos, ref, alt)``."""
        return (
            self.variant.user_chrom,
            self.variant.user_pos,
            self.variant.user_ref,
            self.variant.user_alt,
        )

    @property
    def non_formula_component_rule_matches(self) -> list[ResistanceRule]:
        """Return matched single rules excluding internal formula-component placeholders."""
        return [rule for rule in self.rule_matches if not rule.is_internal_formula_component]

    @property
    def is_resistance_hit(self) -> bool:
        return bool(self.non_formula_component_rule_matches)

    def drug_hits_json(self) -> list[dict]:
        """Serialize non-placeholder matched rules for results persistence and JSON export."""
        return [
            {
                'rule_id': r.id,
                'drug': r.drug_name,
                'external_id': r.external_id,
                'reference_identifier': r.reference_identifier,
                'reference': r.reference,
                'mutation': r.mutation,
                'phenotype': r.phenotype,
                'clinical_phenotype': r.clinical_phenotype,
                'ic50': r.ic50,
                'fold_ic50': r.fold_ic50,
                'score': r.score,
                'publications': [p.to_dict() for p in r.publications],
                'pubchem_url': r.pubchem_url,
            }
            for r in self.non_formula_component_rule_matches
        ]


@dataclass
class FormulaRuleHit:
    """A fired boolean formula rule with its contributing annotations and Fréchet metrics."""

    rule_set: ResistanceRuleSet
    matched_variants: list[AnnotatedVariant] = field(default_factory=list)
    matched_member_ids: list[str] = field(default_factory=list)
    frechet_lower: float = 0.0
    forced_fraction: float = 0.0
    member_count: int = 1

    def to_dict(self) -> dict:
        """Serialize this formula hit for results persistence and JSON export."""
        rs = self.rule_set
        return {
            'drug': rs.drug_name,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'ic50': rs.ic50,
            'fold_ic50': rs.fold_ic50,
            'score': rs.score,
            'publications': [p.to_dict() for p in rs.publications],
            'source': rs.source,
            'pubchem_url': rs.pubchem_url,
            'rule_group': rs.group_name,
            'logic_expression': rs.logic_expression,
            'members': [
                {
                    'member_id': m.external_id,
                    'feature': m.feature_name,
                    'position': m.position + 1,  # 1-based for user-facing output
                    'reference': m.reference,
                    'mutation': m.mutation,
                }
                for m in rs.members
            ],
            'matched_member_ids': list(self.matched_member_ids),
            'frechet_lower': self.frechet_lower,
            'forced_fraction': self.forced_fraction,
            'member_count': self.member_count,
            'matched_variants': [
                {
                    'feature': v.feature_name,
                    'codon_pos': v.codon_pos + 1,
                    'ref_aa': v.ref_aa,
                    'alt_aa': v.alt_aa,
                    'allele_freq': v.variant.allele_freq,
                }
                for v in self.matched_variants
            ],
        }


@dataclass(frozen=True)
class FeatureMatch:
    """Result of aligning a query sequence against one internal feature CDS."""

    feature: FeatureRecord
    identity: float
    cds_coverage: float
    query_coverage: float
    query_start: int
    query_end: int
    strand: str
    cigar: str
    cds_start: int = 0
    intron_intervals: tuple[IntronInterval, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReferenceGroup:
    """One matched internal reference (and its ruled features/rules) within a profiling run."""

    reference_name: str
    reference_id: int
    organism: str
    reference_length_nt: int
    query_name: str
    query_sequence: str
    feature_matches: list[FeatureMatch] = field(default_factory=list)
    features: list[FeatureRecord] = field(default_factory=list)
    rules: list[ResistanceRule] = field(default_factory=list)
    formula_rules: list[ResistanceRuleSet] = field(default_factory=list)
    rule_feature_names: set[str] = field(default_factory=set)


@dataclass
class ProfilingResult:
    """Complete output of a profiling run; feeds HTML/JSON/TSV/PDF exports and results DB."""

    project_name: str = ''
    organism: str = ''
    sample_name: str = ''
    vcf_name: str = ''
    run_timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec='seconds')
    )
    total_variants: int = 0
    variants_in_cds: int = 0
    resistance_hits: int = 0
    is_fasta_mode: bool = False
    coverage_assessed: bool = False
    annotations: list[AnnotatedVariant] = field(default_factory=list)
    formula_hits: list[FormulaRuleHit] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    sample_classifications: list[dict] = field(default_factory=list)
    references: list[ReferenceGroup] = field(default_factory=list)

    @property
    def reference_name(self) -> str:
        return self.references[0].reference_name if self.references else ''

    @property
    def reference_length_nt(self) -> int:
        return self.references[0].reference_length_nt if self.references else 0

    @property
    def query_sequence(self) -> str:
        return self.references[0].query_sequence if self.references else ''

    @property
    def feature_matches(self) -> list[FeatureMatch]:
        return self.references[0].feature_matches if self.references else []

    @property
    def cds_annotations(self) -> list[AnnotatedVariant]:
        return [a for a in self.annotations if a.feature_name]

    @property
    def database_hit_annotations(self) -> list[AnnotatedVariant]:
        """Return unique annotations counted as database hits (direct + fired formula members)."""
        hit_annotations: list[AnnotatedVariant] = []
        seen_ids: set[int] = set()

        for ann in self.annotations:
            if ann.is_resistance_hit and id(ann) not in seen_ids:
                seen_ids.add(id(ann))
                hit_annotations.append(ann)

        for formula_hit in self.formula_hits:
            for ann in formula_hit.matched_variants:
                if id(ann) not in seen_ids:
                    seen_ids.add(id(ann))
                    hit_annotations.append(ann)

        return hit_annotations

    @property
    def database_hit_count(self) -> int:
        return len(self.database_hit_annotations)

    def summary_dict(self) -> dict:
        """Return a JSON-serializable summary of run metadata and counts."""
        return {
            'project_name': self.project_name,
            'organism': self.organism,
            'reference': self.reference_name,
            'reference_length_nt': self.reference_length_nt,
            'sample': self.sample_name,
            'vcf': self.vcf_name,
            'timestamp': self.run_timestamp,
            'total_variants': self.total_variants,
            'variants_in_cds': self.variants_in_cds,
            'resistance_hits': self.resistance_hits,
            'formula_rule_hits': len(self.formula_hits),
        }
