from datetime import datetime, timezone

import audit
from models import (
    ADVISER_DRIVEN_STAGES,
    ALLOWED_TRANSITIONS,
    CASE_STAGES,
    DOCUMENT_CATEGORY_KEYS,
    Document,
    FactFind,
)


class TransitionError(Exception):
    pass


def can_advance(user, new_stage):
    if user.role != "client":
        return False
    if new_stage not in CASE_STAGES:
        return False
    if ALLOWED_TRANSITIONS.get(user.case_stage) != new_stage:
        return False
    return _prerequisites_met(user, new_stage)


def advance_stage(user, new_stage, *, actor):
    if user.role != "client":
        raise TransitionError("Only client cases have a stage.")
    if new_stage not in CASE_STAGES:
        raise TransitionError(f"Unknown stage: {new_stage}")
    if ALLOWED_TRANSITIONS.get(user.case_stage) != new_stage:
        raise TransitionError(
            f"Cannot move from {user.case_stage} to {new_stage}."
        )
    if not _prerequisites_met(user, new_stage):
        raise TransitionError(
            f"Prerequisites for {new_stage} are not satisfied."
        )

    from_stage = user.case_stage
    user.case_stage = new_stage
    user.stage_updated_at = datetime.now(timezone.utc)

    if actor is not None:
        actor_user_id = actor.id
        actor_role = actor.role
    else:
        actor_user_id = None
        actor_role = "system"

    audit.log_event(
        "case_stage_advanced",
        user_id=actor_user_id,
        target_type="user",
        target_id=user.id,
        from_stage=from_stage,
        to_stage=new_stage,
        actor_role=actor_role,
    )


def auto_advance_if_eligible(user, new_stage, *, actor):
    """No-op when the user is already at or past new_stage; otherwise advance."""
    if user.role != "client":
        return False
    if user.case_stage is None:
        return False
    current_index = CASE_STAGES.index(user.case_stage)
    target_index = CASE_STAGES.index(new_stage)
    if current_index >= target_index:
        return False
    if ALLOWED_TRANSITIONS.get(user.case_stage) != new_stage:
        return False
    advance_stage(user, new_stage, actor=actor)
    return True


def derive_initial_stage(user):
    """Derive a starting stage for an existing user from their data."""
    if user.role != "client":
        return None
    if user.esigned:
        return "under_review"

    fact_find = FactFind.query.filter_by(user_id=user.id).first()
    if fact_find is not None and fact_find.is_complete:
        fact_find_done = True
    else:
        fact_find_done = False

    uploaded_categories = set()
    for document in Document.query.filter_by(user_id=user.id).all():
        uploaded_categories.add(document.category)
    docs_done = DOCUMENT_CATEGORY_KEYS.issubset(uploaded_categories)

    if docs_done:
        return "awaiting_esign"
    if fact_find_done:
        return "documents_pending"
    return "fact_find_in_progress"


def _prerequisites_met(user, new_stage):
    if new_stage == "documents_pending":
        fact_find = FactFind.query.filter_by(user_id=user.id).first()
        if fact_find is None:
            return False
        if not fact_find.is_complete:
            return False
        return True

    if new_stage == "awaiting_esign":
        uploaded = set()
        for document in Document.query.filter_by(user_id=user.id).all():
            uploaded.add(document.category)
        return DOCUMENT_CATEGORY_KEYS.issubset(uploaded)

    if new_stage == "under_review":
        if user.esigned:
            return True
        return False

    if new_stage in ADVISER_DRIVEN_STAGES:
        fact_find = FactFind.query.filter_by(user_id=user.id).first()
        if fact_find is None:
            return False
        if not fact_find.is_complete:
            return False
        if not user.esigned:
            return False
        return True

    return True
