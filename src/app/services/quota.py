from datetime import datetime, timezone

from app.errors import QuotaExceeded, SubscriptionInactive

ACTIVE_STATUS = "active"


def current_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The current calendar month in UTC, as a half-open [start, end) window."""
    now = now or datetime.now(timezone.utc)
    start = now.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def require_active_subscription(status: str) -> None:
    if status != ACTIVE_STATUS:
        raise SubscriptionInactive(status)


def require_within_quota(resource: str, used: int, requested: int, quota: int) -> None:
    """Quotas are inclusive: reaching the limit exactly is allowed, exceeding it is not."""
    if used + requested > quota:
        raise QuotaExceeded(resource=resource, quota=quota, used=used, requested=requested)
