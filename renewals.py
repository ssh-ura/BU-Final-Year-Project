from datetime import date

ALERT_BANDS = ("expired", "urgent", "soon", "upcoming", "future")

BAND_LABELS = {
    "expired": "Expired",
    "urgent": "Urgent",
    "soon": "Soon",
    "upcoming": "Upcoming",
    "future": "Future",
}

BAND_BADGES = {
    "expired": "danger",
    "urgent": "danger",
    "soon": "warning",
    "upcoming": "info",
    "future": "light text-dark",
}


def alert_band(deal_end_date, today=None):
    if today is None:
        today = date.today()
    days = (deal_end_date - today).days
    if days < 0:
        return "expired"
    if days <= 30:
        return "urgent"
    if days <= 90:
        return "soon"
    if days <= 180:
        return "upcoming"
    return "future"


def mortgage_summary(mortgage, today=None):
    if today is None:
        today = date.today()
    band = alert_band(mortgage.deal_end_date, today)
    days_remaining = (mortgage.deal_end_date - today).days
    return {
        "mortgage": mortgage,
        "days_remaining": days_remaining,
        "band": band,
        "band_label": BAND_LABELS[band],
        "band_badge": BAND_BADGES[band],
    }
