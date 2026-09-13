"""Calibration knobs for the deterministic engine. Defaults are the current best-known conventions."""

from __future__ import annotations

from enum import StrEnum
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
