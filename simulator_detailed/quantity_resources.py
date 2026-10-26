"""Quantity claims on the existing ResourceRegistry, with no second environment."""
from dataclasses import dataclass


@dataclass(frozen=True)
class QuantityClaim:
    resource_id: str
    amount: int
    unit: str


@dataclass(frozen=True)
class QuantityLease:
    lease_id: int
    owner: str
    claims: tuple[QuantityClaim, ...]


class QuantityRegistryMixin:
    def _init_quantities(self):
        self._quantity_pools = {}
        self._quantity_leases = {}
        self._quantity_waiters = {}
        self._quantity_sequence = 0

    def register_quantity(self, resource_id, capacity, unit):
        if type(resource_id) is not str or not resource_id or type(unit) is not str or not unit:
            raise ValueError("E_QUANTITY_IDENTITY")
        if resource_id in self._quantity_pools or resource_id in self._entries:
            raise ValueError("E_QUANTITY_DUPLICATE")
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("E_QUANTITY_CAPACITY")
        self._quantity_pools[resource_id] = dict(capacity=capacity, unit=unit, used=0, peak=0)

    def _validate_claims(self, claims):
        if not claims:
            raise ValueError("E_QUANTITY_IDENTITY")
        if any(type(c.resource_id) is not str or type(c.unit) is not str for c in claims):
            raise ValueError("E_QUANTITY_IDENTITY")
        if len({c.resource_id for c in claims}) != len(claims):
            raise ValueError("E_QUANTITY_IDENTITY")
        for claim in claims:
            pool = self._quantity_pools.get(claim.resource_id)
            if (pool is None or pool["unit"] != claim.unit or type(claim.amount) is not int
                    or not 0 < claim.amount <= pool["capacity"]):
                raise ValueError("E_QUANTITY_CLAIM")
            if type(pool["used"]) is not int or not 0 <= pool["used"] <= pool["capacity"]:
                raise ValueError("E_QUANTITY_CONSERVATION")

    def quantities_available(self, claims):
        self._validate_claims(claims)
        return all(self._quantity_pools[c.resource_id]["used"] + c.amount
                   <= self._quantity_pools[c.resource_id]["capacity"] for c in claims)

    def try_acquire_quantities(self, claims, owner):
        if getattr(self, "_closing", False):
            raise RuntimeError("E_REGISTRY_CLOSED")
        claims = tuple(claims)
        if type(owner) is not str or not owner:
            raise ValueError("E_QUANTITY_OWNER")
        if not self.quantities_available(claims):
            return None
        self._quantity_sequence += 1
        lease = QuantityLease(self._quantity_sequence, owner, claims)
        for claim in claims:
            pool = self._quantity_pools[claim.resource_id]
            pool["used"] += claim.amount
            pool["peak"] = max(pool["peak"], pool["used"])
        self._quantity_leases[lease.lease_id] = lease
        return lease

    def acquire_quantities(self, claims, owner):
        self._validate_claims(claims)
        token = object()
        self._quantity_waiters[token] = owner
        lease = None
        try:
            while lease is None:
                lease = self.try_acquire_quantities(claims, owner)
                if lease is None:
                    yield self._released
            return lease
        finally:
            self._quantity_waiters.pop(token, None)

    def _drop_quantity_lease(self, lease, *, strict, notify):
        if self._quantity_leases.get(lease.lease_id) is not lease:
            if strict:
                raise ValueError("E_QUANTITY_STALE_LEASE")
            return False
        # Validate every decrement before changing any counter. Cleanup releases
        # real ownership; it must not hide corruption by setting counters to zero.
        self._validate_claims(lease.claims)
        if any(self._quantity_pools[c.resource_id]["used"] < c.amount for c in lease.claims):
            raise ValueError("E_QUANTITY_CONSERVATION")
        for claim in lease.claims:
            self._quantity_pools[claim.resource_id]["used"] -= claim.amount
        del self._quantity_leases[lease.lease_id]
        if notify:
            self._notify_release()
        return True

    def release_quantities(self, lease):
        self._drop_quantity_lease(lease, strict=True, notify=True)

    def discard_quantities(self, lease):
        """Idempotent error-cleanup release; do not run observer callbacks."""
        return self._drop_quantity_lease(lease, strict=False, notify=False)

    def quantities_released(self):
        return (not self._quantity_leases and not self._quantity_waiters
                and all(type(p["used"]) is int and p["used"] == 0 for p in self._quantity_pools.values()))

    def quantity_snapshot(self):
        return {rid: dict(entry) for rid, entry in sorted(self._quantity_pools.items())}
