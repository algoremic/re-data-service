"""Static reference values for parity assertions.

Expected values are derived from known test fixture data using the data-service
_calc_stat, lookup_ppsf, and latest_index implementations.
They document what the data-service MUST produce on those inputs.
Any code change that alters these outputs is a regression.
"""

from __future__ import annotations

import math
from decimal import Decimal

# ---------------------------------------------------------------------------
# Shared CSV fixture rows (identical to test_market_parity.py usage)
# ---------------------------------------------------------------------------

JVC_ROWS: list[str] = [
    "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-15\n",
    "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-20\n",
    "3,1900000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-25\n",
    "4,2050000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-28\n",
    "5,2200000,105,Jumeirah Village Circle,,flat,2,Sales,Sale,2026-01-28\n",
]

INDEX_ROWS: list[str] = [
    "2026-01-01,1.0,1.0,1.1,1.1,1.2,1.2,0.9,0.9,1.0,1.0,1.1,1.1,0.8,0.8,0.9,0.9,1.0,1.0\n",
    "2026-02-01,1.1,1.1,1.2,1.2,1.3,1.3,1.0,1.0,1.1,1.1,1.2,1.2,0.9,0.9,1.0,1.0,1.1,1.1\n",
]

# ---------------------------------------------------------------------------
# PPSF expected — derived from JVC_ROWS (4 × 1BR at 100 sqm, DLD_AREA_UNIT=sqm)
# Prices: 2000000, 1900000, 2050000, 2100000 AED
# sqft = 100 * SQM_TO_SQFT (10.7639104167); n=4 < 10 → no trim
# sorted ppsf: [1765.16…, 1858.06…, 1904.51…, 1950.96…]
# median = mean of middle two = (ppsf[1] + ppsf[2]) / 2
# Exact value verified from gate run against data-service _calc_stat.
# ---------------------------------------------------------------------------

EXPECTED_JVC_1BR_PPSF_AED_SQFT: Decimal = Decimal("1881.286560001699238222163455")

# n returned by _calc_stat for 4 inputs (original count, not trimmed count)
EXPECTED_JVC_1BR_N: int = 4
EXPECTED_JVC_1BR_CONFIDENCE_APPROX: float = math.log(5) / math.log(101)

# ---------------------------------------------------------------------------
# Index expected — latest row (2026-02-01), segment="all", horizon="yearly"
# all_yearly_index column = 1.3; all_yearly_price_index = 1.3
# ---------------------------------------------------------------------------

EXPECTED_INDEX_DATE: str = "2026-02-01"
EXPECTED_INDEX_VALUE: Decimal = Decimal("1.3")
EXPECTED_PRICE_INDEX_VALUE: Decimal = Decimal("1.3")

# ---------------------------------------------------------------------------
# _calc_stat on 15 evenly-spaced values [100, 110, ..., 240]
# n=15 ≥ 10 → k = int(15 * 0.10) = 1; trimmed = s[1:14] = 13 values [110..230]
# median of 13 = s2[6] = 170
# confidence = log(n+1) / log(101) = log(16) / log(101)
# n returned = 15 (original count; data-service _calc_stat stores n, not n_trimmed)
# ---------------------------------------------------------------------------

EXPECTED_CALC_STAT_N15_MEDIAN: Decimal = Decimal("170")
EXPECTED_CALC_STAT_N15_N: int = 15
EXPECTED_CALC_STAT_N15_CONFIDENCE_APPROX: float = math.log(16) / math.log(101)

# ---------------------------------------------------------------------------
# Ingest parity — expected keys and PPSF values from the two-file ingest fixture
#
# Fixture files:
#   Transactions.csv  (DLD schema, DLD_AREA_UNIT=sqm):
#     row 1: Dubai Marina, Tower A, flat, 2BR, 100 sqm, 2 000 000 AED  → accepted
#     row 2: Area, Mortgage Bldg, flat, 1BR, 100 sqm, 1 000 000 AED   → filtered (mortgage)
#   transactions-2026-02-23.csv  (recent-transactions schema):
#     row 1: JVC, apartment, 1BR, 80 sqm, 1 500 000 AED               → accepted
#
# Only 2 keys expected; mortgage row must be absent.
# PPSF = price_aed / (area_sqm * SQM_TO_SQFT); verified via run_ingest gate run.
# ---------------------------------------------------------------------------

INGEST_PARITY_KEY_MARINA: tuple[str, str, str, str, int] = (
    "dubai",
    "dubai marina",
    "tower a",
    "flat",
    2,
)
INGEST_PARITY_PPSF_MARINA: Decimal = Decimal("1858.060800001678259972507116")

INGEST_PARITY_KEY_JVC: tuple[str, str, str, str, int] = (
    "dubai",
    "jvc",
    "",
    "apartment",
    1,
)
INGEST_PARITY_PPSF_JVC: Decimal = Decimal("1741.932000001573368724225421")

# The mortgage row must NOT appear in transactions (filtered by procedure).
INGEST_PARITY_KEY_MORTGAGE_FILTERED: tuple[str, str, str, str, int] = (
    "dubai",
    "area",
    "mortgage bldg",
    "flat",
    1,
)
