"""Calibration knobs for the deterministic engine. Defaults are the current best-known conventions."""

from __future__ import annotations

from enum import StrEnum
import calendar
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Estimator(StrEnum):
    MEAN_LAST_3 = "mean3"
    MEAN_LAST_6 = "mean6"
    MEAN_ALL = "mean_all"
    MEDIAN_ALL = "median_all"
    MAX_LAST_3 = "max3"
    MAX_LAST_6 = "max6"
    MAX_ALL = "max_all"
    LAST = "last"


class Rounding(StrEnum):
    NONE = "none"
    CENT = "cent"
    UNIT = "unit"
    HUNDRED = "hundred"


IntradayStep = Literal["credit", "payment", "debit"]


class EngineKnobs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: int = 90
    # "days": request_date + horizon_days. "month_end": last day of the calendar month that ends the
    # forecast period (request month + horizon_months - 1).
    horizon_mode: Literal["days", "month_end"] = "month_end"
    horizon_months: int = 3
    min_occurrences: int = 3
    cadence_window: int = 6
    stale_cadence_multiple: float = 1.5
    monthly_cadence_range: tuple[int, int] = (27, 32)
    estimator: Estimator = Estimator.MEDIAN_ALL
    rounding: Rounding = Rounding.NONE
    intraday_order: tuple[IntradayStep, IntradayStep, IntradayStep] = ("credit", "payment", "debit")
    reserve_duplicate_suspects: bool = False
    scheduled_credit_dedupe_days: int = 5
    project_confirmed_credits_forward: bool = True
    # Evidence readings the agent may judge between (see agent/scenarios.py).
    count_undated_one_time_income: bool = False
    reduced_pay_continues: bool = False
    require_deadline: bool = True
    max_spending_changes: int = 3


def horizon_end(start: date, knobs: EngineKnobs) -> date:
    """Last day included in the forecast period."""
    if knobs.horizon_mode == "days":
        return start + timedelta(days=knobs.horizon_days)
    month_index = start.month - 1 + knobs.horizon_months - 1
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])
