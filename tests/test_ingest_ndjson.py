from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from re_data.ingest.csv_transactions import ingest_ndjson_file, ingest_transaction_file
from re_data.ingest.runner import run_ingest
from tests.conftest import make_settings, write_recent_csv


def _write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_ndjson_sale_accepted_and_nulls_skipped(tmp_path: Path):
    path = tmp_path / "hist.json"
    _write_ndjson(
        path,
        [
            {
                "transaction_id": "1-11-2010-1",
                "actual_worth": 299000.0,
                "procedure_area": 69.0,
                "area_name_en": "Al Warsan First",
                "building_name_en": "U-09",
                "property_sub_type_en": "Flat",
                "rooms_en": "1 B/R",
                "trans_group_en": "Sales",
                "procedure_name_en": "Sell",
                "instance_date": "2010-06-08",
            },
            {
                # null price → unparseable, not corrupt Decimal
                "transaction_id": "1-11-2010-2",
                "actual_worth": None,
                "procedure_area": 70.0,
                "area_name_en": "Al Warsan First",
                "building_name_en": None,
                "property_sub_type_en": "Flat",
                "rooms_en": "1 B/R",
                "trans_group_en": "Sales",
                "procedure_name_en": "Sell",
                "instance_date": "2010-06-09",
            },
            {
                # mortgage filtered
                "transaction_id": "2-361-2011-1",
                "actual_worth": 1000000.0,
                "procedure_area": 100.0,
                "area_name_en": "Burj Khalifa",
                "building_name_en": "MISKA 1",
                "property_sub_type_en": "Flat",
                "rooms_en": "2 B/R",
                "trans_group_en": "Mortgages",
                "procedure_name_en": "Delayed Lease to Own Registration",
                "instance_date": "2011-03-16",
            },
        ],
    )
    settings = make_settings(tmp_path, benchmarks_paths=str(path), index_file="")
    acc = ingest_ndjson_file(path, settings)
    assert acc.detected_schema == "dld_transactions"
    assert acc.rows_read == 3
    assert acc.rows_accepted == 1
    assert acc.skip_reasons.unparseable == 1
    assert acc.skip_reasons.filtered_procedure == 1
    rec = acc.comp_records[0]
    assert rec.area == "al warsan first"
    assert rec.building == "u-09"
    assert rec.bedrooms == 1
    assert rec.transaction_date == "2010-06-08"
    assert rec.price_aed == Decimal("299000")
    assert rec.aed_per_sqft > 0


def test_ingest_transaction_file_dispatches_json_and_csv(tmp_path: Path):
    ndjson = tmp_path / "a.json"
    _write_ndjson(
        ndjson,
        [
            {
                "transaction_id": "1",
                "actual_worth": "2000000",
                "procedure_area": "100",
                "area_name_en": "Dubai Marina",
                "building_name_en": "Tower",
                "property_sub_type_en": "flat",
                "rooms_en": "2",
                "trans_group_en": "Sales",
                "procedure_name_en": "Sale",
                "instance_date": "2026-01-01",
            }
        ],
    )
    recent = tmp_path / "transactions-2026-07-19.csv"
    write_recent_csv(
        recent,
        ["Sell - Pre registration,1573426,56.33,Bukadra,Flat,1\n"],
    )
    # Enrich with INSTANCE_DATE / PROJECT via a fuller header file
    recent.write_text(
        "PROCEDURE_EN,TRANS_VALUE,PROCEDURE_AREA,AREA_EN,PROP_SB_TYPE_EN,ROOMS_EN,"
        "INSTANCE_DATE,PROJECT_EN,GROUP_EN\n"
        "Sell - Pre registration,1573426,56.33,Bukadra,Flat,1,"
        "2026-07-01 16:16:29,Skyvue,Sales\n",
        encoding="utf-8",
    )

    settings = make_settings(
        tmp_path,
        benchmarks_paths=f"{recent};{ndjson}",
        index_file="",
    )
    snap = run_ingest(settings)
    by_name = {f.name: f for f in snap.files}
    assert by_name["a.json"].schema == "dld_transactions"
    assert by_name["a.json"].status == "loaded"
    assert by_name["transactions-2026-07-19.csv"].schema == "recent_transactions"
    assert by_name["transactions-2026-07-19.csv"].status == "loaded"
    assert snap.totals.accepted_transaction_rows == 2
    # No index configured → not degraded solely for missing index
    assert snap.state == "complete"
    assert snap.totals.index_loaded is False

    # Recent row used INSTANCE_DATE + PROJECT_EN
    acc = ingest_transaction_file(recent, settings)
    assert acc.comp_records[0].transaction_date == "2026-07-01"
    assert acc.comp_records[0].building == "skyvue"


def test_cell_null_strings_do_not_become_corrupt_decimals(tmp_path: Path):
    path = tmp_path / "bad.json"
    _write_ndjson(
        path,
        [
            {
                "transaction_id": "x",
                "actual_worth": "null",
                "procedure_area": "100",
                "area_name_en": "Area",
                "building_name_en": "",
                "property_sub_type_en": "Flat",
                "rooms_en": "1",
                "trans_group_en": "Sales",
                "procedure_name_en": "Sell",
                "instance_date": "2020-01-01",
            }
        ],
    )
    settings = make_settings(tmp_path, benchmarks_paths=str(path), index_file="")
    acc = ingest_ndjson_file(path, settings)
    assert acc.rows_accepted == 0
    assert acc.skip_reasons.unparseable == 1
    assert acc.comp_records == []
