"""
Miami-Dade Property Appraiser ingestion adapter.

Source: the county's Property Appraiser GIS view (PaGISView_gdb), an ArcGIS
FeatureServer hosted on Esri's ArcGIS Online infrastructure. Free, queryable
per-folio over HTTP, returns JSON, updated by the county.

This module does two things and keeps them separate on purpose:
  * fetch_page()    — talks to the network (the brittle, replaceable part)
  * ingest_feature() — pure mapping from one record onto the models (the part
                       we can test offline against a known sample)

It honors the model invariants:
  * RawSnapshot is written first, before anything is parsed.
  * Parcel (identity + stable descriptors) is upserted by folio.
  * OwnershipSnapshot is append-only: a new dated row is written only when the
    raw owner string changes, so the ownership trajectory accretes without
    duplicating on every scrape. owner_name_raw is never lost; owner_entity
    stays null for the later resolution job.
  * AssessmentSnapshot is keyed (parcel, source, tax_year) so re-scraping a year
    is idempotent while each new year extends the value trajectory.
  * Deed is an append-only event deduped on (source, source_record_id).
"""

from datetime import datetime, date, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
import json
import urllib.parse
import urllib.request

from django.db import transaction
from django.utils import timezone

from properties.models import (
    Parcel, OwnershipSnapshot, AssessmentSnapshot, Deed, RawSnapshot,
)

SOURCE = "miami_dade_pa"
SERVICE_URL = (
    "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/"
    "PaGISView_gdb/FeatureServer/0/query"
)


# ---------------------------------------------------------------------------
# Network layer (replaceable / mockable)
# ---------------------------------------------------------------------------

def build_query_url(where="1=1", offset=0, page_size=1000):
    params = {
        "where": where,
        "outFields": "*",
        "outSR": "4326",          # geometry.x = longitude, geometry.y = latitude
        "returnGeometry": "true",
        "resultOffset": offset,
        "resultRecordCount": page_size,
        "f": "json",
    }
    return f"{SERVICE_URL}?{urllib.parse.urlencode(params)}"


def fetch_page(where="1=1", offset=0, page_size=1000, timeout=60):
    """Fetch one page from the FeatureServer. Returns the parsed JSON dict."""
    url = build_query_url(where=where, offset=offset, page_size=page_size)
    req = urllib.request.Request(url, headers={"User-Agent": "Vanthir/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")), url


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _str(v):
    return (v or "").strip()


