"""Compare equivalent read requests with live OpenRouter and fake Calendar.

Run with python -m evaluation.benchmark_reads. Uses model credits, never Google.
The polite variant intentionally exercises the general model path.
"""
from statistics import median
from time import perf_counter

from app.graph_agent import CalendarConversation
from app.llm import create_openrouter_model
from tests.test_calendar_service import FakeService


def main():
    model = create_openrouter_model()
    for query in ['Please show my tasks tomorrow', 'Show my tasks tomorrow']:
        samples = []
        for _ in range(3):
            chat = CalendarConversation(model, FakeService())
            started = perf_counter()
            result = chat.ask(query)
            samples.append(round(perf_counter() - started, 3))
            assert result['verified'], result['response']
        print(f'{query}: seconds={samples}; median={median(samples):.3f}', flush=True)


if __name__ == '__main__':
    main()
