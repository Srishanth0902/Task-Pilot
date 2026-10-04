"""Check that the configured model is free and supports structured planning."""
import argparse
import requests
from app.config import OPENROUTER_MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Make one synthetic planner request; never access Calendar')
    args = parser.parse_args()
    try:
        response = requests.get('https://openrouter.ai/api/v1/models', timeout=20)
        response.raise_for_status()
        model = next((item for item in response.json()['data'] if item['id'] == OPENROUTER_MODEL), None)
    except (requests.RequestException, ValueError, KeyError):
        raise SystemExit('Model catalog could not be verified; try again later.') from None
    if not model or any(float(model['pricing'].get(field, '1')) != 0 for field in ('prompt', 'completion')):
        raise SystemExit('Configured model is not a currently listed zero-price model.')
    if 'structured_outputs' not in model.get('supported_parameters', []):
        raise SystemExit('Configured model does not advertise structured outputs.')
    print('Catalog: configured model has zero token prices and structured-output support.', flush=True)
    if args.live:
        from app.llm import create_openrouter_model
        from app.graph_agent import QueryPlan
        try:
            result = create_openrouter_model().with_structured_output(QueryPlan, method='json_schema').invoke(
                'Interpret this synthetic calendar request: List my events tomorrow. Return intent=list. Do not use any external tools.')
        except Exception as error:
            # Exception text can contain generated content or provider details.
            # Report only its type and numeric HTTP status, never the body.
            status = getattr(error, 'status_code', None)
            label = f' HTTP {status}' if isinstance(status, int) else ''
            raise SystemExit(f'Live structured planning failed ({type(error).__name__}{label}). '
                             'Check free-provider availability and credentials; no Calendar calls or automatic retries were made.') from None
        if getattr(result, 'intent', None) != 'list':
            raise SystemExit('Synthetic planning check returned an incorrect intent.')
        print('Live synthetic structured planning passed. No Google Calendar calls were made.')


if __name__ == '__main__':
    main()
