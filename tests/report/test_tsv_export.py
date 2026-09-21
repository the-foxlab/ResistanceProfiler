"""Tests for the TSV results export (``--export tsv``)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from respro.db.models import (
    AnnotatedVariant,
    CodonState,
    FeatureMatch,
    FeatureRecord,
    FormulaRuleHit,
    ProfilingResult,
    Publication,
    ReferenceGroup,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
)
from respro.report._row_helpers import (
    build_reference_name_by_chrom,
    nt_change_stored,
    nt_change_user,
)
from respro.report.non_html_exports import export_results, write_tsv

# Canonical 19-column header, in order.
TSV_COLUMNS = [
    'reference', 'gene', 'nt_mut', 'nt_mut_user', 'strand',
    'aa_frequency', 'depth', 'consequence', 'aa_effects',
    'in_database', 'rule_type',
    'drug', 'phenotype', 'clinical_phenotype', 'ic50', 'fold_ic50', 'score',
    'source', 'publications',
]

_PLACEHOLDER = 'n/a'

# Columns always present (structural / always-shown, mirroring the HTML Database
# Hits table). Conditional columns (phenotype group, ic50, fold_ic50, score,
# publications) are dropped when no row carries a real value.
_TSV_ALWAYS_COLUMNS = [
    'reference', 'gene', 'nt_mut', 'nt_mut_user', 'strand',
    'aa_frequency', 'depth', 'consequence', 'aa_effects',
    'in_database', 'rule_type',
    'drug', 'source',
]
_TSV_CONDITIONAL_COLUMNS = [
    'phenotype', 'clinical_phenotype', 'ic50', 'fold_ic50', 'score',
    'publications',
]


def _expected_header(present: set[str]) -> list[str]:
    """Build the expected TSV header: always-columns plus the given conditional ones.

    ``present`` is the set of conditional columns that have at least one real
    value. Each conditional column is kept independently, mirroring the HTML's
    independent has_*_metrics flags.
    """
    return [c for c in TSV_COLUMNS if c in _TSV_ALWAYS_COLUMNS or c in present]


def _read_tsv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read a TSV file into (header, rows)."""
    with path.open(encoding='utf-8', newline='') as fh:
        reader = csv.reader(fh, delimiter='\t')
        rows = list(reader)
    return rows[0], rows[1:]


def _pub(doi: str = '', pubmed_id: str = '') -> Publication:
    return Publication(id=0, doi=doi, title='', pubmed_id=pubmed_id, raw_input='')


def _feature(name: str, strand: str = '+', reference_id: int = 1, fid: int = 1) -> FeatureRecord:
    return FeatureRecord(
        id=fid, reference_id=reference_id, name=name, protein=name,
        start=0, end=12, strand=strand, codon_start=0, nt_sequence='ATGAAAGCTTAA',
    )


def _rule(
    *, rid: int, drug: str, phenotype: str = 'resistant', clinical: str = 'unknown',
    ic50: str = '', fold_ic50: str = '', score: str = '', external_id: str = '',
    source: str = '', publications: list[Publication] | None = None,
    mutation: str = 'E', is_internal_formula_component: bool = False,
) -> ResistanceRule:
    return ResistanceRule(
        id=rid, feature_name='gag', feature_id=1, drug_name=drug, drug_id=1,
        reference_identifier='ref', position=2, reference='K', mutation=mutation,
        phenotype=phenotype, clinical_phenotype=clinical, ic50=ic50,
        fold_ic50=fold_ic50, score=score, external_id=external_id, source=source,
        publications=publications or [], is_internal_formula_component=is_internal_formula_component,
    )


def _ann(
    *, chrom: str = 'ref', pos: int = 3, ref: str = 'A', alt: str = 'G',
    af: float = 0.95, depth: int = 500, feature: str = 'gag', codon_pos: int = 2,
    ref_aa: str = 'K', alt_aa: str = 'E', consequence: str = 'missense',
    af_bin: str = 'high', is_fasta_mode: bool = False,
    user_chrom: str = '', user_pos: int = 0, user_ref: str = '', user_alt: str = '',
    is_combined_codon_event: bool = False, ref_codon: str = 'AAA', alt_codon: str = 'GAA',
    rule_matches: list[ResistanceRule] | None = None,
    combined_states: list[CodonState] | None = None,
) -> AnnotatedVariant:
    return AnnotatedVariant(
        variant=VariantCall(
            chrom=chrom, pos=pos, ref=ref, alt=alt, allele_freq=af, depth=depth,
            user_chrom=user_chrom, user_pos=user_pos, user_ref=user_ref, user_alt=user_alt,
        ),
        feature_name=feature, codon_pos=codon_pos, ref_codon=ref_codon, alt_codon=alt_codon,
        ref_aa=ref_aa, alt_aa=alt_aa, consequence=consequence, af_bin=af_bin,
        is_fasta_mode=is_fasta_mode, is_combined_codon_event=is_combined_codon_event,
        rule_matches=rule_matches or [],
        combined_states=combined_states or [],
    )


