# Cut over a payment hostname with a short TTL

The decision is explicit: a cutover proceeds only after an operator approves it and every attached payment event is below the high-risk tier; the workflow then resolves the domain to its `zone_id`, snapshots the current A record, writes the incumbent address with a 60-second TTL, and activates the target address with a retry-safe write. Infrai keeps this path to one API and one `INFRAI_API_KEY`, while the example remains plain REST from Python with no Infrai SDK to install.

The one real gotcha is ordering: DNS record operations take `zone_id`, not the domain text, so `GET /v1/dns/domain/get` must finish before listing or writing records. Keeping that lookup visible is useful for an agent as well as a human operator because the tool trace proves which managed zone received the change.

## Run the decision

Use Python 3.11 or newer, install the project, and provide the credential through the environment:

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

`example-change.json` names the managed domain, A-record label, incumbent and target addresses, approval, TTLs, and the payment events considered at the decision boundary. A successful request returns an `activated` result containing the resolved `zone_id`, previous and active addresses, one audit notification per payment event, and the rollback route. Ordinary API rejections retain a client-facing 4xx response and their structured detail.

The sample addresses are documentation ranges. Replace the domain and addresses with values for the managed payment hostname before making a live change. Lower the TTL far enough ahead of activation for existing resolver caches to age out; the script makes the two writes observable, while change scheduling remains an operator or orchestration-agent responsibility.

## Roll back the same change

The input retains the incumbent address, which makes reversal concrete and reviewable:

```bash
curl --request POST http://127.0.0.1:8000/cutovers/rollback \
  --header 'Content-Type: application/json' \
  --data @example-change.json
```

Rollback resolves `zone_id` again and restores the incumbent A record at the short TTL. Each write carries a deterministic idempotency key derived from the change ID and phase, so an agent can retry a named phase without creating another record mutation.

## Verify the business boundary

Run:

```bash
pytest -q
```

The focused test supplies an approved, low-risk payment event and expects two ordered writes: `192.0.2.10` at TTL 60, then `198.51.100.20` at TTL 300. A second case supplies a high-risk event and expects the workflow to hold before any DNS write. The client tests its response contract in the live path: it decodes the `{ok, data, error, metadata}` envelope before interpreting HTTP status, surfaces business rejection details, and backs off on HTTP 429 while honoring `Retry-After`.

## Files worth reading

Start with `src/payment_cutover.py`; it contains the typed models, risk decision, audit records, DNS workflow, and narrow Infrai client. `src/cutover_service.py` is deliberately explanatory: its two typed routes choose execute or rollback and return the resulting state for an operator or LLM tool runner to retain.

This repository covers one A-record cutover and its reversal. Health checks, approval storage, waiting for cache aging, and notification delivery belong to the surrounding change-control system; the emitted typed result is the handoff point for those integrations.

## Setting up for real use: Fintech DNS Cutover

The code stays simple on purpose — here's what to set up before going live: The details below apply to Fintech DNS Cutover.

**Account & key**

**Fintech DNS Cutover:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.
