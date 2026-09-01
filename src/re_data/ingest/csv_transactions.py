from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from re_data.config import SQM_TO_SQFT, Settings
from re_data.ingest.parsers import (
    cell_str,
    date_from_filename,
    key_tuple,
    norm,
    parse_date_any,
    parse_decimal,
    parse_int,
)
from re_data.models.domain import BenchmarkKey, CompRecord, DetectedSchema, SkipReasons

SkipKind = Literal["filtered_procedure", "out_of_bounds_ppsf", "unparseable", "accepted"]


@dataclass
class IngestAccumulator:
    grouped: dict[BenchmarkKey, list[Decimal]] = field(default_factory=dict)
    last_dates: dict[BenchmarkKey, str] = field(default_factory=dict)
    comp_records: list[CompRecord] = field(default_factory=list)
    rows_read: int = 0
    rows_accepted: int = 0
    skip_reasons: SkipReasons = field(default_factory=SkipReasons)
    detected_schema: DetectedSchema = "unknown"

    @property
    def rows_skipped(self) -> int:
        return self.skip_reasons.rows_skipped


def file_content_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _record_skip(acc: IngestAccumulator, kind: SkipKind) -> None:
    if kind == "filtered_procedure":
        acc.skip_reasons = SkipReasons(
            filtered_procedure=acc.skip_reasons.filtered_procedure + 1,
            out_of_bounds_ppsf=acc.skip_reasons.out_of_bounds_ppsf,
            unparseable=acc.skip_reasons.unparseable,
        )
    elif kind == "out_of_bounds_ppsf":
        acc.skip_reasons = SkipReasons(
            filtered_procedure=acc.skip_reasons.filtered_procedure,
            out_of_bounds_ppsf=acc.skip_reasons.out_of_bounds_ppsf + 1,
            unparseable=acc.skip_reasons.unparseable,
        )
    elif kind == "unparseable":
        acc.skip_reasons = SkipReasons(
            filtered_procedure=acc.skip_reasons.filtered_procedure,
            out_of_bounds_ppsf=acc.skip_reasons.out_of_bounds_ppsf,
            unparseable=acc.skip_reasons.unparseable + 1,
        )


def _accept_row(
    acc: IngestAccumulator,
    *,
    emirate: str,
    area: str,
    building: str,
    unit_type: str,
    bedrooms: int,
    ppsf: Decimal,
    price_aed: Decimal,
    size_sqft: Decimal,
    row_date: str | None,
    transaction_id: str | None = None,
) -> None:
    k = key_tuple(emirate, area, building, unit_type, bedrooms)
    acc.grouped.setdefault(k, []).append(ppsf)
    if row_date:
        prev = acc.last_dates.get(k)
        if prev is None or row_date > prev:
            acc.last_dates[k] = row_date
    acc.comp_records.append(
        CompRecord(
            transaction_date=row_date,
            price_aed=price_aed,
            size_sqft=size_sqft,
            aed_per_sqft=ppsf,
            emirate=k[0],
            area=k[1],
            building=k[2],
            unit_type=k[3],
            bedrooms=k[4],
            transaction_id=transaction_id,
        )
    )
    acc.rows_accepted += 1


