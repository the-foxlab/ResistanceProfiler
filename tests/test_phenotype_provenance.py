"""Phenotype label provenance: verbatim database labels survive assessment and report.

Covers the phenotype-label-provenance feature:
- drug aggregation tracks verbatim phenotype labels per rank (P1),
- ``by_phenotype`` assessments return the database-provided label instead of the
  canonical fallback label (P2),
- report phenotype columns display the verbatim labels (P3),
- the summary narrative lead sentence wording (P4).
"""

from __future__ import annotations

import json
import sqlite3

from conftest import make_profiling_result

from respro.db.algorithms import compute_drug_assessment
from respro.db.models import (
    AnnotatedVariant,
    ProfilingResult,
    ResistanceRule,
    VariantCall,
)
from respro.db.phenotype_ranks import most_frequent_label
from respro.report.html import build_report_context

# ─── Helpers ──────────────────────────────────────────────────────────

def _result_with_phenotypes(phenotypes: list[str], drug_name: str = 'DrugA',
                            feature_matches: list | None = None) -> ProfilingResult:
    """A single-reference result with one rule hit per phenotype label.

    Each hit sits at its own codon position on the same feature/drug so the
    drug table aggregates them into one entry.
    """
    annotations = []
    for i, phenotype in enumerate(phenotypes, start=1):
        rule = ResistanceRule(
            id=i, feature_name='gag', feature_id=1,
            drug_name=drug_name, drug_id=1,
            reference_identifier='ref',
            position=i, reference='K', mutation='E',
            phenotype=phenotype,
        )
        annotations.append(AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=i + 1, ref='A', alt='G',
                                allele_freq=0.95, depth=500),
            feature_name='gag', codon_pos=i, ref_aa='K', alt_aa='E',
            consequence='missense', af_bin='high',
            rule_matches=[rule],
        ))
    return make_profiling_result(
        project_name='T', organism='test',
        reference_name='ref', reference_length_nt=1000,
        total_variants=len(annotations), variants_in_cds=len(annotations),
        resistance_hits=len(annotations),
        annotations=annotations,
        feature_matches=feature_matches or [],
    )


def _feature_matches(count: int) -> list:
    """One feature match per hit so the narrative lists profiled features."""
    from respro.db.models import FeatureMatch, FeatureRecord

    feat = FeatureRecord(
        id=1, reference_id=1, name='gag', protein='Gag',
        start=0, end=12, strand='+', codon_start=0,
        nt_sequence='ATGAAAGCTTAA',
    )
    return [FeatureMatch(
        feature=feat, identity=1.0, cds_coverage=1.0, query_coverage=1.0,
        query_start=0, query_end=12, strand='+', cigar='12M', cds_start=0,
    )] * count


def _algorithm_conn(configs: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        'CREATE TABLE interpretation_algorithm '
        '(id INTEGER PRIMARY KEY AUTOINCREMENT, algorithm_name TEXT, config_json TEXT)'
    )
    for config in configs:
        conn.execute(
            'INSERT INTO interpretation_algorithm (algorithm_name, config_json) VALUES (?, ?)',
            ('drug_interpretation', json.dumps(config)),
        )
    conn.commit()
    return conn


def _drug_data(rank_label_counts: dict[int, dict[str, int]],
               hit_count: int | None = None) -> dict:
    """Build the drug_data payload for compute_drug_assessment."""
    rank_counts = {rank: sum(labels.values()) for rank, labels in rank_label_counts.items()}
    return {
        'rank_counts': rank_counts,
        'rank_label_counts': rank_label_counts,
        'score_total': 0.0,
        'ic50_values': [],
        'fold_ic50_values': [],
        'hit_count': hit_count if hit_count is not None else sum(rank_counts.values()),
    }


# ─── P1: verbatim label tracking during aggregation ───────────────────

class TestRankLabelCountsAggregation:
    """Drug aggregation records verbatim phenotype labels per rank."""

    def test_rank_label_counts_tracks_verbatim_labels(self) -> None:
        """Hits storing 'sensitive' expose the verbatim label, not 'susceptible'."""
        result = _result_with_phenotypes(['sensitive', 'sensitive', 'resistant'])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        row = ctx['summary']['drug_table']['rows'][0]
        assert row['rank_label_counts'] == {
            1: {'sensitive': 2},
            5: {'resistant': 1},
        }
        # rank_counts semantics unchanged.
        assert row['rank_counts'] == {1: 2, 5: 1}

    def test_rank_label_counts_tracks_mixed_same_rank_labels(self) -> None:
        """Two distinct labels sharing a rank are both recorded with counts."""
        result = _result_with_phenotypes(['sensitive', 'normal'])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        row = ctx['summary']['drug_table']['rows'][0]
        assert row['rank_label_counts'] == {1: {'sensitive': 1, 'normal': 1}}
        assert row['rank_counts'] == {1: 2}