def _result(annotations: list[AnnotatedVariant], *, formula_hits=None, features=None,
             reference_name='NC_001798.2', organism='HSV-1', query_name='ref',
             is_fasta_mode=False) -> ProfilingResult:
    feats = features if features is not None else [_feature('gag')]
    references = [
        ReferenceGroup(
            reference_name=reference_name, reference_id=1, organism=organism,
            reference_length_nt=1000, query_name=query_name, query_sequence='ATGAAAGCTTAA',
            features=feats, rule_feature_names={'gag'},
            feature_matches=[
                FeatureMatch(
                    feature=feats[0], identity=1.0, cds_coverage=1.0, query_coverage=1.0,
                    query_start=0, query_end=12, strand='+', cigar='12M', cds_start=0,
                ),
            ],
        ),
    ]
    return ProfilingResult(
        project_name='Test', organism=organism, sample_name='S1', vcf_name='in.vcf',
        total_variants=len(annotations), variants_in_cds=len(annotations),
        resistance_hits=sum(1 for a in annotations if a.is_resistance_hit),
        is_fasta_mode=is_fasta_mode, annotations=annotations,
        formula_hits=formula_hits or [], references=references,
    )


class TestRowHelpers:
    """Unit tests for the shared nt-change helpers."""

    def test_nt_change_stored_simple_snp(self) -> None:
        ann = _ann(pos=3, ref='A', alt='G')
        assert nt_change_stored(ann) == 'A4G'

    def test_nt_change_stored_combined_codon_event_uses_per_snp_coords(self) -> None:
        """A per-SNP combined-codon row reports its own VCF nucleotide change,
        not the codon-form ``ref_codon{pos}alt_codon``. Each member keeps its
        own ref/alt/pos."""
        ann = _ann(
            is_combined_codon_event=True, ref_codon='AAA', alt_codon='GAA',
            codon_pos=2, pos=3, ref='A', alt='G',
        )
        assert nt_change_stored(ann) == 'A4G'

    def test_nt_change_user_empty_in_fasta_mode(self) -> None:
        ann = _ann(is_fasta_mode=True)
        assert nt_change_user(ann) == ''

    def test_nt_change_user_from_preserved_coords(self) -> None:
        ann = _ann(user_ref='C', user_pos=5, user_alt='T')
        assert nt_change_user(ann) == 'C6T'

    def test_build_reference_name_by_chrom(self) -> None:
        r = _result([], reference_name='NC_001798.2', query_name='chrom1')
        assert build_reference_name_by_chrom(r) == {'chrom1': 'NC_001798.2'}


class TestWriteTsvHeader:
    def test_header_matches_agreed_layout(self, tmp_path: Path) -> None:
        # Non-hit variant: no rule-derived columns carry a real value, so only the
        # always-present structural columns are emitted.
        r = _result([_ann()])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert header == _expected_header(set())


