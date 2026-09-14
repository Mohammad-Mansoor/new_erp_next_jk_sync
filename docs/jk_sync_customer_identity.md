# Customer Distributed Identity

## Canonical Identity
The distributed system uses `customer_sync_id` as the immutable global identity.

## Customer Merge Cycles
When a cashier merges `A -> B` and later `B -> C`, the system resolves the canonical identity to `C`.

Deep cycle detection is implemented locally in the `Branch` before transmitting to the `Cloud`. When inserting a new merge:
1. It recursively queries the `Customer Merge Log` to find the ultimate canonical root of the target.
2. If the ultimate root equals the original customer, a `ValidationError` is thrown, halting the cycle (e.g., `C -> A` is blocked).

This guarantees the canonical resolution graph remains an acyclic directed tree.
