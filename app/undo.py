"""Undo a single create or time move, with fresh reads and conditional writes.

Undo runs against the calendar provider interface, so it behaves identically
on Google Calendar and on the native calendar. The safety rule is the same for
both: re-read the event, refuse if anything changed since the action, and make
the reversal a conditional write so a concurrent edit loses rather than being
silently overwritten.
"""
from datetime import datetime

from app.calendar_tools import as_provider
from app.scheduling import overlapping_events


def undo_change(provider, record):
    provider = as_provider(provider)
    expected = record['after']
    if not expected.get('etag'):
        raise ValueError('The original event version was not saved. Undo is unavailable for this change.')

    current = provider.get_event(expected['event_id'])
    if not current or not current.get('etag') or current.get('status') == 'cancelled':
        raise ValueError('That event is no longer available to undo.')
    if current['etag'] != expected['etag']:
        raise ValueError('This event changed after your request. Review it in Calendar before making another change.')
    for key in ('title', 'start', 'end', 'description', 'location'):
        if current.get(key) != expected.get(key):
            raise ValueError('This event changed after your request. Undo has stopped.')

    if record['kind'] == 'create':
        result = provider.delete_event(expected['event_id'], if_match=current['etag'])
    else:
        before = record['before']
        start, end = datetime.fromisoformat(before['start']), datetime.fromisoformat(before['end'])
        window = provider.list_events(250, start, end)
        if not window.get('success') or window.get('count', 0) >= 250:
            raise ValueError('Could not fully check the original time. Undo has stopped.')
        if overlapping_events(window['events'], start, end, exclude_event_ids={expected['event_id']}):
            raise ValueError('The original time is now occupied. Choose another time instead.')
        result = provider.update_event(
            expected['event_id'], start=start, end=end, if_match=current['etag'],
        )

    if not result.get('success'):
        raise ValueError(result.get('error') or 'Undo could not be completed.')
    return f"Undone: {expected.get('title', 'calendar event')}."
