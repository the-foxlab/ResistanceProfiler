"""
Lightweight dataclass models for in-memory pipeline objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_INTERNAL_FORMULA_COMPONENT_DRUG_NAME = '__formula_component__'


def is_internal_formula_component_drug_name(drug_name: str) -> bool:
    """Return True when a drug name is the internal placeholder for formula members."""
    return (drug_name or '').strip().lower() == _INTERNAL_FORMULA_COMPONENT_DRUG_NAME


@dataclass(frozen=True)
class Publication:
    """A deduplicated publication entry linked to one or more resistance rules."""

    id: int
    doi: str
    title: str
    pubmed_id: str
    raw_input: str  # original curator string; preserved as display fallback


@dataclass(frozen=True)
class CoverageGap:
    """A codon stretch that could not be assessed due to N-bases or missing alignment in FASTA mode."""

    feature_name: str
    codon_start: int  # 0-based codon index, inclusive
    codon_end: int    # 0-based codon index, inclusive
    chrom: str = ''   # query contig (== ReferenceGroup.query_name); '' when single-reference/legacy


@dataclass
class FeatureRecord:
    """A annotation loaded from the database."""

    id: int
    reference_id: int
    name: str
    protein: str
    start: int
    end: int
    strand: str
    codon_start: int = 0  # 0-based offset (GenBank codon_start qualifier minus 1)
    nt_sequence: str = ''  # CDS nucleotide slice in coding orientation
    aa_sequence: str = ''  # pre-translated protein sequence stored at init time
    feature_type: str = 'CDS'
    parent_feature_name: str = ''
    reference_accession: str = ''  # accession of the parent reference (e.g. NC_001806)
    segments: tuple[FeatureSegment, ...] = field(default_factory=tuple)

    @property
    def length_nt(self) -> int:
        return self.end - self.start

    @property
    def _coding_segments(self) -> tuple[FeatureSegment, ...]:
        """Return CDS segments in 5'->3' coding orientation."""
        if not self.segments:
            return (FeatureSegment(segment_index=0, start=self.start, end=self.end),)
        if self.strand == '+':
            return self.segments
        return tuple(reversed(self.segments))

    def genomic_to_cds_position(self, pos: int) -> int | None:
        """Return the 0-based CDS offset for one genomic position, or None outside coding segments."""
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
        """Return the genomic position for one 0-based CDS offset, or None outside the CDS."""
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
        """
        Return True if pos (0-based) falls within this feature.

        :param pos: 0-based genomic position
        :return: True if position is within feature bounds
        """
        return self.genomic_to_cds_position(pos) is not None

    def codon_index(self, pos: int) -> int | None:
        """
        Return 0-based codon index for a 0-based genomic position.

        :param pos: 0-based genomic position
        :return: 0-based codon index
        """
        nt_offset = self.genomic_to_cds_position(pos)
        if nt_offset is None:
            return None
        return (nt_offset - self.codon_start) // 3

    def codon_position_in_codon(self, pos: int) -> int | None:
        """
        Return 0-based position within the codon (0, 1, or 2).

        :param pos: 0-based genomic position
        :return: 0-based position within codon
        """
        nt_offset = self.genomic_to_cds_position(pos)
        if nt_offset is None:
            return None
        return (nt_offset - self.codon_start) % 3

    @property
    def display_name(self) -> str:
        """Return the preferred display name for reports: protein for mat_peptides (if present), else name."""
        if self.feature_type == 'mat_peptide' and self.protein:
            return self.protein
        return self.name


@dataclass(frozen=True)
class FeatureSegment:
    """One CDS genomic segment as a 0-based [start, end) interval."""

    segment_index: int
    start: int
    end: int


@dataclass
class ResistanceRule:
    """A single resistance rule loaded from the database."""

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
    clinical_phenotype: str = 'unknown'
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
class FormulaRuleRuntime:
    """Runtime representation of one formula rule and its referenced atomic members."""

    id: int
    formula_id: str
    label: str
    normalized_expression: str
    drug_name: str
    drug_id: int
    phenotype: str
    clinical_phenotype: str
    ic50: str
    fold_ic50: str
    score: str
    source: str
    comment: str
    pubchem_url: str = ''
    description: str = ''
    publications: list[Publication] = field(default_factory=list)
    member_rules: dict[str, ResistanceRule] = field(default_factory=dict)


@dataclass
class ResistanceRuleSetMember:
    """One member mutation within a combined resistance rule set."""

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
    """A combined resistance rule requiring multiple member mutations to co-occur."""

    id: int
    drug_name: str
    drug_id: int
    phenotype: str
    clinical_phenotype: str = 'unknown'
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
class VariantCall:
    """A single variant extracted from a VCF record (0-based internal position)."""

    chrom: str
    pos: int
    ref: str
    alt: str
    allele_freq: float = 0.0
    depth: int = 0
    filter_status: str = 'PASS'
    query_ref_codon: str = ''


@dataclass
class AnnotatedVariant:
    """A variant with codon-aware amino acid annotation and rule matches."""

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
    af_bin: str = ''
    is_fasta_mode: bool = False  # True when derived from consensus FASTA, not a VCF
    rule_matches: list[ResistanceRule] = field(default_factory=list)

    @property
    def non_formula_component_rule_matches(self) -> list[ResistanceRule]:
        """Return matched single rules excluding internal formula-component placeholder rows."""
        return [rule for rule in self.rule_matches if not rule.is_internal_formula_component]

    @property
    def is_resistance_hit(self) -> bool:
        return bool(self.non_formula_component_rule_matches)

    def drug_hits_json(self) -> list[dict]:
        """
        Return a JSON-serializable list of matched drug/rule info.

        :return: list of dicts containing drug and rule information
        """
        return [
            {
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
                'publications': [
                    {'doi': p.doi, 'title': p.title, 'pubmed_id': p.pubmed_id, 'raw_input': p.raw_input}
                    for p in r.publications
                ],
                'pubchem_url': r.pubchem_url,
            }
            for r in self.non_formula_component_rule_matches
        ]


@dataclass
class FormulaRuleHit:
    """A fired formula resistance rule with its contributing annotated variants."""

    rule_set: ResistanceRuleSet
    matched_variants: list[AnnotatedVariant] = field(default_factory=list)
    matched_member_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """
        Return a JSON-serializable representation of this formula rule hit.

        :return: dict with rule set metadata and matched variant summaries
        """
        rs = self.rule_set
        return {
            'drug': rs.drug_name,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'ic50': rs.ic50,
            'fold_ic50': rs.fold_ic50,
            'score': rs.score,
            'publications': [
                {'doi': p.doi, 'title': p.title, 'pubmed_id': p.pubmed_id, 'raw_input': p.raw_input}
                for p in rs.publications
            ],
            'source': rs.source,
            'pubchem_url': rs.pubchem_url,
            'rule_group': rs.group_name,
            'logic_expression': rs.logic_expression,
            'members': [
                {
                    'member_id': m.external_id,
                    'feature': m.feature_name,
                    'position': m.position + 1,  # 1-based for output
                    'reference': m.reference,
                    'mutation': m.mutation,
                }
                for m in rs.members
            ],
            'matched_member_ids': list(self.matched_member_ids),
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
    """Result of aligning a query sequence to an internal feature CDS."""

    feature: FeatureRecord
    identity: float
    cds_coverage: float   # fraction of CDS bases covered by the alignment
    query_coverage: float  # fraction of query bases consumed by the alignment
    query_start: int
    query_end: int
    strand: str
    cigar: str
    cds_start: int = 0  # first aligned CDS position (0-based); used to reconstruct gapped strings


@dataclass(frozen=True)
class ReferenceGroup:
    """One matched internal reference within a (possibly multi-reference) profiling run.

    Carries the per-reference query identity, alignment matches, and the ruled
    features/rules loaded for that internal reference. A single-reference run
    produces one ``ReferenceGroup``; a multi-chrom VCF run produces one per
    matched FASTA record. Flat lists on :class:`ProfilingResult` (annotations,
    formula_hits, coverage_gaps) carry ``feature_name`` so per-reference grouping
    is derivable from this list.
    """

    reference_name: str
    reference_id: int
    organism: str
    reference_length_nt: int
    query_name: str
    query_sequence: str
    feature_matches: list[FeatureMatch] = field(default_factory=list)
    features: list[FeatureRecord] = field(default_factory=list)
    rules: list[ResistanceRule] = field(default_factory=list)
    formula_rules: list[FormulaRuleRuntime] = field(default_factory=list)
    rule_feature_names: set[str] = field(default_factory=set)


@dataclass
class ProfilingResult:
    """Complete result of a single profiling run. HTML exports are derived from this object.

    Per-reference data lives in ``references`` (one :class:`ReferenceGroup` per
    matched internal reference). Read-only convenience properties
    (:attr:`reference_name`, :attr:`reference_length_nt`, :attr:`query_sequence`,
    :attr:`feature_matches`) delegate to ``references[0]`` to support incremental
    migration of legacy single-reference readers; they are NOT stored fields.
    """

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
    annotations: list[AnnotatedVariant] = field(default_factory=list)
    formula_hits: list[FormulaRuleHit] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    sample_classifications: list[dict] = field(default_factory=list)
    references: list[ReferenceGroup] = field(default_factory=list)

    @property
    def reference_name(self) -> str:
        """Primary reference name (first matched reference); '' when no references."""
        return self.references[0].reference_name if self.references else ''

    @property
    def reference_length_nt(self) -> int:
        """Primary reference length in nucleotides; 0 when no references."""
        return self.references[0].reference_length_nt if self.references else 0

    @property
    def query_sequence(self) -> str:
        """Primary reference query sequence; '' when no references."""
        return self.references[0].query_sequence if self.references else ''

    @property
    def feature_matches(self) -> list[FeatureMatch]:
        """Primary reference feature matches; empty list when no references."""
        return self.references[0].feature_matches if self.references else []

    @property
    def cds_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations that fall within a coding region.

        :return: list of AnnotatedVariant with feature_name set
        """
        return [a for a in self.annotations if a.feature_name]

    @property
    def database_hit_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations that should be counted as database hits.

        This includes direct resistance hits and formula-only member annotations referenced by
        fired formula rules, without double-counting shared annotations.
        """
        hit_annotations: list[AnnotatedVariant] = []
        seen_annotation_ids: set[int] = set()

        for ann in self.annotations:
            if not ann.is_resistance_hit:
                continue
            ann_id = id(ann)
            if ann_id in seen_annotation_ids:
                continue
            seen_annotation_ids.add(ann_id)
            hit_annotations.append(ann)

        for formula_hit in self.formula_hits:
            for ann in formula_hit.matched_variants:
                ann_id = id(ann)
                if ann_id in seen_annotation_ids:
                    continue
                seen_annotation_ids.add(ann_id)
                hit_annotations.append(ann)

        return hit_annotations

    @property
    def database_hit_count(self) -> int:
        """Return the total number of database-hit annotations."""
        return len(self.database_hit_annotations)

    def summary_dict(self) -> dict:
        """
        Return a JSON-serializable summary (no per-variant detail).

        :return: dict with project metadata and summary statistics
        """
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
