"""
Vanthir property-intelligence data model.

FIVE LOAD-BEARING DECISIONS (do not erode these as the codebase grows):

1. ADDRESS/PARCEL IS THE SPINE. Parcel is the universal join key. Owners change,
   buildings sell, LLCs dissolve, but the parcel (folio) persists, and every
   public record keys to it. Nothing else is the primary entity.

2. RESOLVE OWNERS LATER. OwnershipSnapshot always stores the verbatim deed string
   in `owner_name_raw` and never loses it. `owner_entity` stays NULL until a
   separate, confidence-gated resolution job links it to a canonical OwnerEntity.
   This lets v1 (the buyer report) ship with raw owner strings; the portfolio
   tree fills in later with no re-ingestion.

3. SOURCE ON EVERYTHING. Every fact inherits SourcedRecord: source, source_url,
   source_record_id (for idempotent dedup), effective_date (when the event
   happened) and observed_at (when we scraped it). The timeline IS the product,
   so provenance is non-negotiable.

4. THE FCRA WALL. Vanthir furnishes intelligence about PROPERTIES and ENTITIES,
   never eligibility determinations about INDIVIDUALS. CourtFiling deliberately
   does not expose individual party names as a queryable/screening field. We are
   not a consumer reporting agency.

5. SCORES DECOMPOSE INTO SOURCED FACTS. A HealthScore cannot exist without
   ScoreFactor rows that each cite the underlying records. There is never a naked
   number about a named entity — only "here are the dated, sourced facts and the
   methodology." This is the trade-libel defense.

APPEND-ONLY PATTERN: events (violations, permits, filings) dedup on
(source, source_record_id) so re-scraping is idempotent. State that changes over
time (assessment, flood zone, ownership, HOA reserves) gets a NEW dated row every
observation instead of an UPDATE — that trajectory is the "time machine" moat.
Ingestion code must never .update() these; it inserts.
"""

from django.db import models


# ---------------------------------------------------------------------------
# Provenance primitives
# ---------------------------------------------------------------------------

class RawSnapshot(models.Model):
    """Unparsed payload, stored before anything touches it.

    When a parser improves (and against county data it will, repeatedly), we
    reprocess from raw instead of re-scraping, and we keep provenance for every
    derived row if an entity ever disputes something.
    """
    source = models.CharField(max_length=120, db_index=True)
    source_url = models.URLField(max_length=1000, blank=True)
    source_record_id = models.CharField(max_length=255, blank=True, db_index=True)
    payload = models.JSONField()
    content_type = models.CharField(max_length=80, default="application/json")
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["source", "source_record_id"])]

    def __str__(self):
        return f"{self.source}:{self.source_record_id or self.pk} @ {self.observed_at:%Y-%m-%d}"