# ─── P2: by_phenotype returns verbatim labels ─────────────────────────

class TestByPhenotypeVerbatimLabels:
    """by_phenotype assessments preserve database-provided labels."""

    def test_rank5_verbatim_label(self) -> None:
        """A DB saying 'high-level resistance' assesses as 'high-level resistance'."""
        data = _drug_data({5: {'high-level resistance': 1}})
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'high-level resistance'

    def test_most_frequent_label_wins_at_rank(self) -> None:
        """A 2×'high-level resistance' / 1×'resistant' split assesses as the majority."""
        data = _drug_data({5: {'high-level resistance': 2, 'resistant': 1}})
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'high-level resistance'

    def test_tie_breaks_alphabetically(self) -> None:
        """An equal split picks the alphabetically first label, deterministically."""
        data = _drug_data({5: {'resistant': 1, 'high-level resistance': 1}})
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'high-level resistance'

    def test_rank1_verbatim_label(self) -> None:
        """A DB saying 'sensitive' assesses susceptible hits as 'sensitive'."""
        data = _drug_data({1: {'sensitive': 2}})
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'sensitive'

    def test_contradictory_uses_verbatim_label(self) -> None:
        """A DB saying 'conflicting' surfaces that label, not 'contradictory'."""
        data = _drug_data({-1: {'conflicting': 1}})
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'conflicting'

    def test_without_label_counts_falls_back_to_canonical(self) -> None:
        """Drug data without rank_label_counts (older callers) keeps canonical labels."""
        data = {
            'rank_counts': {5: 1},
            'score_total': 0.0, 'ic50_values': [], 'fold_ic50_values': [],
            'hit_count': 1,
        }
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'resistant'

    def test_hits_without_labels_default_to_canonical_susceptible(self) -> None:
        """Hits carrying no rank-mappable label keep the canonical default."""
        data = _drug_data({}, hit_count=1)
        assessment, _ = compute_drug_assessment(data, [{'method': 'by_phenotype'}])
        assert assessment == 'susceptible'

    def test_deterministic_across_dict_insertion_order(self) -> None:
        """Label choice is independent of dict insertion order (PYTHONHASHSEED)."""
        data_a = _drug_data({5: {'resistant': 2, 'high-level resistance': 2}})
        data_b = _drug_data({5: {'high-level resistance': 2, 'resistant': 2}})
        assessment_a, _ = compute_drug_assessment(data_a, [{'method': 'by_phenotype'}])
        assessment_b, _ = compute_drug_assessment(data_b, [{'method': 'by_phenotype'}])
        assert assessment_a == assessment_b == 'high-level resistance'

    def test_mixed_methods_merge_by_rank(self) -> None:
        """Strongest-wins merge stays rank-based with verbatim labels in play."""
        data = _drug_data({1: {'sensitive': 1}})
        data['score_total'] = 20.0
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'susceptible': 0, 'resistant': 10}},
        ]
        final, methods = compute_drug_assessment(data, configs)
        assert final == 'resistant'
        assert methods[0]['assessment'] == 'sensitive'
        assert methods[1]['assessment'] == 'resistant'

    def test_mixed_methods_verbatim_beats_weaker_method(self) -> None:
        """A verbatim rank-5 label wins over a rank-1 configured label."""
        data = _drug_data({5: {'high-level resistance': 1}})
        configs = [
            {'method': 'by_phenotype'},
            {'method': 'by_score', 'thresholds': {'susceptible': 0, 'resistant': 10}},
        ]
        final, methods = compute_drug_assessment(data, configs)
        assert final == 'high-level resistance'
        assert methods[1]['assessment'] == 'susceptible'