def _ingest_row_dld_transactions(
    row: dict[str, str],
    acc: IngestAccumulator,
    settings: Settings,
) -> None:
    trans_group = norm(cell_str(row.get("trans_group_en")))
    proc_en = norm(cell_str(row.get("procedure_name_en")))
    if "mortgage" in proc_en or "mortgage" in trans_group:
        _record_skip(acc, "filtered_procedure")
        return
    if "gift" in proc_en or "inherit" in proc_en:
        _record_skip(acc, "filtered_procedure")
        return

    include = (
        ("sale" in trans_group)
        or ("sale" in proc_en)
        or ("sell" in proc_en)
        or ("transfer" in proc_en)
    )
    if not include:
        _record_skip(acc, "filtered_procedure")
        return

    area = cell_str(row.get("area_name_en"))
    if not area:
        _record_skip(acc, "unparseable")
        return

    building = cell_str(row.get("building_name_en")) or cell_str(row.get("project_name_en"))
    unit_type = cell_str(row.get("property_sub_type_en")) or cell_str(row.get("property_type_en"))
    bedrooms = parse_int(cell_str(row.get("rooms_en")) or cell_str(row.get("rooms_ar")))

    price = parse_decimal(cell_str(row.get("actual_worth")))
    area_sqm = parse_decimal(cell_str(row.get("procedure_area")))
    if price is None or area_sqm is None or area_sqm <= 0:
        _record_skip(acc, "unparseable")
        return

    unit = settings.dld_area_unit
    sqft = area_sqm if unit == "sqft" else area_sqm * SQM_TO_SQFT
    if sqft <= 0:
        _record_skip(acc, "unparseable")
        return

    ppsf = price / sqft
    if ppsf < settings.ppsf_min or ppsf > settings.ppsf_max:
        _record_skip(acc, "out_of_bounds_ppsf")
        return

    d = parse_date_any(cell_str(row.get("instance_date")))
    tid = cell_str(row.get("transaction_id")) or None
    _accept_row(
        acc,
        emirate="dubai",
        area=area,
        building=building,
        unit_type=unit_type,
        bedrooms=bedrooms,
        ppsf=ppsf,
        price_aed=price,
        size_sqft=sqft,
        row_date=d,
        transaction_id=tid,
    )


def _ingest_row_recent_transactions(
    row: dict[str, str],
    acc: IngestAccumulator,
    settings: Settings,
    default_date: str | None,
) -> None:
    proc = norm(cell_str(row.get("PROCEDURE_EN")))
    group = norm(cell_str(row.get("GROUP_EN")))
    if "mortgage" in proc or "mortgage" in group or "gift" in proc or "inherit" in proc:
        _record_skip(acc, "filtered_procedure")
        return
    if not ("sale" in proc or "sell" in proc or "transfer" in proc or "sale" in group):
        _record_skip(acc, "filtered_procedure")
        return

    area = cell_str(row.get("AREA_EN"))
    if not area:
        _record_skip(acc, "unparseable")
        return

    unit_type = cell_str(row.get("PROP_SB_TYPE_EN")) or cell_str(row.get("PROP_TYPE_EN"))
    bedrooms = parse_int(cell_str(row.get("ROOMS_EN")))
    building = cell_str(row.get("PROJECT_EN")) or cell_str(row.get("MASTER_PROJECT_EN"))

    price = parse_decimal(cell_str(row.get("TRANS_VALUE")))
    area_sqm = parse_decimal(
        cell_str(row.get("PROCEDURE_AREA")) or cell_str(row.get("ACTUAL_AREA"))
    )
    if price is None or area_sqm is None or area_sqm <= 0:
        _record_skip(acc, "unparseable")
        return

    unit = settings.dld_area_unit
    sqft = area_sqm if unit == "sqft" else area_sqm * SQM_TO_SQFT
    if sqft <= 0:
        _record_skip(acc, "unparseable")
        return
    ppsf = price / sqft

    if ppsf < settings.ppsf_min or ppsf > settings.ppsf_max:
        _record_skip(acc, "out_of_bounds_ppsf")
        return

    row_date = parse_date_any(cell_str(row.get("INSTANCE_DATE"))) or default_date
    _accept_row(
        acc,
        emirate="dubai",
        area=area,
        building=building,
        unit_type=unit_type,
        bedrooms=bedrooms,
        ppsf=ppsf,
        price_aed=price,
        size_sqft=sqft,
        row_date=row_date,
        transaction_id=cell_str(row.get("TRANSACTION_NUMBER")) or None,
    )