class SourcedRecord(models.Model):
    """Abstract base: every fact carries source + date provenance."""
    source = models.CharField(max_length=120, db_index=True)
    source_url = models.URLField(max_length=1000, blank=True)
    source_record_id = models.CharField(max_length=255, blank=True, db_index=True)
    raw_snapshot = models.ForeignKey(
        RawSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # When the event/fact actually happened (county date).
    effective_date = models.DateField(null=True, blank=True, db_index=True)
    # When we observed/scraped it.
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Spine entities
# ---------------------------------------------------------------------------

class OwnerEntity(models.Model):
    """Canonical owner (LLC, trust, person, fund). Populated by the resolution
    job. Many parcels map to one OwnerEntity once resolved — this is the
    portfolio tree. Self-referential parent for LLC->fund->parent chains."""
    canonical_name = models.CharField(max_length=400, db_index=True)
    entity_type = models.CharField(
        max_length=40,
        choices=[
            ("llc", "LLC"),
            ("trust", "Trust"),
            ("individual", "Individual"),
            ("corp", "Corporation"),
            ("fund", "Fund"),
            ("government", "Government"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
    )
    registered_agent = models.CharField(max_length=400, blank=True)
    state_registry_id = models.CharField(max_length=120, blank=True, db_index=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="subsidiaries"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "owner entities"

    def __str__(self):
        return self.canonical_name


class ManagementCompany(models.Model):
    """First-class entity (stubbed for v1). The fast-follow where real LLC
    resolution gets built. Wired in now so adding it later isn't a migration
    headache, but NOT on the critical path to shipping the buyer report."""
    name = models.CharField(max_length=400, db_index=True)
    owner_entity = models.ForeignKey(
        OwnerEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="mgmt_companies"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "management companies"

    def __str__(self):
        return self.name


class HOA(models.Model):
    """Homeowners association / condo association. Governs many parcels."""
    name = models.CharField(max_length=400, db_index=True)
    state_registry_id = models.CharField(max_length=120, blank=True, db_index=True)
    management_company = models.ForeignKey(
        ManagementCompany, null=True, blank=True, on_delete=models.SET_NULL, related_name="hoas"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "HOA"
        verbose_name_plural = "HOAs"

    def __str__(self):
        return self.name


class ParcelQuerySet(models.QuerySet):
    def indexable(self):
        """Set-level twin of `Parcel.is_indexable` — the same thin-page guard as
        a queryset, so the sitemap can select indexable parcels in one query
        (and auto-scale as more ZIPs are ingested) instead of a per-row Python
        loop. Kept in lockstep with `is_indexable` by tests_sitemap.py."""
        qs = self.exclude(address_line__regex=r"^\s*$")  # no blank/whitespace address
        for marker in Parcel.THIN_LAND_USE_MARKERS:
            qs = qs.exclude(land_use__icontains=marker)  # no reference/common-area
        return qs.filter(assessments__assessed_value__gt=0).distinct()  # has real value


class Parcel(models.Model):
    """THE SPINE. The address/folio everything attaches to."""
    folio = models.CharField(max_length=80, unique=True, db_index=True)  # county parcel id
    county = models.CharField(max_length=120, default="Miami-Dade", db_index=True)
    state = models.CharField(max_length=2, default="FL")
    address_line = models.CharField(max_length=400, blank=True)
    city = models.CharField(max_length=160, blank=True)
    zip_code = models.CharField(max_length=12, blank=True, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    year_built = models.PositiveIntegerField(null=True, blank=True)
    beds = models.PositiveIntegerField(null=True, blank=True)
    baths = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    living_sqft = models.PositiveIntegerField(null=True, blank=True)
    land_use = models.CharField(max_length=120, blank=True)

    hoa = models.ForeignKey(
        HOA, null=True, blank=True, on_delete=models.SET_NULL, related_name="parcels"
    )
    management_company = models.ForeignKey(
        ManagementCompany, null=True, blank=True, on_delete=models.SET_NULL, related_name="parcels"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ParcelQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["county", "zip_code"]),
            models.Index(fields=["state", "county"]),
        ]

    def __str__(self):
        return self.address_line or self.folio

    # Land-use strings that mark a parcel as administrative, not a real place.
    THIN_LAND_USE_MARKERS = ("REFERENCE FOLIO", "COMMON AREA")

    @property
    def is_indexable(self):
        """Thin-page guard. A parcel earns an indexable public page only if it
        is a real, describable place with real data. Reference folios, common-
        area parcels, address-less parcels, and parcels with no positive
        assessed value (the junk flagged in PROGRESS.md) are NOT indexable —
        the view renders them with <meta robots noindex> so they never enter
        the index as thin pages. Pure/derived; no migration."""
        if not (self.address_line or "").strip():
            return False
        land_use = (self.land_use or "").upper()
        if any(marker in land_use for marker in self.THIN_LAND_USE_MARKERS):
            return False
        # Needs at least one positive assessed value (any source) to be real.
        if not self.assessments.filter(assessed_value__gt=0).exists():
            return False
        return True


class Building(models.Model):
    """A structure on a parcel. One parcel can hold many buildings/units."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="buildings")
    name = models.CharField(max_length=400, blank=True)
    unit_count = models.PositiveIntegerField(null=True, blank=True)
    stories = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or f"Building @ {self.parcel}"


class Unit(models.Model):
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name="units")
    unit_number = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("building", "unit_number")]

    def __str__(self):
        return f"{self.building} #{self.unit_number}"


# ---------------------------------------------------------------------------
# Append-only EVENTS (dedup on source+source_record_id; never updated)
# ---------------------------------------------------------------------------

class Violation(SourcedRecord):
    """Code-enforcement violation. The time-series goldmine."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="violations")
    case_number = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=40,
        choices=[("open", "Open"), ("closed", "Closed"), ("unknown", "Unknown")],
        default="unknown",
    )
    opened_date = models.DateField(null=True, blank=True)
    closed_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_record_id"],
                name="uniq_violation_source_record",
            )
        ]
        indexes = [models.Index(fields=["parcel", "effective_date"])]


class Permit(SourcedRecord):
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="permits")
    permit_number = models.CharField(max_length=120, blank=True)
    work_description = models.TextField(blank=True)
    contractor_name = models.CharField(max_length=400, blank=True)
    status = models.CharField(max_length=80, blank=True)
    issued_date = models.DateField(null=True, blank=True)
    final_inspection_passed = models.BooleanField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_record_id"],
                name="uniq_permit_source_record",
            )
        ]
        indexes = [models.Index(fields=["parcel", "effective_date"])]


