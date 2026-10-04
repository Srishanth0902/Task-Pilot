"""OpenRouter-backed LangChain model construction.

OpenRouter implements the OpenAI chat-completions interface, so LangChain's
ChatOpenAI adapter can use Qwen and other compatible models without changing
the calendar agent. Only configuration belongs in this module; calendar logic
remains provider-independent.
"""

import os
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

    free_only = os.getenv('OPENROUTER_FREE_ONLY', 'true').lower() not in {'false', '0', 'no'}
    if free_only and resolved_model != 'openrouter/free' and not resolved_model.endswith(':free'):
        raise ValueError('Free mode requires openrouter/free or a model with a :free variant.')

    return ChatOpenAI(
        model=resolved_model,
        api_key=resolved_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        default_headers={"X-Title": "Task Pilot"},
        # Do not spend several reservations on implicit HTTP retries. A failed
        # planner call is reported; no paid model fallback is configured.
        max_retries=0 if free_only else 2,
        # Calendar context must not be routed to data-collecting providers.
        # Fail closed if no compatible endpoint exists; never relax privacy
        # or switch to a paid endpoint to make a request succeed.
        extra_body={'provider': {'require_parameters': True,
                                'data_collection': 'deny',
                                **({'max_price': {'prompt': 0, 'completion': 0}} if free_only else {})}},
        timeout=60,
    )
