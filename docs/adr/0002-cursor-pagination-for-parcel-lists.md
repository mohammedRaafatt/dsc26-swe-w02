# 0002. Use cursor pagination for listParcels

## Status
Accepted

## Context
Couriers dispatch parcels while clients page through `state=created`. With offset pagination, a parcel that leaves the filtered set between two requests shifts every later item, so the next page skips parcels or repeats them.

## Decision
`listParcels` uses opaque cursors. A cursor points at a fixed position in creation order (the last item returned), not at a count of matching items. The server issues the cursor, checks it on the next request, and answers 400 for any cursor it did not issue. It reads one item beyond the page size, so `next_cursor` is null on the page holding the last match and there is no empty final page.

## Consequences
Walking the list returns every matching item once, in creation order, even when parcels change state mid-walk. Clients cannot jump to page N and must treat cursors as opaque. The server keeps a small table of issued cursors in memory, which a persistent version would replace with signed or encoded tokens.