class CourtFiling(SourcedRecord):
    """Eviction / landlord-tenant / HOA litigation / foreclosure.

    FCRA WALL: this stores the filing as a PROPERTY/ENTITY-level event. It
    deliberately does NOT expose individual party names as a structured,
    queryable screening field. We never furnish a determination about an
    individual. `caption_raw` exists only as opaque provenance text, never as
    a filter/search field for tenant screening.
    """
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="court_filings")
    case_number = models.CharField(max_length=120, blank=True)
    filing_type = models.CharField(
        max_length=60,
        choices=[
            ("eviction", "Eviction"),
            ("foreclosure", "Foreclosure"),
            ("hoa_litigation", "HOA Litigation"),
            ("landlord_tenant", "Landlord/Tenant"),
            ("other", "Other"),
        ],
        default="other",
    )
    # Opaque provenance only. NOT indexed, NOT a screening field. See FCRA wall.
    caption_raw = models.TextField(blank=True)
    disposition = models.CharField(max_length=160, blank=True)
    filed_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_record_id"],
                name="uniq_courtfiling_source_record",
            )
        ]
        indexes = [models.Index(fields=["parcel", "effective_date"])]


class Deed(SourcedRecord):
    """Sale / transfer event. Captures the verbatim grantor/grantee strings."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="deeds")
    instrument_number = models.CharField(max_length=120, blank=True)
    grantor_raw = models.CharField(max_length=600, blank=True)
    grantee_raw = models.CharField(max_length=600, blank=True)
    sale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    recorded_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_record_id"],
                name="uniq_deed_source_record",
            )
        ]
        indexes = [models.Index(fields=["parcel", "effective_date"])]


# ---------------------------------------------------------------------------
# Append-only STATE-OVER-TIME (new dated row every observation; never updated)
# ---------------------------------------------------------------------------

class OwnershipSnapshot(SourcedRecord):
    """Append-only ownership over time. RESOLVE-OWNERS-LATER lives here:
    `owner_name_raw` is the verbatim deed/appraiser string and is never lost;
    `owner_entity` stays NULL until the resolution job confidently links it."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="ownership_snapshots")
    owner_name_raw = models.CharField(max_length=600)  # NEVER null, NEVER lost
    mailing_address_raw = models.CharField(max_length=600, blank=True)
    owner_entity = models.ForeignKey(
        OwnerEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="ownership_snapshots"
    )
    resolution_confidence = models.DecimalField(
        max_digits=4, decimal_places=3, null=True, blank=True,
        help_text="0-1; null until resolution attempted",
    )

    class Meta:
        indexes = [models.Index(fields=["parcel", "observed_at"])]


class AssessmentSnapshot(SourcedRecord):
    """Append-only assessed/market value + tax over time -> value trajectory."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="assessments")
    tax_year = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    assessed_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    market_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    annual_tax = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["parcel", "tax_year"])]


class FloodZoneSnapshot(SourcedRecord):
    """Append-only FEMA flood-zone designation over time."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="flood_zones")
    zone = models.CharField(max_length=40, blank=True)
    in_sfha = models.BooleanField(null=True, help_text="Special Flood Hazard Area")
    panel = models.CharField(max_length=60, blank=True)

    class Meta:
        indexes = [models.Index(fields=["parcel", "observed_at"])]


class HOAFinancialSnapshot(SourcedRecord):
    """Append-only HOA financial health over time. The reserve/assessment
    trajectory that lenders (post-Surfside) and buyers need."""
    hoa = models.ForeignKey(HOA, on_delete=models.CASCADE, related_name="financial_snapshots")
    reserve_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reserve_funded_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    special_assessment_active = models.BooleanField(null=True)
    special_assessment_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    litigation_active = models.BooleanField(null=True)
    fiscal_year = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["hoa", "observed_at"])]


