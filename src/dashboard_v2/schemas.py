"""Pydantic schemas for the Dashboard V2 safe API surface."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class CashflowCreate(BaseModel):
    flow_date: date
    flow_type: Literal["DEPOSIT", "WITHDRAW"]
    amount: float = Field(gt=0)
    note: str = ""
    account_id: str = "default"
    currency: str = "CNY"


class IndexFundSnapshotCreate(BaseModel):
    snapshot_date: date
    fund_code: str = Field(min_length=1)
    shares: float = Field(ge=0)
    cost_amount: float = Field(ge=0)
    note: str = ""


class SafeWriteResult(BaseModel):
    id: str
    status: str = "ok"


JsonDict = dict[str, Any]