class TestAaEffectsColumn:
    """The aa_effects column lists all amino-acid effects (single + combined)
    each with its amino-acid frequency in parentheses."""

    def test_combined_member_lists_single_and_combined_effects(self, tmp_path: Path) -> None:
        """A combined member row lists its single-exchange effect (when its
        amino-acid frequency is > 0) followed by every accepted combined state."""
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
            CodonState(alt_codon='ATG', alt_aa='M', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0,)),
        ]
        ann = _ann(
            is_combined_codon_event=True, combined_states=states,
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        # single_exchange_aa_freq defaults to allele_freq (0.95) > 0, so the single
        # K2M (0.95) is listed first, then the combined states.
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        assert 'aa_effects' in header
        col = header.index('aa_effects')
        assert len(rows) == 1
        effects = rows[0][col]
        # Single exchange first (at its amino-acid frequency = allele_freq 0.95).
        assert 'K2M | 0.95 |' in effects
        # Combined state I follows.
        assert 'K2I | 0.5 |' in effects
        # The combined state that also produces M is deduped (same AA as single).
        assert 'K2M | 0.5 |' not in effects
        assert effects.count('K2M') == 1

    def test_combined_member_zero_single_omits_single(self, tmp_path: Path) -> None:
        """A combined member whose single-exchange is Fréchet-impossible
        (single_exchange_aa_freq=0) omits the single entry; only combined states."""
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = _ann(
            is_combined_codon_event=True, combined_states=states,
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        ann.single_exchange_aa_freq = 0.0  # single guaranteed absent
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        effects = rows[0][col]
        # Single M is omitted (lower=0); only the combined I state.
        assert 'K2M' not in effects
        assert 'K2I | 0.5 |' in effects

    def test_single_snp_lists_only_single_effect(self, tmp_path: Path) -> None:
        """A single-SNP annotation lists only its single effect at the variant
        frequency (no combined states)."""
        ann = _ann(alt_aa='E', codon_pos=2, ref_aa='K')
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        # single_exchange_aa_freq == allele_freq (0.95); no combined states.
        # freq_method defaults to 'observed'.
        assert rows[0][col] == 'K3E | 0.95 | observed'


class TestFreqMethodLabel:
    """The aa_effects frequency is suffixed with `` (observed)`` or
    `` (lower bound)`` driven by ``ann.freq_method``."""

    def test_combined_member_estimated_label(self, tmp_path: Path) -> None:
        """A combined-codon member with freq_method='estimated' suffixes the
        frequency with `` (lower bound)``."""
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = _ann(
            is_combined_codon_event=True, combined_states=states,
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        ann.freq_method = 'estimated'
        ann.single_exchange_aa_freq = 0.0  # only combined state shown
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        assert 'K2I | 0.5 | lower bound' in rows[0][col]

    def test_combined_member_observed_label(self, tmp_path: Path) -> None:
        """A combined-codon member with freq_method='observed' (BAM mode)
        suffixes the frequency with `` (observed)``."""
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = _ann(
            is_combined_codon_event=True, combined_states=states,
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        ann.freq_method = 'observed'
        ann.single_exchange_aa_freq = 0.0
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        assert 'K2I | 0.5 | observed' in rows[0][col]

    def test_combined_state_with_zero_lower_not_shown(self, tmp_path: Path) -> None:
        """A combined state whose amino-acid frequency is 0.0 (Fréchet lower = 0
        or a BAM count that rounds to 0.0 at 3 decimals) must NOT be rendered in
        aa_effects, even when accepted=True. Mirrors the single-exchange gate
        (``single_exchange_aa_freq > 0.0``). Issue 1: zero-frequency states were
        shown as ``K2M | 0.0 | observed`` and matched rules."""
        states = [
            CodonState(alt_codon='ATG', alt_aa='M', lower=0.0, upper=0.0,
                       forced_fraction=1.0, accepted=True, member_indices=(0,)),
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = _ann(
            is_combined_codon_event=True, combined_states=states,
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        ann.freq_method = 'observed'
        ann.single_exchange_aa_freq = 0.0
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        effects = rows[0][col]
        assert 'K2M | 0.0' not in effects
        assert 'K2I | 0.5 | observed' in effects

    def test_single_exchange_below_display_precision_not_shown(self, tmp_path: Path) -> None:
        """A single-exchange whose amino-acid frequency is real but below the
        3-decimal display precision (e.g. 1 molecule in ~6000 -> 0.000165) must
        NOT be rendered as ``K2M | 0.0 | observed``. The raw value passes the
        ``> 0.0`` gate but ``round(0.000165, 3) == 0.0``; the display gate must
        use the rounded value so sub-precision frequencies are suppressed
        consistently with combined states. Issue 1 (rounding): the user saw
        ``L897L | 0.0 | observed`` from a 1-read observation."""
        ann = _ann(
            is_combined_codon_event=True, combined_states=[],
            alt_aa='M', codon_pos=1, ref_aa='K',
        )
        ann.freq_method = 'observed'
        ann.single_exchange_aa_freq = 0.000165  # real but rounds to 0.0 at 3 dp
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        effects = rows[0][col]
        assert 'K2M | 0.0' not in effects
        # The entry is suppressed entirely (nothing else to show).
        assert effects == ''

    def test_single_snp_observed_label_by_default(self, tmp_path: Path) -> None:
        """A single-SNP annotation with freq_method at its default ('observed')
        suffixes the frequency with `` (observed)``."""
        ann = _ann(alt_aa='E', codon_pos=2, ref_aa='K')
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        col = header.index('aa_effects')
        assert rows[0][col] == 'K3E | 0.95 | observed'


class TestSingleRuleRows:
    def test_two_drug_matches_produce_two_rows(self, tmp_path: Path) -> None:
        rule_a = _rule(rid=1, drug='Acyclovir', phenotype='resistant', ic50='>0.5',
                       fold_ic50='12.4', score='3.0', external_id='R1', source='LB2021',
                       publications=[_pub(doi='10.1/a')])
        rule_b = _rule(rid=2, drug='Foscarnet', phenotype='intermediate', ic50='2.1',
                       fold_ic50='1.8', score='1.0', external_id='R2', source='MK2019',
                       publications=[_pub(pubmed_id='99')])
        ann = _ann(rule_matches=[rule_a, rule_b])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        # Two rule rows for the one variant.
        rule_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert len(rule_rows) == 2
        drugs = {row[header.index('drug')] for row in rule_rows}
        assert drugs == {'Acyclovir', 'Foscarnet'}
        # Each row carries its own metrics.
        by_drug = {row[header.index('drug')]: row for row in rule_rows}
        acy = by_drug['Acyclovir']
        assert acy[header.index('phenotype')] == 'resistant'
        assert acy[header.index('ic50')] == '>0.5'
        assert acy[header.index('fold_ic50')] == '12.4'
        assert acy[header.index('score')] == '3.0'
        assert acy[header.index('publications')] == '10.1/a'
        fos = by_drug['Foscarnet']
        assert fos[header.index('publications')] == '99'  # pubmed_id fallback
        # Shared variant columns identical across both rows.
        assert acy[header.index('gene')] == fos[header.index('gene')] == 'gag'
        assert acy[header.index('aa_effects')] == fos[header.index('aa_effects')]
        assert acy[header.index('in_database')] == fos[header.index('in_database')] == 'yes'
        assert acy[header.index('strand')] == fos[header.index('strand')] == '+'

    def test_multiple_publications_joined_with_pipe(self, tmp_path: Path) -> None:
        # DOIs may contain ';' (e.g. structured suffixes), so publications are joined
        # with '|' — not ';' — to stay unambiguous. Regression for the separator.
        rule = _rule(
            rid=1, drug='Acyclovir', phenotype='resistant', external_id='R1',
            publications=[_pub(doi='10.1/a'), _pub(doi='10.2/b;suffix'), _pub(pubmed_id='77')],
        )
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        rule_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert len(rule_rows) == 1
        pubs = rule_rows[0][header.index('publications')]
        assert pubs == '10.1/a|10.2/b;suffix|77'
        # The ';' inside a DOI survives intact because '|' is the separator.
        assert ';' in pubs


class TestFormulaRows:
    def _two_member_formula(self) -> tuple[list[AnnotatedVariant], FormulaRuleHit]:
        member_a = _ann(chrom='ref', pos=3, af=0.88, feature='gag', codon_pos=2,
                        alt_aa='E', af_bin='high')
        member_b = _ann(chrom='ref', pos=9, af=0.97, feature='gag', codon_pos=4,
                        ref_aa='A', alt_aa='T', af_bin='high')
        rs = ResistanceRuleSet(
            id=10, drug_name='Brincidofovir', drug_id=1, phenotype='resistant',
            clinical_phenotype='resistant', ic50='>1.0', fold_ic50='15.0', score='4.0',
            source='LB2021', group_name='FR1', logic_expression='R1 AND R2',
            publications=[_pub(doi='10.1/comb')],
            members=[
                ResistanceRuleSetMember(
                    id=1, rule_set_id=10, feature_name='gag', feature_id=1,
                    reference_identifier='ref', position=2, reference='K',
                    mutation='E', external_id='R1',
                ),
                ResistanceRuleSetMember(
                    id=2, rule_set_id=10, feature_name='gag', feature_id=1,
                    reference_identifier='ref', position=4, reference='A',
                    mutation='T', external_id='R2',
                ),
            ],
        )
        hit = FormulaRuleHit(rule_set=rs, matched_variants=[member_a, member_b],
                             matched_member_ids=['R1', 'R2'])
        return [member_a, member_b], hit

    def test_formula_hit_emits_one_combined_row(self, tmp_path: Path) -> None:
        members, hit = self._two_member_formula()
        r = _result(members, formula_hits=[hit])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        formula_rows = [row for row in rows if row[header.index('rule_type')] == 'formula']
        assert len(formula_rows) == 1
        row = formula_rows[0]
        # Members joined with ';' in gene/nt_mut/aa_effects/strand.
        assert row[header.index('gene')] == 'gag;gag'
        assert ';' in row[header.index('nt_mut')]
        assert 'K3E' in row[header.index('aa_effects')]
        assert 'A5T' in row[header.index('aa_effects')]
        # aa_frequency is the formula hit's Fréchet lower bound (a single value, not
        # the members' joined variant allele frequencies).
        assert ';' not in row[header.index('aa_frequency')]
        assert row[header.index('strand')] == '+;+'
        # Metrics come from the combined rule set, not members.
        assert row[header.index('drug')] == 'Brincidofovir'
        assert row[header.index('phenotype')] == 'resistant'
        assert row[header.index('ic50')] == '>1.0'
        assert row[header.index('fold_ic50')] == '15.0'
        assert row[header.index('score')] == '4.0'
        assert row[header.index('publications')] == '10.1/comb'
        assert row[header.index('in_database')] == 'yes'

    def test_formula_member_only_variant_gets_member_row(self, tmp_path: Path) -> None:
        members, hit = self._two_member_formula()
        # Members have no single rule matches of their own.
        r = _result(members, formula_hits=[hit])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        member_rows = [row for row in rows if row[header.index('rule_type')] == 'formula-member']
        assert len(member_rows) == 2
        for row in member_rows:
            assert row[header.index('in_database')] == 'yes'
            assert row[header.index('drug')] == _PLACEHOLDER
            assert row[header.index('ic50')] == ''  # numeric empty


class TestNonHitAndFastaRows:
    def test_non_hit_variant_row(self, tmp_path: Path) -> None:
        ann = _ann(rule_matches=[])  # no rules
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        assert len(rows) == 1
        row = rows[0]
        assert row[header.index('in_database')] == 'no'
        assert row[header.index('rule_type')] == _PLACEHOLDER
        assert row[header.index('drug')] == _PLACEHOLDER
        # No real rule values anywhere -> conditional columns dropped entirely.
        assert header == _expected_header(set())
        for col in ('phenotype', 'clinical_phenotype', 'ic50', 'fold_ic50',
                    'score', 'publications'):
            assert col not in header

    def test_fasta_mode_omits_vcf_only_columns(self, tmp_path: Path) -> None:
        # reference, nt_mut_user, and depth are VCF-only columns. reference is the
        # matched internal reference name for multi-species VCF runs; nt_mut_user
        # is the VCF-coords-before-remap change; depth is read depth from
        # sequencing reads. In FASTA mode (consensus input, no reads) none is
        # meaningful, so all three columns are omitted entirely — mirroring the
        # HTML's has_user_ref_column (= not is_fasta_mode) and the multi-species
        # Reference column gate.
        ann = _ann(is_fasta_mode=True, af_bin='high')
        r = _result([ann], is_fasta_mode=True)
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        assert 'reference' not in header
        assert 'nt_mut_user' not in header
        assert 'depth' not in header
        assert 'external_id' not in header
        row = rows[0]
        assert row[header.index('strand')] == '+'
        # The remaining structural columns are still present.
        for col in ('gene', 'nt_mut', 'aa_effects', 'aa_frequency',
                    'consequence', 'in_database', 'rule_type', 'drug', 'source'):
            assert col in header

    def test_strand_taken_from_feature_record(self, tmp_path: Path) -> None:
        ann = _ann(feature='pol')
        r = _result([ann], features=[_feature('pol', strand='-')])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        assert rows[0][header.index('strand')] == '-'


class TestInsAnyWildcard:
    def test_ins_any_rule_row_emitted(self, tmp_path: Path) -> None:
        rule = _rule(rid=1, drug='Acyclovir', mutation='INS_any')
        ann = _ann(alt_aa='E', rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        row = [row for row in rows if row[header.index('rule_type')] == 'single'][0]
        # The aa_effects column carries the amino-acid change.
        assert 'K3E' in row[header.index('aa_effects')]


class TestCombinedCodonEvent:
    def test_combined_codon_uses_per_snp_nt_mut(self, tmp_path: Path) -> None:
        rule = _rule(rid=1, drug='Acyclovir')
        ann = _ann(is_combined_codon_event=True, ref_codon='AAA', alt_codon='GAA',
                   codon_pos=2, pos=3, ref='A', alt='G', rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        row = [row for row in rows if row[header.index('rule_type')] == 'single'][0]
        # Per-SNP combined rows report their own VCF NT change, not codon form.
        assert row[header.index('nt_mut')] == 'A4G'


class TestEffectAsResistantRows:
    """Metadata-only effect-as-resistant rows are emitted as rule_type=single."""

    def _result_with_frameshift(self) -> ProfilingResult:
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='NC_001806', pos=18, ref='C', alt='CA',
                                allele_freq=0.95, depth=200),
            feature_name='UL23', codon_pos=6, ref_aa='P', alt_aa='PfsX',
            consequence='frameshift', af_bin='high',
        )
        feat = FeatureRecord(
            id=1, reference_id=1, name='UL23', protein='UL23', start=0, end=60,
            strand='+', codon_start=0, nt_sequence='ATGAAAGCTTAA',
        )
        return ProfilingResult(
            project_name='T', organism='HSV-1', sample_name='S1', vcf_name='in.vcf',
            total_variants=1, variants_in_cds=1, resistance_hits=0,
            annotations=[ann],
            references=[
                ReferenceGroup(
                    reference_name='NC_001806', reference_id=1, organism='HSV-1',
                    reference_length_nt=1000, query_name='NC_001806',
                    query_sequence='ATGAAAGCTTAA', features=[feat],
                ),
            ],
        )

    def _project_conn(self) -> sqlite3.Connection:
        import json as _json
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute(
            'CREATE TABLE interpretation_algorithm '
            '(id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)'
        )
        conn.execute('CREATE TABLE resistance_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute('CREATE TABLE resistance_formula_rule (phenotype TEXT, clinical_phenotype TEXT)')
        conn.execute(
            'INSERT INTO resistance_rule (phenotype, clinical_phenotype) VALUES (?, ?)',
            ('resistant', 'unknown'),
        )
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            (
                'effect_as_resistant',
                _json.dumps({
                    'name': 'effect_as_resistant',
                    'rules': [
                        {
                            'feature': 'UL23',
                            'effect': ['frameshift'],
                            'reference': 'NC_001806',
                            'drug': 'Aciclovir',
                        }
                    ],
                }),
            ),
        )
        conn.commit()
        return conn

    def test_effect_as_resistant_emits_single_row(self, tmp_path: Path) -> None:
        result = self._result_with_frameshift()
        conn = self._project_conn()
        out = tmp_path / 'r.results.tsv'
        write_tsv(result, out, project_conn=conn)
        header, rows = _read_tsv(out)
        # One non-hit variant row + one effect-as-resistant single row.
        single_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert len(single_rows) == 1
        row = single_rows[0]
        assert row[header.index('drug')] == 'Aciclovir'
        assert row[header.index('phenotype')] == 'resistant'
        assert row[header.index('source')] == 'Metadata algorithm'
        assert row[header.index('in_database')] == 'yes'
        assert row[header.index('gene')] == 'UL23'
        assert 'P7PfsX' in row[header.index('aa_effects')]
        # Effect-as-resistant rows carry no numeric metrics or clinical
        # phenotype, so those conditional columns are dropped (no real value in any
        # row). phenotype is kept because phenotype='resistant' is a real value.
        assert 'ic50' not in header
        assert 'fold_ic50' not in header
        assert 'score' not in header
        assert 'publications' not in header
        assert 'clinical_phenotype' not in header  # never a real value here

    def test_no_effect_as_resistant_rows_without_project_conn(self, tmp_path: Path) -> None:
        result = self._result_with_frameshift()
        out = tmp_path / 'r.results.tsv'
        write_tsv(result, out, project_conn=None)
        header, rows = _read_tsv(out)
        # Only the non-hit variant row; no single rows.
        single_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert single_rows == []


class TestExportResultsIntegration:
    def test_export_results_writes_tsv_and_returns_path(self, tmp_path: Path) -> None:
        # Default rule: phenotype='resistant' (real), no numeric metrics/publications.
        r = _result([_ann(rule_matches=[_rule(rid=1, drug='Acyclovir')])])
        html_path = tmp_path / 'sample.report.html'
        outputs = export_results(r, html_path.parent, output_html_path=html_path,
                                 extra_export_formats={'tsv'})
        assert 'tsv' in outputs
        tsv_path = outputs['tsv']
        assert tsv_path.name == 'sample.results.tsv'
        assert tsv_path.exists()
        header, rows = _read_tsv(tsv_path)
        # phenotype='resistant' kept; clinical_phenotype='unknown' and all
        # numeric/publications columns dropped (no real value in any row).
        assert header == _expected_header({'phenotype'})
        assert len(rows) == 1

    def test_unknown_format_still_raises(self, tmp_path: Path) -> None:
        r = _result([_ann()])
        html_path = tmp_path / 'sample.report.html'
        with pytest.raises(ValueError, match='Unsupported export format'):
            export_results(r, html_path.parent, output_html_path=html_path,
                           extra_export_formats={'xml'})

    def test_tsv_no_longer_raises(self, tmp_path: Path) -> None:
        r = _result([_ann()])
        html_path = tmp_path / 'sample.report.html'
        # Should not raise.
        export_results(r, html_path.parent, output_html_path=html_path,
                       extra_export_formats={'tsv'})


class TestCliExportTsv:
    """End-to-end CLI integration for ``respro vcf --export tsv``."""

    def test_profile_vcf_writes_optional_tsv_export(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        from respro.cli.main import app

        output_dir = tmp_path / 'results_tsv'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--export', 'tsv',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code == 0, result.output

        tsv_path = output_dir / f'{sample_vcf.stem}.results.tsv'
        assert tsv_path.exists()
        header, rows = _read_tsv(tsv_path)
        # Header is a subset of the canonical columns in canonical order, and always
        # contains the structural columns. Conditional columns depend on the fixture
        # data (the conftest rule carries only phenotype='resistant').
        assert header == [c for c in TSV_COLUMNS if c in header]
        for col in _TSV_ALWAYS_COLUMNS:
            assert col in header, f'{col} should always be present'

    def test_profile_vcf_rejects_unknown_export_format(
        self,
        project_db: Path,
        sample_vcf: Path,
        sample_ref_fasta: Path,
        tmp_path: Path,
    ) -> None:
        from typer.testing import CliRunner

        from respro.cli.main import app

        output_dir = tmp_path / 'results_bad'
        runner = CliRunner()
        result = runner.invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(sample_vcf),
            '--ref-fasta', str(sample_ref_fasta),
            '--output', str(output_dir),
            '--export', 'xml',
            '--min-af', '0.01',
            '--min-depth', '0',
        ])
        assert result.exit_code != 0
        assert 'json, pdf, tsv' in result.output


def _mat_peptide_feature(name: str, protein: str, strand: str = '+') -> FeatureRecord:
    """A mat_peptide feature whose display_name is its protein (not its name)."""
    return FeatureRecord(
        id=1, reference_id=1, name=name, protein=protein,
        start=0, end=12, strand=strand, codon_start=0, nt_sequence='ATGAAAGCTTAA',
        feature_type='mat_peptide',
    )


class TestDisplayNamesForwarding:
    """The TSV gene column must reflect configured feature display names.

    A mat_peptide feature's display name is its protein (not its name); the HTML
    Database Hits / All Mutations tables show the protein, so the TSV must too.
    """

    def test_write_tsv_applies_display_names_to_gene(self, tmp_path: Path) -> None:
        # Feature name 'UL27' but display name (protein) 'ICP8'.
        feat = _mat_peptide_feature('UL27', 'ICP8')
        rule = _rule(rid=1, drug='Acyclovir')
        rule.feature_name = 'UL27'
        ann = _ann(feature='UL27', rule_matches=[rule])
        r = _result([ann], features=[feat])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out, display_names={'UL27': 'ICP8'})
        header, rows = _read_tsv(out)
        single_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert len(single_rows) == 1
        assert single_rows[0][header.index('gene')] == 'ICP8'

    def test_export_results_forwards_display_names_to_tsv(self, tmp_path: Path) -> None:
        # export_results has `features` available and must build + forward display_names.
        feat = _mat_peptide_feature('UL27', 'ICP8')
        rule = _rule(rid=1, drug='Acyclovir')
        rule.feature_name = 'UL27'
        ann = _ann(feature='UL27', rule_matches=[rule])
        r = _result([ann], features=[feat])
        html_path = tmp_path / 'sample.report.html'
        outputs = export_results(r, html_path.parent, features=[feat],
                                  output_html_path=html_path,
                                  extra_export_formats={'tsv'})
        tsv_path = outputs['tsv']
        header, rows = _read_tsv(tsv_path)
        single_rows = [row for row in rows if row[header.index('rule_type')] == 'single']
        assert len(single_rows) == 1
        # The gene column must show the display name (ICP8), not the raw feature (UL27).
        assert single_rows[0][header.index('gene')] == 'ICP8'


class TestEmptyColumnDropping:
    """Columns that carry no real value in any row are dropped, mirroring the
    HTML Database Hits table's has_*_metrics flags (a column is present only
    when some row has a non-empty, non-'unknown' value for it)."""

    def test_clinical_phenotype_dropped_when_only_phenotype_real(self, tmp_path: Path) -> None:
        # clinical_phenotype='unknown' but phenotype='resistant': the HTML gates the
        # two via independent has_*_metrics flags, so clinical_phenotype is dropped
        # when no row carries a real (non-unknown) clinical phenotype.
        rule = _rule(rid=1, drug='Acyclovir', phenotype='resistant', clinical='unknown')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert 'phenotype' in header
        assert 'clinical_phenotype' not in header

    def test_real_clinical_phenotype_is_kept(self, tmp_path: Path) -> None:
        rule = _rule(rid=1, drug='Acyclovir', clinical='resistant')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert 'clinical_phenotype' in header

    def test_empty_numeric_columns_dropped(self, tmp_path: Path) -> None:
        # No rule carries ic50/fold_ic50/score.
        rule = _rule(rid=1, drug='Acyclovir', ic50='', fold_ic50='', score='')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        for col in ('ic50', 'fold_ic50', 'score'):
            assert col not in header, f'{col} should be dropped when always empty'

    def test_populated_numeric_columns_kept(self, tmp_path: Path) -> None:
        rule = _rule(rid=1, drug='Acyclovir', ic50='>0.5', fold_ic50='12.0',
                     score='3.0')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        for col in ('ic50', 'fold_ic50', 'score'):
            assert col in header, f'{col} should be kept when populated'

    def test_empty_publications_column_dropped(self, tmp_path: Path) -> None:
        rule = _rule(rid=1, drug='Acyclovir', publications=[])
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert 'publications' not in header

    def test_structural_columns_always_kept(self, tmp_path: Path) -> None:
        # Even a non-hit variant keeps all per-variant structural columns.
        ann = _ann(rule_matches=[])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        for col in ('reference', 'gene', 'nt_mut', 'strand', 'aa_frequency',
                    'depth', 'consequence', 'aa_effects', 'in_database', 'rule_type',
                    'drug', 'source'):
            assert col in header, f'{col} should always be present'

    def test_phenotype_dropped_when_only_clinical_phenotype_real(self, tmp_path: Path) -> None:
        # phenotype='unknown' but clinical_phenotype='resistant': the HTML gates the
        # two via independent has_*_metrics flags, so phenotype is dropped when no
        # row carries a real (non-unknown) phenotype.
        rule = _rule(rid=1, drug='Acyclovir', phenotype='unknown', clinical='resistant')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert 'phenotype' not in header
        assert 'clinical_phenotype' in header

    def test_both_phenotype_columns_dropped_when_all_unknown(self, tmp_path: Path) -> None:
        # phenotype='unknown', clinical_phenotype='unknown' -> neither is a real value.
        rule = _rule(rid=1, drug='Acyclovir', phenotype='unknown', clinical='unknown')
        ann = _ann(rule_matches=[rule])
        r = _result([ann])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, _ = _read_tsv(out)
        assert 'phenotype' not in header
        assert 'clinical_phenotype' not in header


class TestFormulaFrechetLowerTsv:
    """TSV formula rows report the Fréchet lower bound as the hit frequency."""

    def test_formula_row_af_is_frechet_lower(self, tmp_path: Path) -> None:
        """The aa_frequency cell of a formula row is the Fréchet lower bound, not the
        members' variant allele frequencies."""
        member_a = _ann(chrom='ref', pos=3, af=0.88, feature='gag', codon_pos=2,
                        alt_aa='E', af_bin='high')
        member_b = _ann(chrom='ref', pos=9, af=0.97, feature='gag', codon_pos=4,
                        ref_aa='A', alt_aa='T', af_bin='high')
        rs = ResistanceRuleSet(
            id=10, drug_name='Brincidofovir', drug_id=1, phenotype='resistant',
            group_name='FR1', logic_expression='R1 AND R2',
            members=[
                ResistanceRuleSetMember(
                    id=1, rule_set_id=10, feature_name='gag', feature_id=1,
                    reference_identifier='ref', position=2, reference='K',
                    mutation='E', external_id='R1',
                ),
                ResistanceRuleSetMember(
                    id=2, rule_set_id=10, feature_name='gag', feature_id=1,
                    reference_identifier='ref', position=4, reference='A',
                    mutation='T', external_id='R2',
                ),
            ],
        )
        hit = FormulaRuleHit(
            rule_set=rs, matched_variants=[member_a, member_b],
            matched_member_ids=['R1', 'R2'],
            frechet_lower=0.6, forced_fraction=0.9, member_count=2,
        )
        r = _result([member_a, member_b], formula_hits=[hit])
        out = tmp_path / 'r.results.tsv'
        write_tsv(r, out)
        header, rows = _read_tsv(out)
        formula_rows = [row for row in rows if row[header.index('rule_type')] == 'formula']
        assert len(formula_rows) == 1
        assert formula_rows[0][header.index('aa_frequency')] == '0.6'
