from datetime import date, datetime, timezone

from django.db import IntegrityError, transaction
from django.test import TestCase

from properties.models import (
    Parcel, OwnerEntity, OwnershipSnapshot, AssessmentSnapshot,
    Violation, HealthScore, ScoreFactor,
)


def _ts(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class InvariantTests(TestCase):
    """Each test guards one load-bearing decision."""

    def setUp(self):
        self.parcel = Parcel.objects.create(
            folio="01-1234-567-8901", address_line="123 Brickell Ave, Miami FL"
        )

    def test_resolution_without_losing_raw(self):
        """DECISION 2: owner_name_raw is never lost; owner_entity fills later."""
        snap = OwnershipSnapshot.objects.create(
            parcel=self.parcel,
            owner_name_raw="SUNRISE HOLDINGS 4421 LLC",
            source="miami_dade_appraiser",
            observed_at=_ts(2026, 1, 1),
        )
        self.assertIsNone(snap.owner_entity)
        self.assertEqual(snap.owner_name_raw, "SUNRISE HOLDINGS 4421 LLC")

        # Later: resolution job links it WITHOUT re-ingesting or losing the raw string.
        entity = OwnerEntity.objects.create(canonical_name="Sunrise Holdings", entity_type="llc")
        snap.owner_entity = entity
        snap.resolution_confidence = 0.92
        snap.save()
        snap.refresh_from_db()
        self.assertEqual(snap.owner_entity, entity)
        self.assertEqual(snap.owner_name_raw, "SUNRISE HOLDINGS 4421 LLC")  # still intact

    def test_append_only_events_dedup(self):
        """APPEND-ONLY: re-scraping the same record is idempotent, not duplicated."""
        common = dict(
            parcel=self.parcel, source="miami_dade_code", source_record_id="CE-2025-0001",
            description="Unpermitted work", observed_at=_ts(2026, 1, 1),
        )
        Violation.objects.create(**common)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Violation.objects.create(**common)  # same source+record_id rejected
        self.assertEqual(Violation.objects.count(), 1)

    def test_state_over_time_is_a_trajectory(self):
        """APPEND-ONLY: state changes create new dated rows, building a trajectory."""
        for yr, val in [(2023, 300000), (2024, 360000), (2025, 420000)]:
            AssessmentSnapshot.objects.create(
                parcel=self.parcel, source="miami_dade_appraiser",
                tax_year=yr, assessed_value=val, observed_at=_ts(yr, 6, 1),
            )
        traj = list(
            AssessmentSnapshot.objects.filter(parcel=self.parcel)
            .order_by("tax_year").values_list("assessed_value", flat=True)
        )
        self.assertEqual([int(v) for v in traj], [300000, 360000, 420000])

    def test_score_requires_citation(self):
        """DECISION 5: a score is only defensible if it decomposes into factors."""
        score = HealthScore.objects.create(
            parcel=self.parcel, dimension="financial", value=61, computed_at=_ts(2026, 1, 1),
        )
        self.assertFalse(score.is_defensible)  # naked number -> not publishable

        ScoreFactor.objects.create(
            score=score,
            label="Reserve funding below recommendation per 2025 filing",
            impact=-15, source="hoa_filing", effective_date=date(2025, 12, 1),
        )
        self.assertTrue(score.is_defensible)  # now backed by a sourced fact
