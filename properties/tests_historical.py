"""
Tests for the SEPARATE historical-assessment adapter
(properties/ingest_historical_assessments.py, source = miami_dade_tax_roll).

Source: Miami-Dade PA public JSON proxy, GetPropertySearchByFolio. Unlike the
appraiser GIS view (current cycle only), this returns a multi-year assessment
block per folio. The fixture below is a faithful, trimmed subset of a real
response for folio 0102100301130 (1102 Brickell Bay Dr) — real values, including
the 2023 row where AssessedValue != TotalValue, and the absurd BathroomCount=1220
the appraiser slice already exposed.
"""

from datetime import date

from django.test import TestCase

from properties.ingest_historical_assessments import (
    ingest_assessment_history, run_historical_ingest,
)
from properties.models import Parcel, AssessmentSnapshot, RawSnapshot

FOLIO = "0102100301130"

# Trimmed real PaServicesProxy GetPropertySearchByFolio payload.
SAMPLE_PAYLOAD = {
    "Assessment": {
        "AssessmentInfos": [
            {"AssessedValue": 387680669, "BuildingOnlyValue": 272426585,
             "ExtraFeatureValue": 0, "LandValue": 115254084,
             "TotalValue": 387680669, "Year": 2025},
            {"AssessedValue": 398830000, "BuildingOnlyValue": 329677550,
             "ExtraFeatureValue": 0, "LandValue": 69152450,
             "TotalValue": 398830000, "Year": 2024},
            {"AssessedValue": 383648650, "BuildingOnlyValue": 327822014,
             "ExtraFeatureValue": 0, "LandValue": 57627042,
             "TotalValue": 385449056, "Year": 2023},  # assessed != market
        ],
    },
    "Taxable": {
        "TaxableInfos": [
            {"CityTaxableValue": 387680669, "CountyTaxableValue": 387680669,
             "SchoolTaxableValue": 387680669, "RegionalTaxableValue": 387680669,
             "Year": 2025},
            {"CityTaxableValue": 398830000, "CountyTaxableValue": 398830000,
             "SchoolTaxableValue": 398830000, "RegionalTaxableValue": 398830000,
             "Year": 2024},
            {"CityTaxableValue": 383648650, "CountyTaxableValue": 383648650,
             "SchoolTaxableValue": 383648650, "RegionalTaxableValue": 385449056,
             "Year": 2023},
        ],
    },
    "PropertyInfo": {"FolioNumber": "01-0210-030-1130", "BathroomCount": 1220},
    "SalesInfos": [],
}

SOURCE = "miami_dade_tax_roll"


class HistoricalAssessmentMappingTests(TestCase):
    def test_seeds_one_dated_snapshot_per_year(self):
        ingest_assessment_history(FOLIO, SAMPLE_PAYLOAD, source_url="http://pa/x")

        # Spine: one parcel keyed by folio.
        p = Parcel.objects.get(folio=FOLIO)

        snaps = AssessmentSnapshot.objects.filter(parcel=p, source=SOURCE)
        self.assertEqual(snaps.count(), 3)  # one row per year
        self.assertEqual(
            sorted(snaps.values_list("tax_year", flat=True)), [2023, 2024, 2025]
        )

        # Values mapped correctly, incl. the year where assessed != market.
        y2023 = snaps.get(tax_year=2023)
        self.assertEqual(int(y2023.assessed_value), 383648650)
        self.assertEqual(int(y2023.market_value), 385449056)
        self.assertEqual(int(y2023.taxable_value), 383648650)
        self.assertEqual(y2023.effective_date, date(2023, 1, 1))
        self.assertEqual(y2023.source_record_id, f"{FOLIO}:2023")

        y2025 = snaps.get(tax_year=2025)
        self.assertEqual(int(y2025.assessed_value), 387680669)

        # Provenance written (RawSnapshot before parsing).
        self.assertEqual(
            RawSnapshot.objects.filter(source=SOURCE, source_record_id=FOLIO).count(), 1
        )

    def test_rerun_is_idempotent(self):
        ingest_assessment_history(FOLIO, SAMPLE_PAYLOAD, source_url="http://pa/x")
        ingest_assessment_history(FOLIO, SAMPLE_PAYLOAD, source_url="http://pa/x")

        p = Parcel.objects.get(folio=FOLIO)
        self.assertEqual(Parcel.objects.count(), 1)
        self.assertEqual(
            AssessmentSnapshot.objects.filter(parcel=p, source=SOURCE).count(), 3
        )  # same years -> no duplicate rows
        self.assertEqual(
            RawSnapshot.objects.filter(source=SOURCE).count(), 2
        )  # provenance logged each scrape

    def test_does_not_touch_appraiser_assessments(self):
        """Historical adapter owns its own source label; an existing appraiser
        assessment for the same parcel/year is left intact (different source)."""
        p = Parcel.objects.create(folio=FOLIO, county="Miami-Dade", state="FL")
        AssessmentSnapshot.objects.create(
            parcel=p, source="miami_dade_pa", tax_year=2025,
            observed_at="2026-01-01T00:00:00Z",
        )
        ingest_assessment_history(FOLIO, SAMPLE_PAYLOAD, source_url="http://pa/x")

        self.assertEqual(
            AssessmentSnapshot.objects.filter(parcel=p, source="miami_dade_pa").count(), 1
        )
        self.assertEqual(
            AssessmentSnapshot.objects.filter(parcel=p, source=SOURCE).count(), 3
        )


class HistoricalSliceIdempotencyTests(TestCase):
    """Slice-scale: many folios via the orchestrator with a stubbed fetch.
    Re-running the whole slice must not duplicate any year for any parcel."""

    def _stub_fetch(self, folio):
        return SAMPLE_PAYLOAD, "http://pa/stub"

    def test_multi_folio_rerun_idempotent(self):
        folios = [FOLIO, "0101000000020", "0101000000030"]
        run_historical_ingest(folios, fetch=self._stub_fetch)
        run_historical_ingest(folios, fetch=self._stub_fetch)

        self.assertEqual(Parcel.objects.count(), 3)
        # 3 parcels x 3 years = 9 dated rows, stable across re-runs.
        self.assertEqual(
            AssessmentSnapshot.objects.filter(source=SOURCE).count(), 9
        )
