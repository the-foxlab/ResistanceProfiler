"""
Tests for configuration loading and validation.

Covers: respro/config/cli_settings.py
- _load_cli_config()
- All dataclass configuration types
- defaults.toml structure validation
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from urllib.parse import urlparse

import pytest

from respro.config.cli_settings import (
    CliConfig,
    _load_cli_config,
)


class TestLoadCliConfig:
    """Tests for _load_cli_config()."""

    def test_loads_without_error(self):
        """Should load configuration without errors."""
        config = _load_cli_config()
        assert isinstance(config, CliConfig)

    def test_returns_cli_config_instance(self):
        """Should return CliConfig dataclass."""
        config = _load_cli_config()
        assert hasattr(config, 'timeouts')
        assert hasattr(config, 'urls')
        assert hasattr(config, 'parsing')
        assert hasattr(config, 'matching')
        assert hasattr(config, 'similarity')
        assert hasattr(config, 'af_bins')
        assert hasattr(config, 'af_bins_fasta')
        assert hasattr(config, 'alignment')

    def test_all_sections_populated(self):
        """Should populate all configuration sections."""
        config = _load_cli_config()
        assert config.timeouts is not None
        assert config.urls is not None
        assert config.parsing is not None
        assert config.matching is not None
        assert config.similarity is not None
        assert config.af_bins is not None
        assert config.af_bins_fasta is not None
        assert config.alignment is not None


class TestCliTimeoutConfig:
    """Tests for CliTimeoutConfig."""

    def test_pubchem_timeout_positive(self):
        """Should have positive pubchem timeout."""
        config = _load_cli_config()
        assert config.timeouts.pubchem > 0
        assert isinstance(config.timeouts.pubchem, int)

    def test_pubmed_timeout_positive(self):
        """Should have positive pubmed timeout."""
        config = _load_cli_config()
        assert config.timeouts.pubmed > 0
        assert isinstance(config.timeouts.pubmed, int)

    def test_crossref_timeout_positive(self):
        """Should have positive crossref timeout."""
        config = _load_cli_config()
        assert config.timeouts.crossref > 0
        assert isinstance(config.timeouts.crossref, int)


class TestCliUrlConfig:
    """Tests for CliUrlConfig."""

    def test_pubchem_urls_have_placeholders(self):
        """Should have {cid} placeholder in pubchem URLs."""
        config = _load_cli_config()
        assert '{cid}' in config.urls.pubchem_compound_page
        assert '{cid}' in config.urls.pubchem_structure_png
        assert '{name}' in config.urls.pubchem_cid_lookup

    def test_ncbi_urls_have_placeholders(self):
        """Should have {pmid}/{identifier} placeholders in NCBI URLs."""
        config = _load_cli_config()
        assert '{pmid}' in config.urls.ncbi_pubmed_esummary
        assert '{identifier}' in config.urls.ncbi_pmc_idconv
        assert '{accession}' in config.urls.ncbi_nuccore_efetch

    def test_crossref_url_has_placeholder(self):
        """Should have {doi} placeholder in CrossRef URL."""
        config = _load_cli_config()
        assert '{doi}' in config.urls.crossref_works

    def test_protein_url_has_placeholder(self):
        """Should have {protein_id} placeholder in protein URL."""
        config = _load_cli_config()
        assert '{protein_id}' in config.urls.ncbi_protein_page

    def test_github_raw_url_present(self):
        """Should have GitHub raw URL."""
        config = _load_cli_config()
        parsed = urlparse(config.urls.github_respro_db_raw)
        host = parsed.hostname
        assert host is not None
        assert host == 'githubusercontent.com' or host.endswith('.githubusercontent.com')

    def test_all_urls_are_strings(self):
        """Should all be string type."""
        config = _load_cli_config()
        for field_name in dir(config.urls):
            if not field_name.startswith('_'):
                value = getattr(config.urls, field_name)
                assert isinstance(value, str)


class TestCliParsingConfig:
    """Tests for CliParsingConfig."""

    def test_doi_prefixes_is_tuple(self):
        """Should have DOI prefixes as tuple."""
        config = _load_cli_config()
        assert isinstance(config.parsing.doi_prefixes, tuple)

    def test_doi_prefixes_not_empty(self):
        """Should have at least one DOI prefix."""
        config = _load_cli_config()
        assert len(config.parsing.doi_prefixes) > 0

    def test_doi_prefixes_are_strings(self):
        """Should all be strings."""
        config = _load_cli_config()
        for prefix in config.parsing.doi_prefixes:
            assert isinstance(prefix, str)


class TestCliMatchingConfig:
    """Tests for CliMatchingConfig."""

    def test_af_threshold_is_float(self):
        """Should have float AF threshold."""
        config = _load_cli_config()
        assert isinstance(config.matching.combination_member_af_threshold, float)

    def test_af_threshold_in_valid_range(self):
        """Should be between 0 and 1."""
        config = _load_cli_config()
        assert 0 < config.matching.combination_member_af_threshold < 1


class TestCliSimilarityConfig:
    """Tests for CliSimilarityConfig."""

    def test_high_threshold_positive(self):
        """Should have positive high threshold."""
        config = _load_cli_config()
        assert config.similarity.high > 0
        assert isinstance(config.similarity.high, int)

    def test_moderate_threshold_non_negative(self):
        """Should have non-negative moderate threshold."""
        config = _load_cli_config()
        assert config.similarity.moderate >= 0
        assert isinstance(config.similarity.moderate, int)

    def test_high_greater_than_moderate(self):
        """High threshold should be greater than moderate."""
        config = _load_cli_config()
        assert config.similarity.high > config.similarity.moderate


class TestCliAfBinsConfig:
    """Tests for CliAfBinsConfig."""

    def test_high_bin_valid_range(self):
        """High bin should be in valid range."""
        config = _load_cli_config()
        high = config.af_bins.high
        assert 0 <= high[0] <= high[1] <= 1

    def test_intermediate_bin_valid_range(self):
        """Intermediate bin should be in valid range."""
        config = _load_cli_config()
        intermediate = config.af_bins.intermediate
        assert 0 <= intermediate[0] <= intermediate[1] <= 1

    def test_low_bin_valid_range(self):
        """Low bin should be in valid range."""
        config = _load_cli_config()
        low = config.af_bins.low
        assert 0 <= low[0] <= low[1] <= 1

    def test_bins_dont_overlap(self):
        """Bins should not overlap."""
        config = _load_cli_config()
        assert config.af_bins.low[1] < config.af_bins.intermediate[0]
        assert config.af_bins.intermediate[1] < config.af_bins.high[0]

    def test_as_dict_method(self):
        """Should return dict from as_dict()."""
        config = _load_cli_config()
        result = config.af_bins.as_dict()
        assert isinstance(result, dict)
        assert 'high' in result
        assert 'intermediate' in result
        assert 'low' in result

    def test_fasta_bins_different_from_vcf(self):
        """FASTA bins should differ from VCF bins."""
        config = _load_cli_config()
        assert config.af_bins.intermediate != config.af_bins_fasta.intermediate


class TestCliAlignmentConfig:
    """Tests for CliAlignmentConfig."""

    def test_preset_is_string(self):
        """Should have string preset."""
        config = _load_cli_config()
        assert isinstance(config.alignment.preset, str)
        assert len(config.alignment.preset) > 0

    def test_k_positive(self):
        """Should have positive k (k-mer size)."""
        config = _load_cli_config()
        assert config.alignment.k > 0
        assert isinstance(config.alignment.k, int)

    def test_w_positive(self):
        """Should have positive w (minimizer window)."""
        config = _load_cli_config()
        assert config.alignment.w > 0
        assert isinstance(config.alignment.w, int)

    def test_best_n_positive(self):
        """Should have positive best_n."""
        config = _load_cli_config()
        assert config.alignment.best_n > 0
        assert isinstance(config.alignment.best_n, int)

    def test_gap_open_penalty_positive(self):
        """Should have positive gap open penalty."""
        config = _load_cli_config()
        assert config.alignment.gap_open_penalty > 0
        assert isinstance(config.alignment.gap_open_penalty, int)

    def test_match_score_positive(self):
        """Should have positive match score."""
        config = _load_cli_config()
        assert config.alignment.match_score > 0
        assert isinstance(config.alignment.match_score, int)

    def test_mismatch_penalty_positive(self):
        """Should have positive mismatch penalty."""
        config = _load_cli_config()
        assert config.alignment.mismatch_penalty > 0
        assert isinstance(config.alignment.mismatch_penalty, int)

    def test_gap_extension_penalty_1_positive(self):
        """Should have positive gap extension penalty 1."""
        config = _load_cli_config()
        assert config.alignment.gap_extension_penalty_1 > 0
        assert isinstance(config.alignment.gap_extension_penalty_1, int)

    def test_gap_open_penalty_2_positive(self):
        """Should have positive gap open penalty 2."""
        config = _load_cli_config()
        assert config.alignment.gap_open_penalty_2 > 0
        assert isinstance(config.alignment.gap_open_penalty_2, int)

    def test_gap_extension_penalty_2_positive(self):
        """Should have positive gap extension penalty 2."""
        config = _load_cli_config()
        assert config.alignment.gap_extension_penalty_2 > 0
        assert isinstance(config.alignment.gap_extension_penalty_2, int)

    def test_intron_junction_tolerance_non_negative(self):
        """Should have non-negative intron junction tolerance."""
        config = _load_cli_config()
        assert config.alignment.intron_junction_tolerance >= 0
        assert isinstance(config.alignment.intron_junction_tolerance, int)


class TestDefaultsTomlStructure:
    """Tests for defaults.toml file structure."""

    def test_file_exists_and_valid_toml(self):
        """Should exist and be valid TOML."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        content = defaults_path.read_text(encoding='utf-8')
        # Should not raise tomllib.TOMLDecodeError
        payload = tomllib.loads(content)
        assert payload is not None

    def test_all_required_sections_present(self):
        """Should have all required sections."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        required_sections = [
            'timeouts',
            'urls',
            'parsing',
            'matching',
            'similarity',
            'af_bins',
            'af_bins_fasta',
            'alignment',
        ]
        for section in required_sections:
            assert section in payload, f"Missing section: {section}"

    def test_no_duplicate_keys(self):
        """Should not have duplicate keys in sections."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        # tomllib would fail on duplicate keys, so if we got here, it's valid
        assert len(payload) > 0

    def test_timeouts_section_structure(self):
        """Should have correct timeouts structure."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        timeouts = payload['timeouts']
        assert 'pubchem' in timeouts
        assert 'pubmed' in timeouts
        assert 'crossref' in timeouts

    def test_urls_section_structure(self):
        """Should have correct urls structure."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        urls = payload['urls']
        assert 'pubchem_compound_page' in urls
        assert 'ncbi_pubmed_esummary' in urls
        assert 'crossref_works' in urls

    def test_alignment_section_has_intron_junction_tolerance(self):
        """Should have intron_junction_tolerance in alignment section."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        alignment = payload['alignment']
        assert 'intron_junction_tolerance' in alignment


class TestPromotedMagicNumbers:
    """Tests for magic-number values promoted into defaults.toml (feature magic-numbers-to-toml)."""

    def test_codon_bam_base_quality_threshold_default(self):
        """Should load bam_base_quality_threshold with documented default 0 (accept all bases)."""
        config = _load_cli_config()
        assert config.codon.bam_base_quality_threshold == 0
        assert isinstance(config.codon.bam_base_quality_threshold, int)

    def test_codon_frechet_epsilon_default(self):
        """Should load frechet_epsilon with documented default 1e-9."""
        config = _load_cli_config()
        assert config.codon.frechet_epsilon == 1e-9
        assert isinstance(config.codon.frechet_epsilon, float)

    def test_genbank_timeout_default(self):
        """Should load genbank_timeout with documented default 30."""
        config = _load_cli_config()
        assert config.timeouts.genbank_timeout == 30
        assert isinstance(config.timeouts.genbank_timeout, int)

    def test_genbank_max_retries_default(self):
        """Should load genbank_max_retries with documented default 3."""
        config = _load_cli_config()
        assert config.timeouts.genbank_max_retries == 3
        assert isinstance(config.timeouts.genbank_max_retries, int)

    def test_genbank_backoff_base_default(self):
        """Should load genbank_backoff_base with documented default 1.0."""
        config = _load_cli_config()
        assert config.timeouts.genbank_backoff_base == 1.0
        assert isinstance(config.timeouts.genbank_backoff_base, float)

    def test_defaults_toml_has_codon_promoted_keys(self):
        """defaults.toml should expose the promoted codon keys with inline comments."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        codon = payload['codon']
        assert 'bam_base_quality_threshold' in codon
        assert 'frechet_epsilon' in codon

    def test_defaults_toml_has_genbank_timeout_keys(self):
        """defaults.toml should expose the promoted genbank timeout keys."""
        defaults_path = files('respro.config').joinpath('defaults.toml')
        payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
        timeouts = payload['timeouts']
        assert 'genbank_timeout' in timeouts
        assert 'genbank_max_retries' in timeouts
        assert 'genbank_backoff_base' in timeouts