def _pos_int(v):
    """int if > 0 else None (county uses 0 as 'unknown' for year_built etc.)."""
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _int0(v):
    """int keeping a legitimate 0 (bedroom/unit counts)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _dec(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _dec_fit(v, max_digits, decimal_places):
    """Decimal that fits a DecimalField(max_digits, decimal_places), else None.

    County data carries garbage in narrow fields (e.g. BATHROOM_COUNT=1220 on a
    DecimalField(4,1)). Such a value crashes the whole batch on save. We treat an
    out-of-range value as UNKNOWN rather than clamping it to a fabricated number —
    the verbatim value is never lost, it stays in the RawSnapshot payload.
    """
    d = _dec(v)
    if d is None:
        return None
    try:
        q = d.quantize(Decimal(1).scaleb(-decimal_places))
    except InvalidOperation:
        return None
    if len(q.as_tuple().digits) > max_digits:
        return None
    return q


def _parse_yyyymmdd(v):
    v = _str(v)
    if len(v) == 8 and v.isdigit():
        try:
            return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return None


def _join_nonempty(*parts):
    return " ".join(p for p in (_str(x) for x in parts) if p)


# ---------------------------------------------------------------------------
# Mapping layer (pure; offline-testable)
# ---------------------------------------------------------------------------

@transaction.atomic
def ingest_feature(attributes, geometry=None, source_url="", observed_at=None):
    """Map one Property Appraiser feature onto the models. Idempotent."""
    a = attributes
    observed_at = observed_at or timezone.now()
    folio = _str(a.get("FOLIO"))
    if not folio:
        return None  # nothing keys without a folio

    # 1) Raw snapshot first — provenance before parsing.
    RawSnapshot.objects.create(
        source=SOURCE,
        source_url=source_url,
        source_record_id=folio,
        payload=a,
        observed_at=observed_at,
    )

    # 2) Parcel (identity + stable descriptors) — upsert by folio.
    lng = lat = None
    if geometry:
        lng, lat = geometry.get("x"), geometry.get("y")

    parcel, _ = Parcel.objects.update_or_create(
        folio=folio,
        defaults=dict(
            county="Miami-Dade",
            state="FL",
            address_line=_join_nonempty(a.get("TRUE_SITE_ADDR"), a.get("TRUE_SITE_UNIT")),
            city=_str(a.get("TRUE_SITE_CITY")),
            zip_code=_str(a.get("TRUE_SITE_ZIP_CODE"))[:12],
            latitude=_dec_fit(lat, 9, 6),
            longitude=_dec_fit(lng, 9, 6),
            year_built=_pos_int(a.get("YEAR_BUILT")),
            beds=_int0(a.get("BEDROOM_COUNT")),
            baths=_dec_fit(a.get("BATHROOM_COUNT"), 4, 1),
            living_sqft=_pos_int(a.get("BUILDING_HEATED_AREA")),
            land_use=_str(a.get("DOR_DESC"))[:120],
        ),
    )

    # 3) Ownership — append-only; new row only when the raw owner string changes.
    owner_raw = _join_nonempty(a.get("TRUE_OWNER1"), a.get("TRUE_OWNER2"), a.get("TRUE_OWNER3"))
    if owner_raw:
        last = (
            OwnershipSnapshot.objects.filter(parcel=parcel)
            .order_by("-observed_at").first()
        )
        if last is None or last.owner_name_raw != owner_raw:
            OwnershipSnapshot.objects.create(
                parcel=parcel,
                owner_name_raw=owner_raw,
                mailing_address_raw=_join_nonempty(
                    a.get("TRUE_MAILING_ADDR1"), a.get("TRUE_MAILING_ADDR2"),
                    a.get("TRUE_MAILING_ADDR3"), a.get("TRUE_MAILING_CITY"),
                    a.get("TRUE_MAILING_STATE"), a.get("TRUE_MAILING_ZIP_CODE"),
                ),
                owner_entity=None,           # resolved later by a separate job
                source=SOURCE,
                source_url=source_url,
                source_record_id=folio,
                observed_at=observed_at,
            )

    # 4) Assessment — idempotent per (parcel, source, tax_year); new year extends trajectory.
    tax_year = _int0(a.get("ASSESSMENT_YEAR_CUR"))
    if tax_year:
        AssessmentSnapshot.objects.get_or_create(
            parcel=parcel,
            source=SOURCE,
            tax_year=tax_year,
            defaults=dict(
                assessed_value=_dec(a.get("ASSESSED_VAL_CUR")),
                source_url=source_url,
                effective_date=date(tax_year, 1, 1),
                observed_at=observed_at,
            ),
        )

    # 5) Deed — append-only sale event, deduped on (source, source_record_id).
    sale_date = _parse_yyyymmdd(a.get("DOS_1"))
    sale_price = _dec(a.get("PRICE_1"))
    if sale_date and sale_price:
        rec_id = f"{folio}:{a.get('DOS_1')}"
        Deed.objects.get_or_create(
            source=SOURCE,
            source_record_id=rec_id,
            defaults=dict(
                parcel=parcel,
                grantee_raw=owner_raw,
                sale_price=sale_price,
                recorded_date=sale_date,
                source_url=source_url,
                effective_date=sale_date,
                observed_at=observed_at,
            ),
        )

    return parcel


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_ingest(where="1=1", limit=None, page_size=1000, stdout=None):
    """Paginate the FeatureServer and ingest. Returns count ingested."""
    def log(msg):
        if stdout:
            stdout.write(msg)

    ingested = 0
    offset = 0
    while True:
        data, url = fetch_page(where=where, offset=offset, page_size=page_size)
        features = data.get("features", [])
        if not features:
            break
        for feat in features:
            ingest_feature(feat.get("attributes", {}), feat.get("geometry"), source_url=url)
            ingested += 1
            if limit and ingested >= limit:
                log(f"Ingested {ingested} (limit reached).")
                return ingested
        log(f"Ingested {ingested} so far...")
        if not data.get("exceededTransferLimit") and len(features) < page_size:
            break
        offset += len(features)
    log(f"Done. Ingested {ingested}.")
    return ingested
