"""
Tests for the single parcel-page template + thin-page guard.

Validates the gate that matters: a real parcel (address + value + sale) renders
an indexable page with the trajectory and computed FAQ JSON-LD, while a junk
parcel (reference folio / common area / address-less / $0) renders noindex.
"""

import json
from datetime import date, datetime, timezone as dt_tz

from django.test import TestCase
from django.urls import reverse

from properties.models import (
    Parcel, OwnershipSnapshot, Deed, AssessmentSnapshot, FloodZoneSnapshot,
)

OBS = datetime(2026, 6, 4, tzinfo=dt_tz.utc)


def _seed_real_parcel():
    p = Parcel.objects.create(
        folio="0101110601160", county="Miami-Dade", state="FL",
        address_line="41 E FLAGLER ST", city="Miami", zip_code="33131-0000",
        year_built=1928, living_sqft=60426, land_use="STORE : RETAIL OUTLET",
    )
    OwnershipSnapshot.objects.create(
        parcel=p, owner_name_raw="41 FLAGLER REALTY LLC",
        source="miami_dade_pa", observed_at=OBS,
    )
    Deed.objects.create(
        parcel=p, source="miami_dade_pa", source_record_id="x:1",
        sale_price=27200000, recorded_date=date(2021, 5, 26),
        grantee_raw="41 FLAGLER REALTY LLC", observed_at=OBS,
    )
    for yr, av, mv in [(2023, 11428750, 11428750), (2024, 12571625, 14147500), (2025, 11211250, 11211250)]:
        AssessmentSnapshot.objects.create(
            parcel=p, source="miami_dade_tax_roll", tax_year=yr,
            assessed_value=av, market_value=mv, taxable_value=av, observed_at=OBS,
        )
    return p


class ParcelPageTests(TestCase):
    def test_real_parcel_is_indexable_and_renders_content(self):
        p = _seed_real_parcel()
        self.assertTrue(p.is_indexable)

        resp = self.client.get(reverse("parcel_detail", args=[p.folio]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()

        self.assertNotIn('name="robots"', html)        # indexable -> no noindex
        self.assertIn("41 FLAGLER REALTY LLC", html)    # raw owner
        self.assertIn("$27,200,000", html)              # sale price
        # 3-year trajectory values present
        self.assertIn("$11,428,750", html)
        self.assertIn("$12,571,625", html)
        self.assertIn("$11,211,250", html)
        self.assertIn("2023", html)
        self.assertIn("2025", html)

    def test_faq_jsonld_is_valid_and_parcel_specific(self):
        p = _seed_real_parcel()
        html = self.client.get(reverse("parcel_detail", args=[p.folio])).content.decode()

        start = html.index('<script type="application/ld+json">') + len('<script type="application/ld+json">')
        end = html.index("</script>", start)
        data = json.loads(html[start:end])

        self.assertEqual(data["@type"], "FAQPage")
        questions = [q["name"] for q in data["mainEntity"]]
        self.assertIn("Who owns 41 E Flagler St?", questions)
        self.assertEqual(len(data["mainEntity"]), 4)
        # owner answer carries the raw string, no resolution
        owner_a = next(q["acceptedAnswer"]["text"] for q in data["mainEntity"] if "owns" in q["name"])
        self.assertIn("41 FLAGLER REALTY LLC", owner_a)
        # flood answer honestly reports absence
        flood_a = next(q["acceptedAnswer"]["text"] for q in data["mainEntity"] if "flood" in q["name"])
        self.assertIn("No FEMA flood-zone", flood_a)

    def test_reference_folio_is_noindexed(self):
        p = Parcel.objects.create(
            folio="0102090901321", county="Miami-Dade", state="FL",
            address_line="", land_use="REFERENCE FOLIO",
        )
        AssessmentSnapshot.objects.create(
            parcel=p, source="miami_dade_tax_roll", tax_year=2025,
            assessed_value=0, observed_at=OBS,
        )
        self.assertFalse(p.is_indexable)
        html = self.client.get(reverse("parcel_detail", args=[p.folio])).content.decode()
        self.assertIn('content="noindex,follow"', html)

    def test_common_area_with_address_is_noindexed(self):
        """Even with an address, a common-area parcel is thin."""
        p = Parcel.objects.create(
            folio="0101140301150", county="Miami-Dade", state="FL",
            address_line="300 S BISCAYNE BLVD",
            land_use="PVT PARK -REC AREA -ROADWAY : COMMON AREA",
        )
        AssessmentSnapshot.objects.create(
            parcel=p, source="miami_dade_tax_roll", tax_year=2025,
            assessed_value=500000, observed_at=OBS,
        )
        self.assertFalse(p.is_indexable)

    def test_address_present_but_no_value_is_noindexed(self):
        p = Parcel.objects.create(
            folio="9999999999999", county="Miami-Dade", state="FL",
            address_line="1 NOWHERE ST", land_use="STORE : RETAIL OUTLET",
        )
        self.assertFalse(p.is_indexable)  # no positive assessed value