class TestMostFrequentLabelHelper:
    """The shared most-frequent-label selection helper."""

    def test_highest_count_wins(self) -> None:
        assert most_frequent_label({'a': 1, 'b': 2}) == 'b'

    def test_tie_breaks_alphabetically(self) -> None:
        assert most_frequent_label({'b': 1, 'a': 1}) == 'a'

    def test_empty_returns_none(self) -> None:
        assert most_frequent_label({}) is None


# ─── P3: report phenotype columns display verbatim labels ────────────

class TestPhenotypeColumnProvenance:
    """Phenotype count column headers use verbatim database labels."""

    def test_column_header_uses_verbatim_label(self) -> None:
        """A 'sensitive'-vocabulary DB shows a Sensitive column, not Susceptible."""
        result = _result_with_phenotypes(['sensitive', 'sensitive'])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        columns = ctx['summary']['drug_table']['phenotype_columns']
        assert len(columns) == 1
        assert columns[0]['rank'] == 1
        assert columns[0]['label'] == 'Sensitive'
        # Badge class stays rank-derived, so colouring is unchanged.
        assert columns[0]['badge_class'] == 'phenotype--susceptible'

    def test_column_header_most_frequent_across_drugs(self) -> None:
        """The header label is the most frequent verbatim label across all drugs."""
        annotations = []
        plan = [('DrugA', 'sensitive'), ('DrugA', 'sensitive'), ('DrugB', 'normal')]
        for i, (drug, phenotype) in enumerate(plan, start=1):
            rule = ResistanceRule(
                id=i, feature_name='gag', feature_id=1,
                drug_name=drug, drug_id=i,
                reference_identifier='ref',
                position=i, reference='K', mutation='E',
                phenotype=phenotype,
            )
            annotations.append(AnnotatedVariant(
                variant=VariantCall(chrom='ref', pos=i + 1, ref='A', alt='G',
                                    allele_freq=0.95, depth=500),
                feature_name='gag', codon_pos=i, ref_aa='K', alt_aa='E',
                consequence='missense', af_bin='high',
                rule_matches=[rule],
            ))
        result = make_profiling_result(
            project_name='T', organism='test',
            reference_name='ref', reference_length_nt=1000,
            total_variants=len(annotations), variants_in_cds=len(annotations),
            resistance_hits=len(annotations),
            annotations=annotations,
        )
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0)
        columns = ctx['summary']['drug_table']['phenotype_columns']
        assert [c['label'] for c in columns] == ['Sensitive']


# ─── P4: narrative lead sentence ──────────────────────────────────────

class TestNarrativeLeadSentence:
    """The summary narrative lead sentence wording."""

    def test_single_species_lead_sentence(self) -> None:
        """The lead attributes the tested sequence information to the organism."""
        from respro.db.models import FeatureMatch, FeatureRecord

        feat = FeatureRecord(
            id=1, reference_id=1, name='gag', protein='Gag',
            start=0, end=12, strand='+', codon_start=0,
            nt_sequence='ATGAAAGCTTAA',
        )
        result = _result_with_phenotypes(['resistant'], feature_matches=[FeatureMatch(
            feature=feat, identity=1.0, cds_coverage=1.0, query_coverage=1.0,
            query_start=0, query_end=12, strand='+', cigar='12M', cds_start=0,
        )])
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        assert text.startswith(
            'The tested sequence information mapped to gag of <strong>test</strong> '
            'and was evaluated against known resistance-associated mutations for 1 drug.'
        )

    def test_multi_species_lead_sentence(self) -> None:
        """Multi-species leads attribute each organism's features with the same lead-in."""
        from respro.db.models import FeatureMatch, FeatureRecord, ReferenceGroup

        def _ref(name, ref_id, organism, feature_name, drug):
            rule = ResistanceRule(
                id=ref_id, feature_name=feature_name, feature_id=ref_id,
                drug_name=drug, drug_id=ref_id,
                reference_identifier=name, position=2, reference='K', mutation='E',
                phenotype='resistant',
            )
            feat = FeatureRecord(
                id=ref_id, reference_id=ref_id, name=feature_name,
                protein=feature_name, start=0, end=12, strand='+',
                codon_start=0, nt_sequence='ATGAAAGCTTAA',
            )
            ann = AnnotatedVariant(
                variant=VariantCall(chrom=name, pos=3, ref='A', alt='G',
                                    allele_freq=0.95, depth=500),
                feature_name=feature_name, codon_pos=2, ref_aa='K', alt_aa='E',
                consequence='missense', af_bin='high',
                rule_matches=[rule],
            )
            return ReferenceGroup(
                reference_name=name, reference_id=ref_id, organism=organism,
                reference_length_nt=1000, query_name=name,
                query_sequence='ATGAAAGCTTAA',
                features=[feat], rule_feature_names={feature_name},
                feature_matches=[FeatureMatch(
                    feature=feat, identity=1.0, cds_coverage=1.0,
                    query_coverage=1.0, query_start=0, query_end=12,
                    strand='+', cigar='12M', cds_start=0,
                )],
            ), ann

        ref_a, ann_a = _ref('refA', 1, 'Organism A', 'gagA', 'DrugA')
        ref_b, ann_b = _ref('refB', 2, 'Organism B', 'polB', 'DrugB')
        result = ProfilingResult(
            project_name='Multi', organism='Organism A',
            sample_name='samp', vcf_name='in.vcf',
            total_variants=2, variants_in_cds=2, resistance_hits=2,
            annotations=[ann_a, ann_b],
            references=[ref_a, ref_b],
        )
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        assert ctx['is_multi_species'] is True
        text = ctx['summary']['narrative']
        assert text.startswith(
            'The tested sequence information mapped to gagA of <strong>Organism A</strong> '
            'and polB of <strong>Organism B</strong> and was evaluated against '
            'known resistance-associated mutations'
        )

    def test_no_features_fallback_lead_sentence(self) -> None:
        """Without profiled features the lead uses the no-features fallback."""
        result = make_profiling_result(
            project_name='T', organism='test',
            reference_name='ref', reference_length_nt=1000,
            total_variants=0, variants_in_cds=0, resistance_hits=0,
            annotations=[],
        )
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        assert text.startswith(
            'The tested sequence information of <strong>test</strong> '
            'mapped to no profiled features and was evaluated, '
            'but no in-scope drugs were available for interpretation.'
        )


