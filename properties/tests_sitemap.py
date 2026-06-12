"""
Tests for the parcel sitemap (/sitemap.xml).

The sitemap must list exactly the indexable parcels and never leak a thin/junk
folio. It is generated from Parcel.objects.indexable() (no hardcoded list), so
these tests also pin that queryset to the per-object Parcel.is_indexable guard.
"""

import re
import xml.dom.minidom as minidom
from datetime import datetime, timezone as dt_tz

from django.test import TestCase

from properties.models import Parcel, AssessmentSnapshot

OBS = datetime(2026, 6, 4, tzinfo=dt_tz.utc)


def _indexable_parcel(folio="0101110601160", addr="41 E FLAGLER ST"):
    p = Parcel.objects.create(
        folio=folio, county="Miami-Dade", state="FL",
        address_line=addr, city="Miami", zip_code="33131-0000",
        land_use="STORE : RETAIL OUTLET",
    )
    AssessmentSnapshot.objects.create(
        parcel=p, source="miami_dade_tax_roll", tax_year=2025,
        assessed_value=11211250, observed_at=OBS,
    )
    return p


def _reference_folio():
    p = Parcel.objects.create(
        folio="0102090901321", county="Miami-Dade", state="FL",
        address_line="", land_use="REFERENCE FOLIO",
    )
    AssessmentSnapshot.objects.create(
        parcel=p, source="miami_dade_tax_roll", tax_year=2025,
        assessed_value=0, observed_at=OBS,
    )
    return p


def _common_area():
    p = Parcel.objects.create(
        folio="0101140301150", county="Miami-Dade", state="FL",
        address_line="300 S BISCAYNE BLVD",
        land_use="PVT PARK -REC AREA -ROADWAY : COMMON AREA",
    )
    AssessmentSnapshot.objects.create(
        parcel=p, source="miami_dade_tax_roll", tax_year=2025,
        assessed_value=500000, observed_at=OBS,
    )
    return p


class SitemapTests(TestCase):
    def setUp(self):
        self.good = _indexable_parcel()
        self.ref = _reference_folio()
        self.common = _common_area()

    def _locs(self):
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("xml", resp["Content-Type"])
        minidom.parseString(resp.content)  # raises if malformed
        return resp, re.findall(r"<loc>(.*?)</loc>", resp.content.decode())

    def test_sitemap_serves_well_formed_xml(self):
        resp, locs = self._locs()
        self.assertTrue(locs)

    def test_sitemap_count_matches_indexable(self):
        _, locs = self._locs()
        self.assertEqual(len(locs), Parcel.objects.indexable().count())

    def test_only_indexable_present_no_junk(self):
        _, locs = self._locs()
        folios = [re.search(r"/property/([^/]+)/", u).group(1) for u in locs]
        self.assertIn(self.good.folio, folios)        # 41 E Flagler St present
        self.assertNotIn(self.ref.folio, folios)      # reference folio excluded
        self.assertNotIn(self.common.folio, folios)   # common area excluded

    def test_sitemap_urls_are_https_property_paths(self):
        _, locs = self._locs()
        for u in locs:
            self.assertTrue(u.startswith("https://"))
            self.assertRegex(u, r"/property/[^/]+/$")

    def test_good_folio_in_sitemap_renders_indexable(self):
        html = self.client.get(f"/property/{self.good.folio}/").content.decode()
        self.assertNotIn("noindex", html)

    def test_queryset_matches_object_guard(self):
        """indexable() queryset must equal the per-object is_indexable set."""
        qs = set(Parcel.objects.indexable().values_list("folio", flat=True))
        prop = {p.folio for p in Parcel.objects.all() if p.is_indexable}
        self.assertEqual(qs, prop)
