"""
Miami-Dade PRIOR-YEAR assessment ingestion adapter (SEPARATE from the appraiser
GIS adapter in properties/ingest.py — do not merge them).

Why a second adapter: the PaGISView GIS layer used by ingest.py only carries the
CURRENT assessment cycle, and its value is null until the roll is certified. The
Property Appraiser's public JSON proxy, by contrast, returns a multi-YEAR
assessment block per folio. We use it to backfill the value trajectory.

Source: Miami-Dade PA public service proxy
  https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx
  ?Operation=GetPropertySearchByFolio&clientAppName=PropertySearch&folioNumber=<folio>
(Reachable from a US network; may be geo-blocked elsewhere.)

Invariants honored:
  * RawSnapshot is written first, before parsing (source = miami_dade_tax_roll).
  * Parcel (folio) is the spine — we join by folio and only ensure the spine row
    exists; descriptor fields (address, beds, year_built, ...) remain the
    appraiser adapter's responsibility. This adapter never overwrites them.
  * AssessmentSnapshot is append-only and idempotent per (parcel, source,
    tax_year): each year is its own dated row; re-running never duplicates or
    .update()s a year. New years extend the trajectory.
  * assessed_value / market_value / taxable_value go through _dec_fit so an
    out-of-range county value yields NULL (raw kept in RawSnapshot) instead of
    crashing the batch — the same fit-or-null rule the appraiser adapter uses.
"""

from datetime import date
import json
import urllib.parse
import urllib.request

from django.db import transaction
from django.utils import timezone

# Reuse the appraiser adapter's value helpers — one source of truth, no dup.
from properties.ingest import _dec_fit, _int0, _str, _strip_nul
from properties.models import Parcel, AssessmentSnapshot, RawSnapshot

SOURCE = "miami_dade_tax_roll"
PROXY_URL = "https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx"

# AssessmentSnapshot money fields are DecimalField(max_digits=14, decimal_places=2).
_MONEY = (14, 2)


# ---------------------------------------------------------------------------
# Network layer (replaceable / mockable)
# ---------------------------------------------------------------------------

def build_folio_url(folio):
    params = {
        "Operation": "GetPropertySearchByFolio",
        "clientAppName": "PropertySearch",
        "folioNumber": folio,
    }
    return f"{PROXY_URL}?{urllib.parse.urlencode(params)}"


def fetch_folio(folio, timeout=60):
    """Fetch one folio's full property record. Returns (payload_dict, url)."""
    url = build_folio_url(folio)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Vanthir/0.1",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.miamidade.gov/Apps/PA/propertysearch/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8").strip()
    if not body:
        return None, url
    return json.loads(body), url


# ---------------------------------------------------------------------------
# Mapping layer (pure; offline-testable)
# ---------------------------------------------------------------------------

def _county_taxable_by_year(payload):
    """Map year -> CountyTaxableValue from the Taxable block."""
    out = {}
    for t in (payload.get("Taxable") or {}).get("TaxableInfos", []) or []:
        yr = _int0(t.get("Year"))
        if yr is not None:
            out[yr] = t.get("CountyTaxableValue")
    return out


@transaction.atomic
def ingest_assessment_history(folio, payload, source_url="", observed_at=None):
    """Map one proxy record's multi-year assessment block onto dated
    AssessmentSnapshot rows. Idempotent per (parcel, source, tax_year)."""
    folio = _str(folio)
    if not folio:
        return None
    observed_at = observed_at or timezone.now()

    # 1) Raw snapshot first — provenance before parsing.
    RawSnapshot.objects.create(
        source=SOURCE,
        source_url=source_url,
        source_record_id=folio,
        payload=_strip_nul(payload),
        observed_at=observed_at,
    )

    # 2) Ensure the spine row exists; never touch appraiser-owned descriptors.
    parcel, _ = Parcel.objects.get_or_create(
        folio=folio, defaults=dict(county="Miami-Dade", state="FL"),
    )

    taxable = _county_taxable_by_year(payload)

    # 3) One dated, idempotent AssessmentSnapshot per year (append-only).
    for info in (payload.get("Assessment") or {}).get("AssessmentInfos", []) or []:
        year = _int0(info.get("Year"))
        if not year:
            continue
        AssessmentSnapshot.objects.get_or_create(
            parcel=parcel,
            source=SOURCE,
            tax_year=year,
            defaults=dict(
                assessed_value=_dec_fit(info.get("AssessedValue"), *_MONEY),
                market_value=_dec_fit(info.get("TotalValue"), *_MONEY),
                taxable_value=_dec_fit(taxable.get(year), *_MONEY),
                source_url=source_url,
                source_record_id=f"{folio}:{year}",
                effective_date=date(year, 1, 1),
                observed_at=observed_at,
            ),
        )

    return parcel


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_historical_ingest(folios, fetch=None, observed_at=None, stdout=None):
    """Fetch + ingest assessment history for each folio. Returns count ingested.

    `fetch` is injectable for offline tests; defaults to the live proxy call.
    `folios` should already be scoped (e.g. one ZIP) by the caller.
    """
    fetch = fetch or fetch_folio

    def log(msg):
        if stdout:
            stdout.write(msg)

    ingested = 0
    for folio in folios:
        try:
            payload, url = fetch(folio)
        except Exception as exc:  # network/parse error on one folio shouldn't kill the slice
            log(f"  ! {folio}: fetch failed ({exc})")
            continue
        if not payload:
            log(f"  - {folio}: empty response, skipped")
            continue
        ingest_assessment_history(folio, payload, source_url=url, observed_at=observed_at)
        ingested += 1
        if ingested % 25 == 0:
            log(f"  ...{ingested} folios ingested")
    log(f"Done. Ingested history for {ingested} folio(s).")
    return ingested