class TestLoadConfigWithOverrides:
    """Tests for load_config_with_overrides() — U1 deep-merge + strict-validate loader."""

    @staticmethod
    def _write_override(tmp_path, text: str):
        p = tmp_path / 'override.toml'
        p.write_text(text, encoding='utf-8')
        return p

    def test_none_override_equals_bundled(self):
        """load_config_with_overrides(None) should be field-equal to CLI_CONFIG."""
        from respro.config.cli_settings import CLI_CONFIG, load_config_with_overrides
        cfg = load_config_with_overrides(None)
        assert cfg == CLI_CONFIG

    def test_codon_override_applies(self, tmp_path):
        """An override [codon] min_read_mapping_quality = 30 should apply, others stay default."""
        from respro.config.cli_settings import CLI_CONFIG, load_config_with_overrides
        p = self._write_override(tmp_path, '[codon]\nmin_read_mapping_quality = 30\n')
        cfg = load_config_with_overrides(p)
        assert cfg.codon.min_read_mapping_quality == 30
        # Other codon keys stay at bundled defaults.
        assert cfg.codon.min_cooccurrence_codon_fraction == CLI_CONFIG.codon.min_cooccurrence_codon_fraction
        assert cfg.codon.bam_base_quality_threshold == CLI_CONFIG.codon.bam_base_quality_threshold
        # Other sections untouched.
        assert cfg.matching == CLI_CONFIG.matching

    def test_matching_override_applies(self, tmp_path):
        """An override [matching] combination_member_af_threshold = 0.8 should apply."""
        from respro.config.cli_settings import load_config_with_overrides
        p = self._write_override(tmp_path, '[matching]\ncombination_member_af_threshold = 0.8\n')
        cfg = load_config_with_overrides(p)
        assert cfg.matching.combination_member_af_threshold == 0.8

    def test_unknown_section_rejected(self, tmp_path):
        """An override with an unknown section [foo] should raise ValueError naming it."""
        from respro.config.cli_settings import load_config_with_overrides
        p = self._write_override(tmp_path, '[foo]\nbar = 1\n')
        with pytest.raises(ValueError, match='foo'):
            load_config_with_overrides(p)

    def test_unknown_key_rejected(self, tmp_path):
        """An override with an unknown key [codon] bar = 1 should raise ValueError naming it."""
        from respro.config.cli_settings import load_config_with_overrides
        p = self._write_override(tmp_path, '[codon]\nbar = 1\n')
        with pytest.raises(ValueError, match='bar'):
            load_config_with_overrides(p)

    def test_type_mismatch_rejected(self, tmp_path):
        """A type mismatch ([codon] min_read_mapping_quality = 'twenty') should raise ValueError."""
        from respro.config.cli_settings import load_config_with_overrides
        p = self._write_override(tmp_path, '[codon]\nmin_read_mapping_quality = "twenty"\n')
        with pytest.raises(ValueError, match='min_read_mapping_quality'):
            load_config_with_overrides(p)

    def test_list_valued_override_replaces_bundled_list(self, tmp_path):
        """A list-valued override ([af_bins] high = [0.8, 1.0]) should replace the bundled list."""
        from respro.config.cli_settings import load_config_with_overrides
        p = self._write_override(tmp_path, '[af_bins]\nhigh = [0.8, 1.0]\n')
        cfg = load_config_with_overrides(p)
        assert cfg.af_bins.high == (0.8, 1.0)
        # Other af_bins keys stay default.
        assert cfg.af_bins.intermediate == (0.25, 0.7499)

    def test_deep_merge_nested_sections(self, tmp_path):
        """A partial section override should deep-merge, keeping un-overridden keys."""
        from respro.config.cli_settings import CLI_CONFIG, load_config_with_overrides
        p = self._write_override(tmp_path, '[timeouts]\ngenbank_timeout = 60\n')
        cfg = load_config_with_overrides(p)
        assert cfg.timeouts.genbank_timeout == 60
        assert cfg.timeouts.pubchem == CLI_CONFIG.timeouts.pubchem
        assert cfg.timeouts.genbank_max_retries == CLI_CONFIG.timeouts.genbank_max_retries

    def test_override_does_not_mutate_bundled_singleton(self, tmp_path):
        """An override must not mutate the module-level CLI_CONFIG singleton."""
        from respro.config.cli_settings import CLI_CONFIG, load_config_with_overrides
        original = CLI_CONFIG.codon.min_read_mapping_quality
        p = self._write_override(tmp_path, '[codon]\nmin_read_mapping_quality = 99\n')
        load_config_with_overrides(p)
        assert CLI_CONFIG.codon.min_read_mapping_quality == original