def _ingest_row_generic(row: dict[str, str], acc: IngestAccumulator, settings: Settings) -> None:
    area = cell_str(row.get("area")) or cell_str(row.get("Area"))
    if not area:
        _record_skip(acc, "unparseable")
        return

    emirate = cell_str(row.get("emirate")) or cell_str(row.get("Emirate")) or "dubai"
    building = cell_str(row.get("building")) or cell_str(row.get("Building"))
    unit_type = (
        cell_str(row.get("unit_type")) or cell_str(row.get("UnitType")) or cell_str(row.get("unit"))
    )
    bedrooms = parse_int(cell_str(row.get("bedrooms")) or cell_str(row.get("Bedrooms")))

    price = parse_decimal(
        cell_str(row.get("price_aed")) or cell_str(row.get("Price")) or cell_str(row.get("price"))
    )
    sqft = parse_decimal(
        cell_str(row.get("size_sqft"))
        or cell_str(row.get("sqft"))
        or cell_str(row.get("Size"))
        or cell_str(row.get("area_sqft"))
    )
    if price is None or sqft is None or sqft <= 0:
        _record_skip(acc, "unparseable")
        return

    ppsf = price / sqft
    if ppsf < settings.ppsf_min or ppsf > settings.ppsf_max:
        _record_skip(acc, "out_of_bounds_ppsf")
        return

    d = parse_date_any(cell_str(row.get("date")) or cell_str(row.get("Date")))
    _accept_row(
        acc,
        emirate=emirate,
        area=area,
        building=building,
        unit_type=unit_type,
        bedrooms=bedrooms,
        ppsf=ppsf,
        price_aed=price,
        size_sqft=sqft,
        row_date=d,
        transaction_id=None,
    )


def ingest_csv_file(path: Path, settings: Settings) -> IngestAccumulator:
    acc = IngestAccumulator()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = set(h or "" for h in (reader.fieldnames or []))

        is_dld = "transaction_id" in headers
        has_worth = "actual_worth" in headers or "meter_sale_price" in headers
        if is_dld and has_worth:
            acc.detected_schema = "dld_transactions"
            for row in reader:
                acc.rows_read += 1
                cleaned = {k: cell_str(v) for k, v in row.items()}
                _ingest_row_dld_transactions(cleaned, acc, settings)
            return acc

        if "PROCEDURE_EN" in headers and "TRANS_VALUE" in headers and "AREA_EN" in headers:
            acc.detected_schema = "recent_transactions"
            default_date = date_from_filename(path.name)
            for row in reader:
                acc.rows_read += 1
                cleaned = {k: cell_str(v) for k, v in row.items()}
                _ingest_row_recent_transactions(cleaned, acc, settings, default_date)
            return acc

        acc.detected_schema = "generic_comps"
        for row in reader:
            acc.rows_read += 1
            cleaned = {k: cell_str(v) for k, v in row.items()}
            _ingest_row_generic(cleaned, acc, settings)
    return acc


def ingest_ndjson_file(path: Path, settings: Settings) -> IngestAccumulator:
    """Line-delimited JSON with the same DLD transaction field names as Transactions.csv."""
    acc = IngestAccumulator()
    acc.detected_schema = "dld_transactions"
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            acc.rows_read += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                _record_skip(acc, "unparseable")
                continue
            if not isinstance(obj, dict):
                _record_skip(acc, "unparseable")
                continue
            # Coerce nulls / numbers to strings so the shared DLD row path stays null-safe.
            row = {str(k): cell_str(v) for k, v in obj.items()}
            _ingest_row_dld_transactions(row, acc, settings)
    return acc


def ingest_transaction_file(path: Path, settings: Settings) -> IngestAccumulator:
    """Dispatch by extension: NDJSON (.json) or CSV (.csv / other)."""
    if path.suffix.lower() == ".json":
        return ingest_ndjson_file(path, settings)
    return ingest_csv_file(path, settings)


def merge_accumulator(
    target_grouped: dict[BenchmarkKey, list[Decimal]],
    target_dates: dict[BenchmarkKey, str],
    target_comps: list[CompRecord],
    acc: IngestAccumulator,
) -> None:
    for k, values in acc.grouped.items():
        target_grouped.setdefault(k, []).extend(values)
    for k, d in acc.last_dates.items():
        prev = target_dates.get(k)
        if prev is None or d > prev:
            target_dates[k] = d
    target_comps.extend(acc.comp_records)
