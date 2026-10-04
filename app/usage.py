"""Persistent allowances for a small free pilot, including actual model calls."""
import os
from fastapi import HTTPException


def positive_limit(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(name + ' must be positive.')
    return value


def chat_allowance(store, user_id):
    wait = store.consume_limits([
        ('chat:user:' + user_id, positive_limit('CHAT_REQUESTS_PER_MINUTE', 20), 60),
    ])
    if wait:
        raise HTTPException(429, 'Please wait a moment before sending another message.',
                            headers={'Retry-After': str(wait)})


class BudgetedModel:
    """Charge the budget only when the graph actually invokes its planner.

    Deterministic follow-ups and calendar reads keep working after the AI
    allowance runs out. Reservations are not refunded for uncertain failures.
    The model factory disables automatic retries in free mode.
    """
    def __init__(self, factory, store, user_id):
        self.factory, self.store, self.user_id = factory, store, user_id

    def with_structured_output(self, *args, **kwargs):
        owner = self

        class Planner:
            def invoke(self, messages, **options):
                wait = owner.store.consume_limits([
                    ('ai:global:minute', positive_limit('AI_REQUESTS_PER_MINUTE', 10), 60),
                    ('ai:global:day', positive_limit('AI_REQUESTS_PER_DAY', 40), 86400),
                    ('ai:user:' + owner.user_id, positive_limit('AI_USER_REQUESTS_PER_DAY', 10), 86400),
                ])
                if wait:
                    raise RuntimeError('The free AI allowance is temporarily exhausted. '
                                       'Try again later; simple calendar reads and pending confirmations remain available.')
                return owner.factory().with_structured_output(*args, **kwargs).invoke(messages, **options)

        return Planner()
