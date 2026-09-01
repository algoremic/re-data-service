"""Ingest extension tests — AC-1.1 through AC-1.3, TASK-2 runner-wiring checks.

Covers row-level CompRecord retention (ADR-016) alongside the pre-existing
grouped ppsf aggregates, and the area-keyed DatasetSnapshot.comp_records_by_area
index built once in run_ingest().
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from re_data.ingest.csv_transactions import ingest_csv_file
from re_data.ingest.runner import run_ingest
from tests.conftest import make_settings, write_dld_csv, write_recent_csv

# ---------------------------------------------------------------------------
# AC-1.1 — DLD row accepted -> CompRecord present with all required fields
# ---------------------------------------------------------------------------


def test_dld_row_accepted_produces_comp_record(tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "12345678,2000000,100,Jumeirah Village Circle,Binghatti Heights,flat,1,Sales,Sale,2024-03-15\n"
        ],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    acc = ingest_csv_file(tmp_path / "Transactions.csv", settings)

    assert len(acc.comp_records) == 1
    rec = acc.comp_records[0]
    assert rec.transaction_date == "2024-03-15"
    assert rec.price_aed == Decimal("2000000")
    assert rec.size_sqft > 0
    assert rec.aed_per_sqft > 0
    assert rec.emirate == "dubai"
    assert rec.area == "jumeirah village circle"
    assert rec.building == "binghatti heights"
    assert rec.unit_type == "flat"
    assert rec.bedrooms == 1
    assert rec.transaction_id == "12345678"


# ---------------------------------------------------------------------------
# AC-1.2 — recent-transactions row -> CompRecord with filename date, no txn id
# ---------------------------------------------------------------------------


def test_recent_transactions_row_uses_filename_date_and_null_txn_id(tmp_path):
    write_recent_csv(
        tmp_path / "transactions-2026-02-23.csv",
        ["Sell,1500000,80,Marina,apartment,1\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "transactions-2026-02-23.csv"),
        index_file="",
    )
    acc = ingest_csv_file(tmp_path / "transactions-2026-02-23.csv", settings)

    assert len(acc.comp_records) == 1
    rec = acc.comp_records[0]
    assert rec.transaction_date == "2026-02-23"
    assert rec.transaction_id is None
    assert rec.area == "marina"
    assert rec.building == ""


# ---------------------------------------------------------------------------
# Edge: generic schema row -> CompRecord with transaction_id=None
# ---------------------------------------------------------------------------


def test_generic_schema_row_has_null_transaction_id(tmp_path):
    generic_csv = tmp_path / "generic.csv"
    generic_csv.write_text(
        "area,unit_type,bedrooms,price_aed,size_sqft,date\n"
        "Business Bay,studio,0,900000,500,2024-05-01\n",
        encoding="utf-8",
    )
    settings = make_settings(tmp_path, benchmarks_paths=str(generic_csv), index_file="")
    acc = ingest_csv_file(generic_csv, settings)

    assert len(acc.comp_records) == 1
    rec = acc.comp_records[0]
    assert rec.transaction_id is None
    assert rec.area == "business bay"
    assert rec.unit_type == "studio"
    assert rec.bedrooms == 0


# ---------------------------------------------------------------------------
# Edge: row where area_name_en is empty -> row skipped, not in comp_records
# ---------------------------------------------------------------------------


def test_empty_area_row_skipped_not_in_comp_records(tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "1,2000000,100,,Tower,flat,1,Sales,Sale,2024-03-15\n",
            "2,2000000,100,Dubai Marina,Tower,flat,1,Sales,Sale,2024-03-15\n",
        ],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    acc = ingest_csv_file(tmp_path / "Transactions.csv", settings)

    assert len(acc.comp_records) == 1
    assert acc.comp_records[0].area == "dubai marina"


# ---------------------------------------------------------------------------
# AC-1.3 — grouped transactions parity unchanged by the ingest extension
# ---------------------------------------------------------------------------

_JVC_ROWS = [
    "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-15\n",
    "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-20\n",
    "3,1900000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-25\n",
    "4,2050000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-28\n",
    "5,2200000,105,Jumeirah Village Circle,,flat,2,Sales,Sale,2026-01-28\n",
]


def test_grouped_transactions_parity_unaffected_by_comp_records(tmp_path: Path):
    write_dld_csv(tmp_path / "Transactions.csv", _JVC_ROWS)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    snap = run_ingest(settings)

    key = ("dubai", "jumeirah village circle", "", "flat", 1)
    assert key in snap.transactions
    assert len(snap.transactions[key]) == 4  # AC-1.3: 4 rows at bedrooms=1

    key_2br = ("dubai", "jumeirah village circle", "", "flat", 2)
    assert len(snap.transactions[key_2br]) == 1


# ---------------------------------------------------------------------------
# TASK-2 — full pipeline: run_ingest wires comp_records_by_area (ADR-016)
# ---------------------------------------------------------------------------


def test_run_ingest_builds_area_keyed_index(tmp_path):
    write_dld_csv(tmp_path / "Transactions.csv", _JVC_ROWS)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    snap = run_ingest(settings)

    assert snap.comp_records_by_area != {}
    assert "jumeirah village circle" in snap.comp_records_by_area
    assert len(snap.comp_records_by_area["jumeirah village circle"]) == 5

    total_comp_records = sum(len(v) for v in snap.comp_records_by_area.values())
    assert total_comp_records == snap.totals.comp_record_count


def test_area_key_correctness_for_jvc(tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Jumeirah Village Circle,Binghatti,flat,1,Sales,Sale,2024-03-15\n"],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    snap = run_ingest(settings)

    records = snap.comp_records_by_area["jumeirah village circle"]
    assert len(records) == 1
    assert records[0].building == "binghatti"


def test_accepted_rows_equals_comp_record_count_for_golden_fixture(tmp_path):
    write_dld_csv(tmp_path / "Transactions.csv", _JVC_ROWS)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    snap = run_ingest(settings)

    assert snap.totals.accepted_transaction_rows == snap.totals.comp_record_count


def test_degraded_empty_dataset_has_empty_comp_records_by_area(tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    snap = run_ingest(settings)

    assert snap.comp_records_by_area == {}
    assert snap.totals.comp_record_count == 0
    assert snap.state == "degraded"
