"""Core infrastructure tests: util, config, db, events, jobs.

These cover the functions the rest of the system trusts silently - identifier
normalisation, bilingual word counting, JSON parameter binding, cooperative
cancellation. A bug in any of them shows up far from its cause, which is why they
are tested directly.
"""

from __future__ import annotations

import time

import pytest

from papercreator.core import util


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.1234/ABC", "10.1234/abc"),
            ("https://doi.org/10.1234/abc", "10.1234/abc"),
            ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
            ("DOI: 10.1234/abc", "10.1234/abc"),
            ("doi:10.1234/abc.", "10.1234/abc"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_doi_forms_converge(self, raw, expected):
        """Providers return DOIs in all of these shapes; dedupe needs one form."""
        assert util.normalize_doi(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2301.01234", "2301.01234"),
            ("arXiv:2301.01234", "2301.01234"),
            ("https://arxiv.org/abs/2301.01234v3", "2301.01234"),
            ("https://arxiv.org/pdf/2301.01234.pdf", "2301.01234"),
            ("2301.01234v12", "2301.01234"),
        ],
    )
    def test_arxiv_version_is_stripped(self, raw, expected):
        """v1 and v3 of one preprint must deduplicate to the same record."""
        assert util.normalize_arxiv_id(raw) == expected

    def test_title_similarity_tolerates_case_and_punctuation(self):
        assert util.title_similarity("Attention Is All You Need",
                                     "attention is all you need!") == 1.0

    def test_title_similarity_separates_distinct_titles(self):
        score = util.title_similarity(
            "Attention Is All You Need",
            "Attention Is Not All You Need: Pure Attention Loses Rank",
        )
        assert score < 0.9, f"distinct papers must not reach the merge threshold ({score})"

    def test_title_similarity_length_prefilter(self):
        # A very short title against a long one should not be a near match.
        assert util.title_similarity("GANs", "Generative Adversarial Networks for "
                                             "Image Synthesis at Scale") == 0.0


class TestWordCount:
    def test_counts_english_words(self):
        assert util.word_count("one two three") == 3

    def test_counts_chinese_characters(self):
        # CJK has no spaces, so whitespace tokens would report 1 per paragraph.
        assert util.word_count("多智能体系统") == 6

    def test_counts_mixed_text(self):
        assert util.word_count("使用 GNN 预测") == 4 + 1

    def test_empty(self):
        assert util.word_count("") == 0


class TestSlugify:
    def test_ascii(self):
        assert util.slugify("Multi-Agent LLM Systems!") == "multi-agent-llm-systems"

    def test_chinese_gets_a_stable_suffix(self):
        """Two different Chinese titles must not collide on one directory name."""
        first = util.slugify("多智能体论文写作")
        second = util.slugify("图神经网络综述")
        assert first != second
        assert util.slugify("多智能体论文写作") == first, "must be deterministic"

    def test_windows_reserved_names_are_escaped(self):
        assert util.slugify("CON").startswith("x-")

    def test_empty_falls_back(self):
        assert util.slugify("", fallback="paper").startswith("paper")


class TestLanguageDetection:
    def test_english(self):
        assert util.detect_language("This is an English abstract.") == "en"

    def test_chinese(self):
        assert util.detect_language("这是一段中文摘要，用于测试。") == "zh"

    def test_mostly_english_with_a_few_cjk(self):
        assert util.detect_language(
            "This English abstract mentions 图神经网络 once among many words "
            "of ordinary English prose that dominate the sample entirely."
        ) == "en"


class TestDatabaseBinding:
    def test_named_parameters_bind_by_name(self, temp_home):
        """A dict must bind by name, not be coerced to a tuple of its keys.

        Regression test: coercing a mapping with tuple() silently bound the
        column *names* as values, which corrupted every write that used named
        placeholders.
        """
        from papercreator.core import db

        db.execute("CREATE TABLE IF NOT EXISTS _bind_test (a TEXT, b TEXT)")
        db.execute("DELETE FROM _bind_test")
        db.execute(
            "INSERT INTO _bind_test (a, b) VALUES (:a, :b)", {"a": "alpha", "b": "beta"}
        )
        row = db.query_one("SELECT a, b FROM _bind_test")
        assert row is not None
        assert (row["a"], row["b"]) == ("alpha", "beta")

    def test_positional_parameters_still_work(self, temp_home):
        from papercreator.core import db

        db.execute("DELETE FROM _bind_test")
        db.execute("INSERT INTO _bind_test (a, b) VALUES (?,?)", ("x", "y"))
        row = db.query_one("SELECT a, b FROM _bind_test")
        assert (row["a"], row["b"]) == ("x", "y")

    def test_loads_tolerates_corrupt_json(self):
        from papercreator.core import db

        # A hand-edited or truncated cell must not break a list view.
        assert db.loads("{not json", default={"ok": True}) == {"ok": True}
        assert db.loads(None, default=[]) == []

    def test_schema_is_migrated(self, temp_home):
        from papercreator.core import db

        stats = db.stats()
        assert stats["schema_version"] >= 1
        assert stats["counts"]["papers"] is not None, "papers table must exist"


class TestEvents:
    def test_publish_and_replay(self):
        from papercreator.core import events

        first = events.publish("notify", {"message": "one"})
        second = events.publish("notify", {"message": "two"})
        assert second.seq > first.seq
        replayed = events.bus.replay(first.seq)
        assert [e.seq for e in replayed][-1] == second.seq
        assert first.seq not in [e.seq for e in replayed], "after= is exclusive"

    def test_sse_frame_shape(self):
        from papercreator.core import events

        frame = events.publish("notify", {"message": "hello"}).to_sse()
        assert frame.startswith("id: ")
        assert "event: notify" in frame
        assert frame.endswith("\n\n"), "SSE frames must end with a blank line"


