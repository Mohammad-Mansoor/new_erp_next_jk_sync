# Stock Delivery & Idempotency Proof

## Delivery Mechanism
1. Cloud creates `Cloud Stock Sync Log` (auto-increment identity).
2. Branch polls un-ACKed logs.
3. Branch creates an atomic claim in `Local Stock Sync Log` using the Cloud's globally unique ID.
4. If successful, Branch applies the Stock Entry and marks it `PROCESSED`.
5. Branch transmits an ACK to the Cloud.

## Lost ACK Proof
If the ACK in step 5 is lost:
1. Cloud assumes Branch never received it.
2. Cloud resends the delta on the next poll.
3. Branch attempts to insert into `Local Stock Sync Log`.
4. MariaDB throws `DuplicateEntryError`.
5. Branch detects the row is already `PROCESSED` and safely returns an idempotent success, preventing a double-count.

## Ordering Proof
Deltas are sorted by sequence number (`name`) immediately upon arrival at the Branch and applied sequentially in a single batch. If sequence numbers skip, they will still be applied in relative order.
