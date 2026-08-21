from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    batches,
    bbg_indices,
    data_sources,
    dividend_adjusted_prices,
    dividends,
    funds,
    index_memberships,
    instruments,
    macro,
    monthly_fundamentals,
    prices,
    reports,
    uploads,
)
from app.core.config import settings

app = FastAPI(title="Portfolio Manage API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(instruments.router)
app.include_router(prices.router)
app.include_router(dividend_adjusted_prices.router)
app.include_router(dividends.router)
app.include_router(macro.router)
app.include_router(index_memberships.router)
app.include_router(monthly_fundamentals.router)
app.include_router(uploads.router)
app.include_router(batches.router)
app.include_router(data_sources.router)
app.include_router(funds.router)
app.include_router(bbg_indices.router)
app.include_router(reports.router)


@app.get("/health")
def health():
    return {"status": "ok"}