class TestJobs:
    def test_job_completes_and_stores_its_result(self, temp_home):
        from papercreator.core.jobs import manager

        handle = manager.submit("test", lambda ctx: {"value": 42})
        row = manager.wait(handle.id, timeout=10)
        assert row["status"] == "done"
        assert row["result"]["value"] == 42

    def test_failure_is_recorded_not_raised(self, temp_home):
        from papercreator.core.jobs import manager

        def explode(ctx):
            raise ValueError("deliberate")

        handle = manager.submit("test", explode)
        row = manager.wait(handle.id, timeout=10)
        assert row["status"] == "failed"
        assert "deliberate" in row["error"]

    def test_cancellation_is_cooperative(self, temp_home):
        """Cancel sets a flag; the worker must observe it at a checkpoint."""
        from papercreator.core.jobs import manager

        def slow(ctx):
            for _ in range(200):
                ctx.raise_if_cancelled()
                time.sleep(0.02)
            return {"finished": True}

        handle = manager.submit("test", slow)
        time.sleep(0.2)
        assert manager.cancel(handle.id) is True
        row = manager.wait(handle.id, timeout=10)
        assert row["status"] == "cancelled"


class TestSecretScrubbing:
    @pytest.mark.parametrize(
        "text",
        [
            "using key sk-abcdef1234567890",
            "Authorization: Bearer abcdefghijklmnop",
            "https://api.example.com/v1?api_key=supersecretvalue",
            "token=ghp_abcdefghijklmnopqrst",
        ],
    )
    def test_credentials_never_reach_a_log_sink(self, text):
        from papercreator.core.logging_setup import scrub

        cleaned = scrub(text)
        assert "***" in cleaned
        for secret in ("sk-abcdef1234567890", "abcdefghijklmnop",
                       "supersecretvalue", "ghp_abcdefghijklmnopqrst"):
            assert secret not in cleaned

    def test_ordinary_text_is_untouched(self):
        from papercreator.core.logging_setup import scrub

        message = "retrieved 42 papers from openalex in 1530ms"
        assert scrub(message) == message


class TestSettings:
    def test_environment_is_sparse_and_overrides_persisted_settings(
        self, temp_home, monkeypatch
    ):
        from papercreator.core import config
        from papercreator.core.paths import get_paths

        get_paths().settings_file.write_text(
            '{"server":{"host":"127.0.0.2","port":9123},"identity":{"contact_email":"disk@example.edu"}}',
            encoding="utf-8",
        )
        monkeypatch.delenv("PC_PORT", raising=False)
        monkeypatch.setenv("PC_HOST", "")
        monkeypatch.setenv("PC_CONTACT_EMAIL", "env@example.edu")

        settings = config.reload_settings()
        assert settings.server.host == "127.0.0.2"
        assert settings.server.port == 9123
        assert settings.identity.contact_email == "env@example.edu"

        monkeypatch.delenv("PC_CONTACT_EMAIL")
        assert config.reload_settings().identity.contact_email == "disk@example.edu"

    def test_empty_persisted_string_clears_an_older_value(self):
        from papercreator.core.config import _deep_merge

        merged = _deep_merge(
            {"identity": {"contact_email": "old@example.edu"}},
            {"identity": {"contact_email": ""}},
        )
        assert merged["identity"]["contact_email"] == ""

    def test_configuration_sources_never_include_values(
        self, temp_home, monkeypatch
    ):
        from papercreator.core import config
        from papercreator.core.paths import get_paths

        secret = "source-diagnostic-must-not-leak"
        get_paths().secrets_file.write_text(
            '{"provider_keys":{"openalex":"stored-secret"}}', encoding="utf-8"
        )
        monkeypatch.setenv("PC_OPENALEX_API_KEY", secret)
        metadata = config.configuration_sources()

        assert metadata["precedence"][-1] == "environment"
        assert "PC_OPENALEX_API_KEY" in metadata["environment"]["variables"]
        assert "provider_keys.openalex" in metadata["environment"]["override_fields"]
        assert "provider_keys.openalex" not in metadata["dotenv"]["override_fields"]
        assert secret not in str(metadata)
        assert "stored-secret" not in str(metadata)

    def test_secrets_are_masked_in_api_output(self, temp_home):
        from papercreator.core.config import MASK, Settings

        settings = Settings.model_validate(
            {"provider_keys": {"openalex": "real-secret-value"}}
        )
        redacted = settings.redacted()
        assert redacted["provider_keys"]["openalex"] == MASK
        assert "real-secret-value" not in str(redacted)

    def test_user_agent_includes_contact_email(self):
        from papercreator.core.config import Settings

        settings = Settings.model_validate(
            {"identity": {"contact_email": "me@example.edu"}}
        )
        agent = settings.user_agent()
        assert "PaperCreator" in agent and "me@example.edu" in agent

    def test_openalex_endpoint_allows_https_and_loopback_http(self):
        from papercreator.core.config import Settings

        secure = Settings.model_validate({
            "retrieval": {"openalex_endpoint": "https://mirror.example.edu/openalex/works/"}
        })
        local = Settings.model_validate({
            "retrieval": {"openalex_endpoint": "http://127.0.0.1:9123/works"}
        })

        assert secure.retrieval.openalex_endpoint == "https://mirror.example.edu/openalex/works"
        assert local.retrieval.openalex_endpoint == "http://127.0.0.1:9123/works"

    def test_openalex_endpoint_rejects_remote_plain_http(self):
        from pydantic import ValidationError as PydanticValidationError

        from papercreator.core.config import Settings

        with pytest.raises(PydanticValidationError, match="must use HTTPS"):
            Settings.model_validate({
                "retrieval": {"openalex_endpoint": "http://mirror.example.edu/works"}
            })
