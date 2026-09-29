"""Undo a single create or time move, with fresh reads and conditional writes."""
from datetime import datetime
from app.config import CALENDAR_ID
from app.calendar_service import _normalise_event, get_events
from app.scheduling import overlapping_events


def undo_change(service, record):
    expected = record['after']
    if not expected.get('etag'):
        raise ValueError('The original event version was not saved. Undo is unavailable for this change.')
    resource = service.events().get(calendarId=CALENDAR_ID, eventId=expected['event_id']).execute()
    current = _normalise_event(resource)
    if not resource.get('etag') or current.get('status') == 'cancelled':
        raise ValueError('That event is no longer available to undo.')
    if expected.get('etag') and resource['etag'] != expected['etag']:
        raise ValueError('This event changed after your request. Review it in Calendar before making another change.')
    for key in ('title', 'start', 'end', 'description', 'location'):
        if current.get(key) != expected.get(key):
            raise ValueError('This event changed after your request. Undo has stopped.')
    if record['kind'] == 'create':
        operation = service.events().delete(calendarId=CALENDAR_ID, eventId=expected['event_id'])
    else:
        before = record['before']
        start, end = datetime.fromisoformat(before['start']), datetime.fromisoformat(before['end'])
        window = get_events(service, 250, start, end)
        if not window.get('success') or window.get('count', 0) >= 250:
            raise ValueError('Could not fully check the original time. Undo has stopped.')
        if overlapping_events(window['events'], start, end, exclude_event_ids={expected['event_id']}):
            raise ValueError('The original time is now occupied. Choose another time instead.')
        operation = service.events().patch(calendarId=CALENDAR_ID, eventId=expected['event_id'], body={
            'start': {'dateTime': before['start']}, 'end': {'dateTime': before['end']},
        })
    operation.headers['If-Match'] = resource['etag']
    operation.execute()
    return f"Undone: {expected.get('title', 'calendar event')}."
