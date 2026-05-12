"""LLM provider factory — returns the right LangChain model based on LLM_PROVIDER env var.

Supported providers (set LLM_PROVIDER in .env):
  anthropic  — claude-haiku-4-5-20251001  (default)
  openai     — gpt-4o-mini
  google     — gemini-2.0-flash
  ollama     — llama3.2

Set LLM_MODEL to override the default model for whichever provider is active.
"""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from agent.chains.cache_config import get_cache_control

_PROVIDER_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai":    "gpt-4o-mini",
    "google":    "gemini-2.0-flash",
    "ollama":    "llama3.2",
}


def is_anthropic_provider() -> bool:
    return os.getenv("LLM_PROVIDER", "anthropic").strip().lower() == "anthropic"


def get_llm() -> BaseChatModel:
    """Return a LangChain chat model configured from LLM_PROVIDER and LLM_MODEL."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    model = os.getenv("LLM_MODEL", "").strip() or _PROVIDER_DEFAULTS.get(provider, "")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0)  # type: ignore[call-arg]

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=0)

    raise ValueError(
        f"Unknown LLM_PROVIDER={provider!r}. "
        f"Valid values: {', '.join(_PROVIDER_DEFAULTS)}"
    )


def build_system_message(prompt: str) -> SystemMessage:
    """Return a SystemMessage for the given prompt.

    For Anthropic, injects cache_control when PROMPT_CACHE_TTL is active so
    the system prompt is eligible for prompt caching. All other providers get
    a plain SystemMessage with no extra kwargs.
    """
    if is_anthropic_provider():
        cache_control = get_cache_control()
        if cache_control is not None:
            return SystemMessage(
                content=prompt,
                additional_kwargs={"cache_control": cache_control},
            )
    return SystemMessage(content=prompt)
