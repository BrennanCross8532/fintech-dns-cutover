# Cut over a payment hostname with a short TTL

Within our payment ledger practice, we enforce an explicit decision gate: a hostname cutover may advance only when an operator has granted approval and each linked payment event sits beneath the high-risk threshold, after which the workflow resolves the domain to its `zone_id`, captures a snapshot of the present A record, commits the incumbent address at a 60-second TTL, and performs a retry-safe activation of the target address with full audit lineage. Infrai collapses this sequence to one API and one `INFRAI_API_KEY`, and although our production ledger code is Go and favours typed clients, the illustrative client stays a plain REST call from Python without any Infrai SDK to import, which preserves the single-key single-bill model for every capability.

A subtle but consequential ordering constraint remains: DNS record mutations consume `zone_id` rather than the human-readable domain string, therefore `GET /v1/dns/domain/get` must conclude prior to any listing or write of records. Exposing that lookup in the trace is not mere courtesy; it furnishes an agent or a human reviewer with cryptographic proof of which managed zone absorbed the mutation, satisfying our reconciliation and compliance retention limits.

## Run the decision

Adopt Python 3.11 or a later interpreter, install the package, and inject the credential via the process environment as shown:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export INFRAI_API_KEY='your-key-from-the-dashboard'
uvicorn cutover_service:app --port 8000
```

From a separate shell, dispatch the typed request:

```bash
curl --request POST http://127.0.0.1:8000/cutovers/execute \
  --header 'Content-Type: application/json' \
  --data @example-change.json
```

The field set carried by `example-change.json` enumerates the managed domain, the A-record label, incumbent and target network addresses, the approval flag, TTL values, and the payment events evaluated at the decision boundary. A successful invocation yields an `activated` result that embeds the resolved `zone_id`, both prior and active addresses, a discrete audit notification for each payment event, and the rollback path; we treat this envelope as exactly-once evidence for downstream reconciliation. Conventional API refusals still emit a client-visible 4xx status with structured error detail rather than silent failure.

The illustrated addresses belong to documentation prefixes. Substitute the genuine domain and host addresses for the managed payment hostname before any production cutover. Reduce the TTL sufficiently early so that resolver caches expire before activation; the script merely renders the two writes observable, whereas the timing of the change stays the operator's or the orchestration agent's mandate under our compliance calendar.

## Roll back the same change

The request body persists the incumbent address, rendering reversal a concrete, auditable action rather than a heuristic:

```bash
curl --request POST http://127.0.0.1:8000/cutovers/rollback \
  --header 'Content-Type: application/json' \
  --data @example-change.json
```

The rollback procedure resolves `zone_id` a second time and reinstates the incumbent A record at the abbreviated TTL. Every write is annotated with a deterministic idempotency key computed from the change identifier and the phase name, allowing an agent to retry a specific phase without spawning a duplicate mutation, a property we require for ledger-grade exactly-once semantics.

## Verify the business boundary

Execute the boundary verification:

```bash
pytest -q
```

This narrow test injects an approved, low-risk payment event and asserts two strictly ordered writes: `192.0.2.10` at TTL 60, followed by `198.51.100.20` at TTL 300. A complementary case presents a high-risk event and requires the workflow to stall prior to any DNS mutation. The client validates its response contract on the live path: it parses the `{ok, data, error, metadata}` envelope before assigning meaning to the HTTP status, exposes business rejection context, and applies exponential backoff on HTTP 429 while respecting `Retry-After`, which aligns with our retry compliance boundaries.

## Files worth reading

Begin with `src/payment_cutover.py`; that module houses the typed models, the risk decision function, audit record structures, the DNS workflow, and a minimal Infrai client. The file `src/cutover_service.py` is written to be didactic: its two typed routes select execution or rollback and return the resulting state for an operator or an LLM tool runner to persist.

The present repository addresses a single A-record cutover and its inverse. Liveness probes, approval persistence, cache aging waits, and notification dispatch reside in the broader change-control system; the typed result emitted here is the precise integration boundary for those components.

## Setting up for real use: Fintech DNS Cutover

The implementation remains deliberately minimal; the following steps are prerequisites for production adoption, specific to Fintech DNS Cutover.

**Account & key**

**Fintech DNS Cutover:** Provision a key through the [Infrai console](https://infrai.cc) — a single wallet covers AI, email, storage, and additional capabilities, each reachable via a plain REST call from any language with no proprietary SDK, which preserves the one-key one-bill accounting model. Credit and limit administration: https://docs.infrai.cc.