class BillingError(Exception):
    """Base class for domain errors raised by the service layer."""


class TenantNotFound(BillingError):
    pass


class IdempotencyConflict(BillingError):
    """The same idempotency key was reused for a different request."""


class QuotaExceeded(BillingError):
    def __init__(self, resource: str, quota: int, used: int, requested: int):
        self.resource = resource
        self.quota = quota
        self.used = used
        self.requested = requested
        super().__init__(f"Monthly {resource} quota exceeded")


class SubscriptionInactive(BillingError):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"Subscription is {status}")
