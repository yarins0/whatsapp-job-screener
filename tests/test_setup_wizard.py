"""Tests for the interactive setup wizard (setup.py).

All tests use mocked input() and subprocess.run — no real installs happen.
"""

from __future__ import annotations

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

    # --- fresh install (no existing .env) ---

    def test_fresh_anthropic(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["1", "sk-ant-key", "bot:token", "12345", "n"])
        assert provider == "anthropic"
        content = env_path.read_text()
        assert "LLM_PROVIDER=anthropic" in content
        assert "ANTHROPIC_API_KEY=sk-ant-key" in content
        assert "TELEGRAM_BOT_TOKEN=bot:token" in content
        assert "TELEGRAM_CHAT_ID=12345" in content

    def test_fresh_openai(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["2", "sk-openai-key", "bot:token", "12345", "n"])
        assert provider == "openai"
        content = env_path.read_text()
        assert "LLM_PROVIDER=openai" in content
        assert "OPENAI_API_KEY=sk-openai-key" in content

    def test_fresh_google(self, tmp_path):
        env_path = tmp_path / ".env"
        provider = self._run(env_path, ["3", "goog-key", "bot:token", "12345", "n"])
        assert provider == "google"
        assert "GOOGLE_API_KEY=goog-key" in env_path.read_text()

    def test_fresh_ollama_no_api_key_prompt(self, tmp_path):
        env_path = tmp_path / ".env"
        # Ollama has no API key — only 3 inputs: provider, bot token, chat id, no-tg-source
        provider = self._run(env_path, ["4", "bot:token", "12345", "n"])
        assert provider == "ollama"
        content = env_path.read_text()
        assert "LLM_PROVIDER=ollama" in content
        assert "API_KEY" not in content

    def test_invalid_provider_choice_reprompts(self, tmp_path, capsys):
        env_path = tmp_path / ".env"
        self._run(env_path, ["9", "0", "1", "sk-ant", "bot:token", "12345", "n"])
        assert "1 and 4" in capsys.readouterr().out

    def test_with_telegram_source(self, tmp_path):
        env_path = tmp_path / ".env"
        inputs = ["1", "sk-ant", "bot:token", "12345", "y", "98765", "hash123", "+972501234567"]
        self._run(env_path, inputs)
        content = env_path.read_text()
        assert "TELEGRAM_API_ID=98765" in content
        assert "TELEGRAM_API_HASH=hash123" in content
        assert "TELEGRAM_PHONE=+972501234567" in content

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
        self._run(env_path, ["y", "1", "sk-ant-new", "bot:token", "12345", "n"])
        content = env_path.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-new" in content
        assert "old-key" not in content

    def test_preserves_unknown_fields_on_reconfigure(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "LLM_PROVIDER=anthropic\nLANGSMITH_API_KEY=ls-key\nDUPLICATE_WINDOW_DAYS=14\n",
            encoding="utf-8",
        )
        self._run(env_path, ["y", "1", "sk-ant", "bot:token", "12345", "n"])
        content = env_path.read_text()
        assert "LANGSMITH_API_KEY=ls-key" in content
        assert "DUPLICATE_WINDOW_DAYS=14" in content

    def test_existing_value_used_as_default_on_empty_input(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("LLM_PROVIDER=anthropic\nTELEGRAM_CHAT_ID=99999\n", encoding="utf-8")
        # Press Enter (empty) for TELEGRAM_CHAT_ID -> should keep 99999
        self._run(env_path, ["y", "1", "sk-ant", "bot:token", "", "n"])
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