# ─── R2: narrative findings removal + verbatim list sections ──────────

class TestNarrativeProvenanceLists:
    """The narrative lead carries no findings clause and the list sections
    use verbatim assessment labels."""

    def test_lead_has_no_findings_clause(self) -> None:
        """The mixed-categories lead ends after the evaluation clause."""
        result = _result_with_phenotypes(
            ['resistant', 'resistant', 'sensitive', 'sensitive', 'sensitive'],
            feature_matches=_feature_matches(5),
        )
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        assert 'The assessment found' not in text
        assert text.startswith(
            'The tested sequence information mapped to gag of <strong>test</strong> '
            'and was evaluated against known resistance-associated mutations for 1 drug.'
        )

    def test_list_sections_use_verbatim_labels(self) -> None:
        """List section titles carry the database's own labels."""
        result = _result_with_phenotypes(
            ['sensitive', 'sensitive', 'high-level resistance'],
            feature_matches=_feature_matches(3),
        )
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        assert 'Drugs assessed as high-level resistance:' in text
        assert 'Drugs assessed as sensitive:' in text
        # Canonical round-trip labels must not appear as section titles.
        assert 'Drugs assessed as resistant:' not in text
        assert 'Drugs assessed as susceptible:' not in text

    def test_list_sections_ordered_most_severe_first(self) -> None:
        """Sections appear in descending severity order of their labels."""
        result = _result_with_phenotypes(
            ['sensitive', 'low-level resistance', 'high-level resistance'],
            feature_matches=_feature_matches(3),
        )
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        hi = text.index('Drugs assessed as high-level resistance:')
        lo = text.index('Drugs assessed as low-level resistance:')
        sens = text.index('Drugs assessed as sensitive:')
        assert hi < lo < sens

    def test_contradictory_list_section_uses_verbatim_label(self) -> None:
        """A contradictory assessment surfaces under the uniform verbatim title."""
        result = _result_with_phenotypes(['contradictory'], feature_matches=_feature_matches(1))
        conn = _algorithm_conn([{'name': 'drug_interpretation', 'method': 'by_phenotype'}])
        ctx = build_report_context(result, similarity_high=1, similarity_moderate=0,
                                   project_conn=conn)
        text = ctx['summary']['narrative']
        assert 'Drugs assessed as contradictory:' in text
        assert 'DrugA' in text
        from respro.db.phenotype_ranks import RANK_CONTRADICTORY, rank_to_colour
        assert rank_to_colour(RANK_CONTRADICTORY) in text
