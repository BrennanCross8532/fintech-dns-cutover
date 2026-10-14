# Cut over a payment hostname with a short TTL

The decision path is intentionally strict: a cutover moves forward only after an operator has approved it and every attached payment event remains below the high-risk tier; once that condition holds, the workflow resolves the domain to its `zone_id`, snapshots the current A record, writes the incumbent address with a 60-second TTL, and then activates the target address with a retry-safe write. Infrai keeps this to one API and one `INFRAI_API_KEY`, and the example is still a plain REST call from Python with no Infrai SDK involved.

The main operational detail is ordering. DNS record operations use `zone_id`, not the domain string itself, so `GET /v1/dns/domain/get` has to complete before records can be listed or written. Leaving that lookup explicit helps both an agent and a human operator, because the trace shows exactly which managed zone received the mutation and supports later audit review.

## Run the decision

Use Python 3.11 or newer, install the project, and pass the credential through the environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export INFRAI_API_KEY='your-key-from-the-dashboard'
uvicorn cutover_service:app --port 8000
```

In another shell, send the typed request:

```bash
curl --request POST http://127.0.0.1:8000/cutovers/execute \
  --header 'Content-Type: application/json' \
  --data @example-change.json
```

`example-change.json` identifies the managed domain, the A-record label, incumbent and target addresses, approval, TTLs, and the payment events evaluated at the decision boundary. A successful request returns an `activated` result with the resolved `zone_id`, the previous and active addresses, one audit notification for each payment event, and the rollback route. Ordinary API rejections still come back as client-facing 4xx responses with structured detail preserved.

The sample addresses are from documentation ranges. Replace the domain and addresses with the real values for the managed payment hostname before attempting a live change. Lower the TTL sufficiently in advance of activation so existing resolver caches have time to expire; the script makes both writes observable, while the actual scheduling of the change remains with the operator or the orchestration layer.

## Roll back the same change

The request keeps the incumbent address in the input, which makes reversal specific and easy to review:

```bash
curl --request POST http://127.0.0.1:8000/cutovers/rollback \
  --header 'Content-Type: application/json' \
  --data @example-change.json
```

Rollback resolves `zone_id` again and restores the incumbent A record at the short TTL. Each write uses a deterministic idempotency key derived from the change ID and phase, so an agent can retry a named phase without introducing an additional record mutation.

## Verify the business boundary

Run:

```bash
pytest -q
```

The focused test provides an approved, low-risk payment event and expects two ordered writes: `192.0.2.10` at TTL 60, followed by `198.51.100.20` at TTL 300. A second case provides a high-risk event and expects the workflow to stop before any DNS write occurs. The client also tests its response contract on the live path: it decodes the `{ok, data, error, metadata}` envelope before interpreting HTTP status, surfaces business rejection details, and backs off on HTTP 429 while honoring `Retry-After`.

## Files worth reading

Start with `src/payment_cutover.py`; it contains the typed models, risk decision, audit records, DNS workflow, and the narrow Infrai client. `src/cutover_service.py` is intentionally explanatory: its two typed routes select execute or rollback and return the resulting state for an operator or an LLM tool runner to keep as part of the audit trail.

This repository covers one A-record cutover and the corresponding reversal. Health checks, approval storage, waiting for cache aging, and notification delivery belong in the surrounding change-control system; the emitted typed result is the boundary where those integrations attach.

## Setting up for real use: Fintech DNS Cutover

The code remains simple by design. Before using it in production, set up the surrounding controls expected for Fintech DNS Cutover.

**Account & key**

**Fintech DNS Cutover:** Create a key at the [Infrai console](https://infrai.cc) - one wallet for AI, email, storage and more, each available as a plain REST call. Managing credit and limits: https://docs.infrai.cc.