from datetime import date, datetime, timezone as dt_tz

from django.test import TestCase

from properties.ingest import ingest_feature
from properties.models import (
    Parcel, OwnershipSnapshot, AssessmentSnapshot, Deed, RawSnapshot,
)

# The exact sample record pulled from the live Miami-Dade PA FeatureServer.
SAMPLE_ATTRS = {
    "OBJECTID": 1, "FOLIO": "0101000000020", "TTRRSS": "544101",
    "TRUE_SITE_ADDR": "16 SE 2 ST", "TRUE_SITE_UNIT": None,
    "TRUE_SITE_CITY": "Miami", "TRUE_SITE_ZIP_CODE": "33131-0000",
    "TRUE_MAILING_ADDR1": "31 SE 5TH ST 2704", "TRUE_MAILING_CITY": "MIAMI",
    "TRUE_MAILING_STATE": "FL", "TRUE_MAILING_ZIP_CODE": "33131",
    "TRUE_OWNER1": "16 SE 2ND STREET DOWNTOWN",
    "TRUE_OWNER2": "C/O ALLAN FILGUEIRAS", "TRUE_OWNER3": "INVESTMENT LLC",
    "CONDO_FLAG": "N", "PARENT_FOLIO": None,
    "DOR_CODE_CUR": "2865", "DOR_DESC": "PARKING LOT/MOBILE HOME PARK : PARKING LOT",
    "BEDROOM_COUNT": 0, "BATHROOM_COUNT": 0, "FLOOR_COUNT": 0, "UNIT_COUNT": 0,
    "BUILDING_HEATED_AREA": 0, "LOT_SIZE": 60198, "YEAR_BUILT": 0,
    "ASSESSMENT_YEAR_CUR": 2026, "ASSESSED_VAL_CUR": None,
    "DOS_1": "20210623", "PRICE_1": 46000000, "PID": 538415,
}
SAMPLE_GEOM = {"x": -80.19, "y": 25.77}  # outSR=4326 -> lng, lat


class AppraiserIngestTests(TestCase):
    def test_maps_sample_record(self):
        ingest_feature(SAMPLE_ATTRS, SAMPLE_GEOM, source_url="http://example/query")

        p = Parcel.objects.get(folio="0101000000020")
        self.assertEqual(p.address_line, "16 SE 2 ST")
        self.assertEqual(p.city, "Miami")
        self.assertEqual(p.zip_code, "33131-0000")
        self.assertEqual(float(p.longitude), -80.19)
        self.assertEqual(float(p.latitude), 25.77)
        self.assertIsNone(p.year_built)          # 0 -> unknown
        self.assertEqual(p.land_use, "PARKING LOT/MOBILE HOME PARK : PARKING LOT")

        # Owner: three lines concatenated, raw preserved, entity unresolved.
        own = OwnershipSnapshot.objects.get(parcel=p)
        self.assertEqual(
            own.owner_name_raw,
            "16 SE 2ND STREET DOWNTOWN C/O ALLAN FILGUEIRAS INVESTMENT LLC",
        )
        self.assertIsNone(own.owner_entity)

        # Assessment year captured (value null is fine).
        assess = AssessmentSnapshot.objects.get(parcel=p)
        self.assertEqual(assess.tax_year, 2026)

        # Sale parsed into a Deed event.
        deed = Deed.objects.get(parcel=p)
        self.assertEqual(int(deed.sale_price), 46000000)
        self.assertEqual(deed.effective_date, date(2021, 6, 23))

        self.assertEqual(RawSnapshot.objects.count(), 1)

    def test_idempotent_rescrape(self):
        """Re-ingesting the same record must not duplicate events or ownership."""
        ingest_feature(SAMPLE_ATTRS, SAMPLE_GEOM, source_url="http://example/query")
        ingest_feature(SAMPLE_ATTRS, SAMPLE_GEOM, source_url="http://example/query")

        self.assertEqual(Parcel.objects.count(), 1)
        self.assertEqual(OwnershipSnapshot.objects.count(), 1)  # unchanged owner -> no new row
        self.assertEqual(AssessmentSnapshot.objects.count(), 1)  # same year -> idempotent
        self.assertEqual(Deed.objects.count(), 1)                # deduped sale
        self.assertEqual(RawSnapshot.objects.count(), 2)         # but provenance logged each scrape

    def test_owner_change_appends(self):
        """A changed owner string writes a NEW dated ownership row (trajectory)."""
        ingest_feature(SAMPLE_ATTRS, SAMPLE_GEOM, source_url="http://example/query")
        changed = dict(SAMPLE_ATTRS)
        changed["TRUE_OWNER1"] = "NEW OWNER HOLDINGS"
        changed["TRUE_OWNER2"] = ""
        changed["TRUE_OWNER3"] = "LLC"
        ingest_feature(changed, SAMPLE_GEOM, source_url="http://example/query")

        p = Parcel.objects.get(folio="0101000000020")
        self.assertEqual(OwnershipSnapshot.objects.filter(parcel=p).count(), 2)