class InsuranceRiskSnapshot(SourcedRecord):
    """Append-only insurance/climate-risk trajectory (homeowner/buyer product)."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="insurance_risk")
    avg_premium = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    carriers_available = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["parcel", "observed_at"])]


# ---------------------------------------------------------------------------
# Verified resident data (STRUCTURED fields, not narrative reviews)
# ---------------------------------------------------------------------------

class ResidentVerification(models.Model):
    """How a resident's contribution was verified. Displayed anonymously."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="verifications")
    method = models.CharField(
        max_length=40,
        choices=[
            ("lease_upload", "Lease Upload"),
            ("utility_bill", "Utility Bill"),
            ("mailed_postcard", "Mailed Postcard Code"),
            ("unverified", "Unverified"),
        ],
        default="unverified",
    )
    status = models.CharField(
        max_length=20,
        choices=[("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")],
        default="pending",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MaintenanceReport(models.Model):
    """Structured maintenance data point -> computes response time."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="maintenance_reports")
    verification = models.ForeignKey(
        ResidentVerification, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    category = models.CharField(max_length=120, blank=True)
    requested_date = models.DateField(null=True, blank=True)
    acknowledged_date = models.DateField(null=True, blank=True)
    resolved_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DepositReport(models.Model):
    """Structured deposit data point -> computes return rate per building."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="deposit_reports")
    verification = models.ForeignKey(
        ResidentVerification, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    amount_returned = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    days_to_return = models.PositiveIntegerField(null=True, blank=True)
    itemized_deductions = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RentReport(models.Model):
    """Structured rent data point -> computes increase trajectory."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="rent_reports")
    verification = models.ForeignKey(
        ResidentVerification, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    lease_start_rent = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    current_rent = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    renewal_date = models.DateField(null=True, blank=True)
    added_fees = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class IncidentReport(models.Model):
    """Dated building-health incidents -> building-health timeline."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="incident_reports")
    verification = models.ForeignKey(
        ResidentVerification, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    incident_type = models.CharField(
        max_length=40,
        choices=[
            ("ac_outage", "AC Outage"),
            ("elevator_down", "Elevator Down"),
            ("flooding", "Flooding"),
            ("pests", "Pests"),
            ("package_theft", "Package Theft"),
            ("safety", "Safety"),
            ("other", "Other"),
        ],
        default="other",
    )
    occurred_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# Health score — MUST decompose into sourced factors
# ---------------------------------------------------------------------------

class HealthScore(models.Model):
    """A computed score for a parcel (or, later, an entity). Append-only by
    computed_at so the score itself has a history.

    INVARIANT: a HealthScore is meaningless without its ScoreFactor rows. The
    `value` is never published as a naked number — the UI always renders the
    decomposition. Enforced in code via `is_defensible`."""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name="health_scores")
    dimension = models.CharField(
        max_length=40,
        choices=[
            ("overall", "Overall"),
            ("financial", "Financial Health"),
            ("maintenance", "Maintenance"),
            ("legal", "Legal/Litigation"),
            ("risk", "Climate/Insurance Risk"),
        ],
        default="overall",
    )
    value = models.PositiveSmallIntegerField(help_text="0-100")
    methodology_version = models.CharField(max_length=40, default="v1")
    methodology_url = models.URLField(max_length=1000, blank=True)
    computed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["parcel", "dimension", "computed_at"])]

    @property
    def is_defensible(self):
        """A score may only be published if it cites at least one sourced factor."""
        return self.factors.exists()

    def __str__(self):
        return f"{self.parcel} {self.dimension}={self.value}"


class ScoreFactor(models.Model):
    """One reason a score moved, citing a sourced record. No factor -> no
    defensible score (trade-libel defense)."""
    score = models.ForeignKey(HealthScore, on_delete=models.CASCADE, related_name="factors")
    label = models.CharField(max_length=400)  # e.g. "Reserve funding below recommendation"
    impact = models.SmallIntegerField(help_text="signed contribution to the score")
    # Provenance for the factor — what sourced record(s) justify it.
    source = models.CharField(max_length=120, blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.label} ({self.impact:+d})"
