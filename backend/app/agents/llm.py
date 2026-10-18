from __future__ import annotations
from functools import lru_cache
from langchain_anthropic import ChatAnthropic
from app.config import settings


@lru_cache(maxsize=8)
def get_llm(model: str, temperature: float = 0.1) -> ChatAnthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "MIO_ANTHROPIC_API_KEY is not set. Export it or add it to backend/.env "
            "before running the agent graph."
        )
    return ChatAnthropic(
        model=model,
        api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=4096,
    )


def architect_llm() -> ChatAnthropic:
    return get_llm(settings.architect_model, temperature=0.3)


def coder_llm() -> ChatAnthropic:
    return get_llm(settings.coder_model, temperature=0.1)


def reviewer_llm() -> ChatAnthropic:
    return get_llm(settings.reviewer_model, temperature=0.0)


def qa_llm() -> ChatAnthropic:
    return get_llm(settings.qa_model, temperature=0.0)