# A second parcel with its own owner/assessment/deed, plus an absurd county
# BATHROOM_COUNT (1220) — pulled from real ZIP 33131 data (folio 0102100301130)
# where the county stores garbage in the field. baths is DecimalField(4,1), so a
# value > 999.9 must NOT crash ingest; the raw value stays in RawSnapshot.
SAMPLE_ATTRS_2 = {
    "OBJECTID": 2, "FOLIO": "0102100301130",
    "TRUE_SITE_ADDR": "100 BISCAYNE BLVD", "TRUE_SITE_UNIT": "2201",
    "TRUE_SITE_CITY": "Miami", "TRUE_SITE_ZIP_CODE": "33131-0000",
    "TRUE_OWNER1": "BISCAYNE TOWER", "TRUE_OWNER2": "", "TRUE_OWNER3": "LLC",
    "DOR_DESC": "RESIDENTIAL : CONDOMINIUM",
    "BEDROOM_COUNT": 2, "BATHROOM_COUNT": 1220,  # absurd county value
    "BUILDING_HEATED_AREA": 1100, "YEAR_BUILT": 1985,
    "ASSESSMENT_YEAR_CUR": 2026, "ASSESSED_VAL_CUR": 450000,
    "DOS_1": "20190115", "PRICE_1": 525000,
}
SAMPLE_GEOM_2 = {"x": -80.18691234567, "y": 25.76554321}


class SliceScaleIdempotencyTests(TestCase):
    """Prove the ingest survives a realistic multi-record slice and that
    re-running the SAME slice is idempotent (no duplicate deeds, no duplicate
    assessment for the same year, no duplicate unchanged-ownership rows)."""

    BATCH = [
        (SAMPLE_ATTRS, SAMPLE_GEOM),
        (SAMPLE_ATTRS_2, SAMPLE_GEOM_2),
    ]

    def _run_slice(self):
        for attrs, geom in self.BATCH:
            ingest_feature(attrs, geom, source_url="http://example/query")

    def test_absurd_bathroom_count_does_not_crash(self):
        """County garbage (1220 baths) must ingest as unknown, not crash."""
        self._run_slice()
        p = Parcel.objects.get(folio="0102100301130")
        self.assertIsNone(p.baths)  # out-of-range -> unknown, not a clamped lie
        # raw value is never lost — it lives in the provenance payload
        raw = RawSnapshot.objects.get(source_record_id="0102100301130")
        self.assertEqual(raw.payload["BATHROOM_COUNT"], 1220)

    def test_slice_rerun_is_idempotent(self):
        self._run_slice()
        self._run_slice()  # re-scrape the identical slice

        self.assertEqual(Parcel.objects.count(), 2)
        self.assertEqual(Deed.objects.count(), 2)                 # both sales deduped
        self.assertEqual(AssessmentSnapshot.objects.count(), 2)   # same year -> idempotent
        self.assertEqual(OwnershipSnapshot.objects.count(), 2)    # unchanged owners -> no new rows
        self.assertEqual(RawSnapshot.objects.count(), 4)          # provenance logged each scrape
