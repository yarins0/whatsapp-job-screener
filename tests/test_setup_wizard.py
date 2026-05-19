"""Tests for the interactive setup wizard (setup.py).

All tests use mocked input() and subprocess.run — no real installs happen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import setup as wizard  # noqa: E402  (must come after sys.path insert)


# ---------------------------------------------------------------------------
# _load_dotenv
# ---------------------------------------------------------------------------

class TestLoadDotenv:
    def test_empty_when_file_missing(self, tmp_path):
        with patch.object(wizard, "DOTENV_PATH", tmp_path / ".env"):
            assert wizard._load_dotenv() == {}

    def test_parses_key_value_pairs(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        with patch.object(wizard, "DOTENV_PATH", f):
            assert wizard._load_dotenv() == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# comment\n\nFOO=bar\n", encoding="utf-8")
        with patch.object(wizard, "DOTENV_PATH", f):
            assert wizard._load_dotenv() == {"FOO": "bar"}

    def test_value_with_embedded_equals(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("KEY=a=b=c\n", encoding="utf-8")
        with patch.object(wizard, "DOTENV_PATH", f):
            assert wizard._load_dotenv() == {"KEY": "a=b=c"}


# ---------------------------------------------------------------------------
# _ask
# ---------------------------------------------------------------------------

class TestAsk:
    def test_returns_typed_value(self):
        with patch("builtins.input", return_value="hello"):
            assert wizard._ask("Prompt") == "hello"

    def test_strips_whitespace(self):
        with patch("builtins.input", return_value="  value  "):
            assert wizard._ask("Prompt") == "value"

    def test_returns_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            assert wizard._ask("Prompt", default="fallback") == "fallback"

    def test_reprompts_until_value_given(self, capsys):
        with patch("builtins.input", side_effect=["", "", "ok"]):
            result = wizard._ask("Prompt")
        assert result == "ok"
        assert "(required" in capsys.readouterr().out

    def test_secret_flag_is_no_op(self):
        with patch("builtins.input", return_value="token123"):
            assert wizard._ask("Key", secret=True) == "token123"


# ---------------------------------------------------------------------------
# _ask_yn
# ---------------------------------------------------------------------------

class TestAskYn:
    @pytest.mark.parametrize("answer", ["y", "yes", "Y", "YES"])
    def test_yes_answers(self, answer):
        with patch("builtins.input", return_value=answer):
            assert wizard._ask_yn("Continue?") is True

    @pytest.mark.parametrize("answer", ["n", "no", "N", "NO"])
    def test_no_answers(self, answer):
        with patch("builtins.input", return_value=answer):
            assert wizard._ask_yn("Continue?") is False

    def test_empty_defaults_to_yes(self):
        with patch("builtins.input", return_value=""):
            assert wizard._ask_yn("Continue?", default_yes=True) is True

    def test_empty_defaults_to_no(self):
        with patch("builtins.input", return_value=""):
            assert wizard._ask_yn("Continue?", default_yes=False) is False


# ---------------------------------------------------------------------------
# check_prerequisites
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    def _ok(self, stdout="v22.0.0"):
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    def test_passes_when_node_and_npm_present(self, capsys):
        with patch("shutil.which", return_value="/usr/bin/node"), \
             patch("subprocess.run", side_effect=[self._ok("v22.0.0"), self._ok("10.0.0")]):
            wizard.check_prerequisites()
        out = capsys.readouterr().out
        assert "node" in out
        assert "npm" in out

    def test_exits_when_node_missing(self):
        with patch("shutil.which", side_effect=lambda t: None if t == "node" else "/usr/bin/npm"), \
             pytest.raises(SystemExit):
            wizard.check_prerequisites()

    def test_exits_when_npm_missing(self):
        with patch("shutil.which", side_effect=lambda t: "/usr/bin/node" if t == "node" else None), \
             patch("subprocess.run", return_value=self._ok()), \
             pytest.raises(SystemExit):
            wizard.check_prerequisites()


# ---------------------------------------------------------------------------
# configure_env
# ---------------------------------------------------------------------------

class TestConfigureEnv:
    def _run(self, env_path, inputs):
        with patch.object(wizard, "DOTENV_PATH", env_path), \
             patch("builtins.input", side_effect=inputs):
            return wizard.configure_env()

    # Actual prompt order (what the wizard asks):
    # Anthropic:     provider, api_key, bot_token, chat_id, cache_ttl, dup_days, tg_source_yn
    # Other LLMs:    provider, api_key, bot_token, chat_id, dup_days,  tg_source_yn
    # Ollama:        provider, bot_token, chat_id, dup_days, tg_source_yn
    # With tg_source: ..., tg_source_yn=y, api_id, api_hash, phone

    # --- fresh install (no existing .env) ---

    def test_fresh_anthropic(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["1", "sk-ant-key", "bot:token", "12345", "5m", "7", "n"])
        assert provider == "anthropic"
        content = env_path.read_text()
        assert "LLM_PROVIDER=anthropic" in content
        assert "ANTHROPIC_API_KEY=sk-ant-key" in content
        assert "TELEGRAM_BOT_TOKEN=bot:token" in content
        assert "TELEGRAM_CHAT_ID=12345" in content

    def test_fresh_openai(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["2", "sk-openai-key", "bot:token", "12345", "7", "n"])
        assert provider == "openai"
        content = env_path.read_text()
        assert "LLM_PROVIDER=openai" in content
        assert "OPENAI_API_KEY=sk-openai-key" in content

    def test_fresh_google(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["3", "goog-key", "bot:token", "12345", "7", "n"])
        assert provider == "google"
        assert "GOOGLE_API_KEY=goog-key" in env_path.read_text()

    def test_fresh_ollama_no_api_key_prompt(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["4", "bot:token", "12345", "7", "n"])
        assert provider == "ollama"
        content = env_path.read_text()
        assert "LLM_PROVIDER=ollama" in content
        assert "API_KEY" not in content

    def test_invalid_provider_choice_reprompts(self, tmp_path, capsys):
        env_path = tmp_path / ".env"
        self._run(env_path, ["9", "0", "1", "sk-ant", "bot:token", "12345", "off", "7", "n"])
        assert "1 and 4" in capsys.readouterr().out

    def test_with_telegram_source(self, tmp_path):
        env_path = tmp_path / ".env"
        inputs = ["1", "sk-ant", "bot:token", "12345", "off", "7", "y", "98765", "hash123", "+972501234567"]
        self._run(env_path, inputs)
        content = env_path.read_text()
        assert "TELEGRAM_API_ID=98765" in content
        assert "TELEGRAM_API_HASH=hash123" in content
        assert "TELEGRAM_PHONE=+972501234567" in content

    # --- new fields ---

    def test_anthropic_writes_prompt_cache_ttl(self, tmp_path):
        env_path = tmp_path / ".env"
        self._run(env_path, ["1", "sk-ant", "bot:token", "12345", "5m", "7", "n"])
        assert "PROMPT_CACHE_TTL=5m" in env_path.read_text()

    def test_anthropic_cache_ttl_rejects_invalid_then_accepts(self, tmp_path):
        env_path = tmp_path / ".env"
        # "invalid" and "300" are rejected; "auto" is accepted on the third try
        self._run(env_path, ["1", "sk-ant", "bot:token", "12345", "invalid", "300", "auto", "7", "n"])
        assert "PROMPT_CACHE_TTL=auto" in env_path.read_text()

    def test_anthropic_cache_ttl_default_is_off(self, tmp_path):
        env_path = tmp_path / ".env"
        self._run(env_path, ["1", "sk-ant", "bot:token", "12345", "", "7", "n"])
        assert "PROMPT_CACHE_TTL=off" in env_path.read_text()

    def test_non_anthropic_has_no_cache_ttl(self, tmp_path):
        env_path = tmp_path / ".env"
        self._run(env_path, ["2", "sk-openai", "bot:token", "12345", "7", "n"])
        assert "PROMPT_CACHE_TTL" not in env_path.read_text()

    def test_duplicate_window_days_written_for_all_providers(self, tmp_path):
        cases = [
            ("1", ["sk-ant", "bot:token", "12345", "off", "14"]),
            ("2", ["sk-openai", "bot:token", "12345", "14"]),
            ("3", ["goog-key", "bot:token", "12345", "14"]),
            ("4", ["bot:token", "12345", "14"]),
        ]
        for provider_num, fields in cases:
            env_path = tmp_path / f".env{provider_num}"
            self._run(env_path, [provider_num] + fields + ["n"])
            assert "DUPLICATE_WINDOW_DAYS=14" in env_path.read_text()

    def test_duplicate_window_days_default_is_7(self, tmp_path):
        env_path = tmp_path / ".env"
        self._run(env_path, ["1", "sk-ant", "bot:token", "12345", "off", "", "n"])
        assert "DUPLICATE_WINDOW_DAYS=7" in env_path.read_text()

    # --- existing .env ---

    def test_skip_reconfigure_returns_existing_provider(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("LLM_PROVIDER=openai\nOPENAI_API_KEY=old-key\n", encoding="utf-8")
        provider = self._run(env_path, ["n"])
        assert provider == "openai"
        assert "old-key" in env_path.read_text()

    def test_reconfigure_overwrites_keys(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("LLM_PROVIDER=openai\nOPENAI_API_KEY=old-key\n", encoding="utf-8")
        self._run(env_path, ["y", "1", "sk-ant-new", "bot:token", "12345", "off", "7", "n"])
        content = env_path.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-new" in content
        assert "old-key" not in content

    def test_preserves_unknown_fields_on_reconfigure(self, tmp_path):
        env_path = tmp_path / ".env"
        # LANGSMITH_API_KEY is unknown to wizard — must survive reconfigure.
        # DUPLICATE_WINDOW_DAYS is now a wizard key — prompted with existing as default.
        env_path.write_text(
            "LLM_PROVIDER=anthropic\nLANGSMITH_API_KEY=ls-key\nDUPLICATE_WINDOW_DAYS=14\n",
            encoding="utf-8",
        )
        # Empty input for dup_days -> uses existing value 14 as default
        self._run(env_path, ["y", "1", "sk-ant", "bot:token", "12345", "off", "", "n"])
        content = env_path.read_text()
        assert "LANGSMITH_API_KEY=ls-key" in content
        assert "DUPLICATE_WINDOW_DAYS=14" in content

    def test_existing_value_used_as_default_on_empty_input(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("LLM_PROVIDER=anthropic\nTELEGRAM_CHAT_ID=99999\n", encoding="utf-8")
        # Press Enter for TELEGRAM_CHAT_ID -> keeps 99999
        self._run(env_path, ["y", "1", "sk-ant", "bot:token", "", "off", "7", "n"])
        assert "TELEGRAM_CHAT_ID=99999" in env_path.read_text()

    def test_skip_reconfigure_defaults_to_anthropic_when_no_provider_in_env(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("TELEGRAM_BOT_TOKEN=x\n", encoding="utf-8")
        provider = self._run(env_path, ["n"])
        assert provider == "anthropic"


# ---------------------------------------------------------------------------
# install_python_deps
# ---------------------------------------------------------------------------

class TestInstallPythonDeps:
    def _ok(self):
        return MagicMock(returncode=0, stdout="", stderr="")

    def test_anthropic_runs_single_install(self):
        with patch("subprocess.run", return_value=self._ok()) as mock_run:
            wizard.install_python_deps("anthropic")
        assert mock_run.call_count == 1

    def test_openai_installs_extra_package(self):
        with patch("subprocess.run", return_value=self._ok()) as mock_run:
            wizard.install_python_deps("openai")
        assert mock_run.call_count == 2
        extra_args = mock_run.call_args_list[1][0][0]
        assert any("langchain-openai" in a for a in extra_args)

    def test_google_installs_extra_package(self):
        with patch("subprocess.run", return_value=self._ok()) as mock_run:
            wizard.install_python_deps("google")
        assert mock_run.call_count == 2
        extra_args = mock_run.call_args_list[1][0][0]
        assert any("langchain-google-genai" in a for a in extra_args)

    def test_ollama_installs_extra_package(self):
        with patch("subprocess.run", return_value=self._ok()) as mock_run:
            wizard.install_python_deps("ollama")
        assert mock_run.call_count == 2

    def test_exits_on_pip_failure(self):
        fail = MagicMock(returncode=1, stdout="err", stderr="bad")
        with patch("subprocess.run", return_value=fail), pytest.raises(SystemExit):
            wizard.install_python_deps("anthropic")


# ---------------------------------------------------------------------------
# install_node_deps
# ---------------------------------------------------------------------------

class TestInstallNodeDeps:
    def test_success(self):
        ok = MagicMock(returncode=0, stdout="", stderr="")
        with patch("shutil.which", return_value="/usr/bin/npm"), \
             patch("subprocess.run", return_value=ok):
            wizard.install_node_deps()

    def test_exits_on_npm_failure(self):
        fail = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("shutil.which", return_value="/usr/bin/npm"), \
             patch("subprocess.run", return_value=fail), \
             pytest.raises(SystemExit):
            wizard.install_node_deps()

    def test_uses_resolved_npm_path(self):
        ok = MagicMock(returncode=0, stdout="", stderr="")
        with patch("shutil.which", return_value="C:\\npm.cmd") as mock_which, \
             patch("subprocess.run", return_value=ok) as mock_run:
            wizard.install_node_deps()
        mock_which.assert_called_with("npm")
        assert mock_run.call_args[0][0][0] == "C:\\npm.cmd"


# ---------------------------------------------------------------------------
# init_database
# ---------------------------------------------------------------------------

class TestInitDatabase:
    def test_success(self, capsys):
        ok = MagicMock(returncode=0, stdout="Initialized DB at db/jobs.db", stderr="")
        with patch("subprocess.run", return_value=ok):
            wizard.init_database()
        assert "Initialized DB" in capsys.readouterr().out

    def test_exits_on_failure(self):
        fail = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("subprocess.run", return_value=fail), pytest.raises(SystemExit):
            wizard.init_database()


# ---------------------------------------------------------------------------
# configure_prefs
# ---------------------------------------------------------------------------

class TestConfigurePrefs:
    def _run(self, prefs_path, inputs):
        with patch.object(wizard, "PREFS_PATH", prefs_path), \
             patch("builtins.input", side_effect=inputs):
            wizard.configure_prefs()

    # Fresh install (no existing prefs file).
    # Input order: roles (_ask), blocklist (raw input), location_blocklist (raw input).

    def test_fresh_writes_given_roles(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        self._run(prefs_path, ["python, backend", "", ""])
        assert json.loads(prefs_path.read_text())["roles"] == ["python", "backend"]

    def test_fresh_empty_blocklist_keeps_default(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        self._run(prefs_path, ["python", "", ""])
        assert json.loads(prefs_path.read_text())["blocklist"] == [
            "unpaid", "volunteer", "senior", "sales", "qa"
        ]

    def test_fresh_custom_blocklist(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        self._run(prefs_path, ["python", "senior, unpaid", ""])
        assert json.loads(prefs_path.read_text())["blocklist"] == ["senior", "unpaid"]

    def test_fresh_location_blocklist_parsed(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        self._run(prefs_path, ["python", "", "Jerusalem, Haifa"])
        assert json.loads(prefs_path.read_text())["location_blocklist"] == ["Jerusalem", "Haifa"]

    def test_fresh_min_salary_is_null(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        self._run(prefs_path, ["python", "", ""])
        assert json.loads(prefs_path.read_text())["min_salary"] is None

    # Existing file — skip.
    # Input order: reconfigure_yn (_ask_yn).

    def test_skip_reconfigure_leaves_file_unchanged(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        original = {"roles": ["go"], "blocklist": [], "location_blocklist": [], "min_salary": None}
        prefs_path.write_text(json.dumps(original), encoding="utf-8")
        self._run(prefs_path, ["n"])
        assert json.loads(prefs_path.read_text())["roles"] == ["go"]

    # Existing file — reconfigure.
    # Input order: reconfigure_yn, roles, blocklist, location_blocklist.

    def test_reconfigure_overwrites_roles(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        prefs_path.write_text(
            json.dumps({"roles": ["old"], "blocklist": [], "location_blocklist": [], "min_salary": None}),
            encoding="utf-8",
        )
        self._run(prefs_path, ["y", "rust, go", "", ""])
        assert json.loads(prefs_path.read_text())["roles"] == ["rust", "go"]

    def test_reconfigure_empty_blocklist_keeps_existing(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        prefs_path.write_text(
            json.dumps({"roles": ["x"], "blocklist": ["qa"], "location_blocklist": [], "min_salary": None}),
            encoding="utf-8",
        )
        self._run(prefs_path, ["y", "python", "", ""])
        assert json.loads(prefs_path.read_text())["blocklist"] == ["qa"]

    def test_min_salary_preserved_from_existing(self, tmp_path):
        prefs_path = tmp_path / "prefs.json"
        prefs_path.write_text(
            json.dumps({"roles": ["x"], "blocklist": [], "location_blocklist": [], "min_salary": 5000}),
            encoding="utf-8",
        )
        self._run(prefs_path, ["y", "python", "", ""])
        assert json.loads(prefs_path.read_text())["min_salary"] == 5000

    def test_invalid_json_warns_and_uses_defaults(self, tmp_path, capsys):
        prefs_path = tmp_path / "prefs.json"
        prefs_path.write_text("not json!", encoding="utf-8")
        self._run(prefs_path, ["y", "python", "", ""])
        assert "invalid" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# authenticate_telegram_source
# ---------------------------------------------------------------------------

class TestAuthenticateTelegramSource:
    _TG_ENV = {
        "TELEGRAM_API_ID": "123",
        "TELEGRAM_API_HASH": "abc",
        "TELEGRAM_PHONE": "+972501234567",
    }

    def test_skips_when_no_telegram_source_vars(self, capsys):
        with patch.object(wizard, "_load_dotenv", return_value={}):
            wizard.authenticate_telegram_source()
        assert "not configured" in capsys.readouterr().out

    def test_skips_when_only_partial_vars(self, capsys):
        partial = {"TELEGRAM_API_ID": "123", "TELEGRAM_API_HASH": "abc"}
        with patch.object(wizard, "_load_dotenv", return_value=partial):
            wizard.authenticate_telegram_source()
        assert "not configured" in capsys.readouterr().out

    def test_user_declines_no_subprocess(self):
        with patch.object(wizard, "_load_dotenv", return_value=self._TG_ENV), \
             patch("builtins.input", return_value="n"), \
             patch("subprocess.run") as mock_run:
            wizard.authenticate_telegram_source()
        mock_run.assert_not_called()

    def test_user_declines_prints_manual_instruction(self, capsys):
        with patch.object(wizard, "_load_dotenv", return_value=self._TG_ENV), \
             patch("builtins.input", return_value="n"):
            wizard.authenticate_telegram_source()
        assert "manually" in capsys.readouterr().out

    def test_user_accepts_runs_listener(self):
        ok = MagicMock(returncode=0)
        with patch.object(wizard, "_load_dotenv", return_value=self._TG_ENV), \
             patch("builtins.input", return_value="y"), \
             patch("subprocess.run", return_value=ok) as mock_run:
            wizard.authenticate_telegram_source()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        assert cmd[-1] == "sources.telegram.listener"

    def test_keyboard_interrupt_from_subprocess_is_swallowed(self):
        with patch.object(wizard, "_load_dotenv", return_value=self._TG_ENV), \
             patch("builtins.input", return_value="y"), \
             patch("subprocess.run", side_effect=KeyboardInterrupt):
            wizard.authenticate_telegram_source()  # must not raise
