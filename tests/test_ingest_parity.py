"""Ingest parity tests — data-service run_ingest on known-good fixtures.

Verifies that the data-service ingest pipeline produces exactly the expected
transaction keys and PPSF values for a two-file fixture (DLD + recent-transactions
schema), and that filtered rows (mortgage) are absent.

Forward-compatible with ST-056 TASK-1 (benchmarks.py delete): no dependency on
realestate-core-service or dubai_re_calc.benchmarks.
"""

from __future__ import annotations

from decimal import Decimal

from re_data.ingest.runner import run_ingest
from tests.conftest import make_settings, write_dld_csv, write_recent_csv
from tests.helpers.parity_reference import (
    INGEST_PARITY_KEY_JVC,
    INGEST_PARITY_KEY_MARINA,
    INGEST_PARITY_KEY_MORTGAGE_FILTERED,
    INGEST_PARITY_PPSF_JVC,
    INGEST_PARITY_PPSF_MARINA,
)


def test_parity_with_benchmarks_py(tmp_path, monkeypatch):
    """Ingest of DLD + recent-transactions CSVs produces correct keys and PPSF values.

    Assertions preserve original test strength:
    - Both expected keys are present.
    - No unexpected extra keys (bidirectional coverage).
    - PPSF values match known-correct Decimal constants within Decimal('0.0001').
    - The mortgage row is absent (filtered by procedure).
    """
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "1,2000000,100,Dubai Marina,Tower A,flat,2,Sales,Sale,2026-01-15\n",
            "2,1000000,100,Area,Mortgage Bldg,flat,1,Mortgages,Mortgage,2026-01-01\n",
        ],
    )
    write_recent_csv(
        tmp_path / "transactions-2026-02-23.csv",
        ["Sell,1500000,80,JVC,apartment,1\n"],
    )
    paths = [tmp_path / "transactions-2026-02-23.csv", tmp_path / "Transactions.csv"]
    monkeypatch.setenv("DLD_AREA_UNIT", "sqm")
    monkeypatch.setenv("PPSF_MIN", "100")
    monkeypatch.setenv("PPSF_MAX", "20000")

    settings = make_settings(
        tmp_path,
        benchmarks_paths=f"{paths[0]};{paths[1]}",
        index_file="",
    )
    snap = run_ingest(settings)

    # Mortgage row must be absent — filtered by procedure.
    assert INGEST_PARITY_KEY_MORTGAGE_FILTERED not in snap.transactions, (
        "mortgage row should be filtered; found in transactions"
    )

    # Both accepted rows must be present.
    assert INGEST_PARITY_KEY_MARINA in snap.transactions, (
        f"expected key {INGEST_PARITY_KEY_MARINA!r} missing from transactions"
    )
    assert INGEST_PARITY_KEY_JVC in snap.transactions, (
        f"expected key {INGEST_PARITY_KEY_JVC!r} missing from transactions"
    )

    # Bidirectional: no extra unexpected keys.
    expected_keys = {INGEST_PARITY_KEY_MARINA, INGEST_PARITY_KEY_JVC}
    actual_keys = set(snap.transactions.keys())
    assert actual_keys == expected_keys, (
        f"unexpected keys in transactions: {actual_keys - expected_keys}"
    )

    # PPSF values match known-correct constants within tolerance.
    marina_vals = list(snap.transactions[INGEST_PARITY_KEY_MARINA])
    assert len(marina_vals) == 1
    assert abs(marina_vals[0] - INGEST_PARITY_PPSF_MARINA) < Decimal("0.0001"), (
        f"Marina PPSF mismatch: {marina_vals[0]} vs {INGEST_PARITY_PPSF_MARINA}"
    )

    jvc_vals = list(snap.transactions[INGEST_PARITY_KEY_JVC])
    assert len(jvc_vals) == 1
    assert abs(jvc_vals[0] - INGEST_PARITY_PPSF_JVC) < Decimal("0.0001"), (
        f"JVC PPSF mismatch: {jvc_vals[0]} vs {INGEST_PARITY_PPSF_JVC}"
    )
