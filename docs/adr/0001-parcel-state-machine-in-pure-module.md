# 0001. Keep the parcel lifecycle in a pure module

## Status
Accepted

## Context
The lifecycle rules (dispatch, fail, retry, return, the three-attempt limit) will keep changing as Sherif learns more about real deliveries. If those rules sit inside route handlers, every change touches HTTP code, and testing a rule needs a running app.

## Decision
All lifecycle rules live in `app/domain.py` as one function, `apply_event(parcel, event)`. It uses only the standard library, takes an immutable `Parcel`, returns a new one, and raises `InvalidTransition` for anything not allowed. The HTTP layer only looks up the parcel, calls `apply_event`, stores the result, and maps `InvalidTransition` to a 409 problem response.

## Consequences
Rule changes happen in one file and can be tested with plain pytest and random event sequences, with no web server. The domain cannot import FastAPI, so the HTTP layer can be replaced without touching the rules. The cost is a thin mapping layer between the two, and the HTTP layer must not re-implement rules, or the two will drift apart.
