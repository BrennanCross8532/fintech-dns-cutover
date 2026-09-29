from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from payment_cutover import (
    CutoverRequest,
    CutoverResult,
    InfraiClient,
    InfraiError,
    PaymentHostnameCutover,
)


app = FastAPI(title="Payment hostname cutover")


@app.exception_handler(InfraiError)
async def infrai_error_response(_: Request, error: InfraiError) -> JSONResponse:
    status = error.status_code if 400 <= error.status_code < 500 else 502
    return JSONResponse(
        status_code=status,
        content={"error": {"code": error.code, "detail": error.detail}},
    )


@app.exception_handler(ValueError)
async def decision_error_response(_: Request, error: ValueError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": {"message": str(error)}})


def run_with_client(operation: Callable[[PaymentHostnameCutover], CutoverResult]) -> CutoverResult:
    client = InfraiClient(os.environ["INFRAI_API_KEY"])
    try:
        return operation(PaymentHostnameCutover(client))
    finally:
        client.close()


@app.post("/cutovers/execute", response_model=CutoverResult)
def execute_cutover(change: CutoverRequest) -> CutoverResult:
    return run_with_client(lambda workflow: workflow.execute(change))


@app.post("/cutovers/rollback", response_model=CutoverResult)
def rollback_cutover(change: CutoverRequest) -> CutoverResult:
    return run_with_client(lambda workflow: workflow.rollback(change))
