import json
from flask import has_request_context, request
from models import AuditEvent, db


def log_event(event_type, *, user_id=None, target_type=None, target_id=None, **details):
    """Write an AuditEvent row. Pulls IP/UA from the active request when available."""
    ip_address = None
    user_agent = None
    if has_request_context():
        ip_address = request.remote_addr
        ua = request.user_agent.string if request.user_agent else None
        if ua:
            user_agent = ua[:255]

    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=json.dumps(details) if details else None,
    )
    db.session.add(event)
