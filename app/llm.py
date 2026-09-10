"""OpenRouter-backed LangChain model construction.

OpenRouter implements the OpenAI chat-completions interface, so LangChain's
ChatOpenAI adapter can use Qwen and other compatible models without changing
the calendar agent. Only configuration belongs in this module; calendar logic
remains provider-independent.
"""

from langchain_openai import ChatOpenAI

from app.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
)


def create_openrouter_model(
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    temperature: float = 0,
) -> ChatOpenAI:
    """Return a LangChain chat model configured for OpenRouter.

    ``model_name`` and ``api_key`` are injectable for tests. In normal use the
    values come from ``OPENROUTER_MODEL`` and ``OPENROUTER_API_KEY`` in .env.
    """
    resolved_key = (api_key if api_key is not None else OPENROUTER_API_KEY).strip()
    if not resolved_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. Add it to the project .env file."
        )

    resolved_model = (model_name or OPENROUTER_MODEL).strip()
    if not resolved_model:
        raise ValueError("OPENROUTER_MODEL cannot be empty.")

    return ChatOpenAI(
        model=resolved_model,
        api_key=resolved_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        default_headers={"X-Title": "Task Pilot"},
        max_retries=2,
        timeout=60,
    )
