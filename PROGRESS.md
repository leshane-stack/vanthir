# Vanthir — Progress / Handoff

_Last updated: 2026-06-04_

## What's built and working
- **Data model** (`properties/models.py`): full schema honoring the five invariants —
  Parcel as spine, resolve-owners-later (`OwnershipSnapshot.owner_name_raw` verbatim,
  `owner_entity` null), source-on-everything (`SourcedRecord` + `RawSnapshot`), the FCRA
  wall (`CourtFiling.caption_raw` is opaque provenance, not a screening field), and
  scores-decompose-into-factors (`HealthScore.is_defensible`).
- **Appraiser ingest adapter** (`properties/ingest.py`): Miami-Dade Property Appraiser
  GIS FeatureServer. `fetch_page()` (network) is separated from `ingest_feature()` (pure
  mapping). Writes RawSnapshot first; upserts Parcel by folio; ownership append-only on
  change; assessment idempotent per (parcel, source, tax_year); deed deduped on
  (source, source_record_id).
- **Management command** `python manage.py ingest_appraiser` (`--folio` / `--zip` /
  `--where` / `--limit`).
- **Tests** (`properties/tests_ingest.py`): 5 passing (3 pre-existing + 2 added). Full
  suite: 9 passing.

## What this session added
1. **Initialized git** (repo had no `.git`) and made a base commit; project is now under
   version control so changes are reversible.
2. **Fixed a real slice-scale crash.** Running `ingest_appraiser --zip 33131 --limit 200`
   died with `decimal.InvalidOperation` on folio `0102100301130`, which carries an absurd
   county `BATTHROOM_COUNT = 1220`. `baths` is `DecimalField(4,1)` (max 999.9), so the
   value overflowed on save and aborted the whole batch. Added `_dec_fit(v, max_digits,
   decimal_places)` — returns the Decimal only if it fits the field, else `None` (treated
   as unknown, **not** clamped to a fabricated number). The verbatim value is never lost;
   it stays in the RawSnapshot payload. Applied to `baths`, `latitude`, `longitude` (the
   narrow-precision fields where county garbage realistically overflows).
3. **TDD for idempotency at slice scale** (`SliceScaleIdempotencyTests`): wrote the
   failing test first (it reproduced the 1220-baths crash), then made it pass. Covers
   (a) the absurd-baths record ingests as `baths=None` with raw 1220 preserved, and
   (b) re-running an identical multi-record slice does not duplicate deeds, same-year
   assessments, or unchanged-ownership rows.
4. Verified live idempotency end-to-end: ran the command **twice** against a clean DB.

## Data-quality findings — ZIP 33131, 200 parcels (clean single run)
| Field | Count |
|---|---|
| Parcels ingested | 200 |
| With owner string (OwnershipSnapshot) | 198 |
| With assessment (AssessmentSnapshot) | 200 |
| With sale/deed | 134 |
| With lat **and** lng | 200 |

**Live re-run (idempotency proof):** parcels 200→200, ownership 198→198, assessments
200→200, deeds 134→134. Only RawSnapshot grows (200→400) — intentional, provenance is
logged every scrape.

### Flagged / suspect — all explained, none are parser bugs
- **All 200 `assessed_value` are NULL.** The county GIS view's current cycle is tax year
  **2026**, and `ASSESSED_VAL_CUR` is genuinely empty in the source (assessment roll not
  certified yet as of 2026-06-04). `tax_year` is captured correctly. **Implication:** this
  single source yields no value *trajectory* yet — only the current (empty) year. The
  value-history moat needs prior-year roll data (a separate source/endpoint), out of scope
  this session.
- **2 parcels with no owner** (`0141019030001`, `0141019040001`): owner fields fully null
  in source (government / reference folios). Adapter correctly writes **no** OwnershipSnapshot
  rather than a blank `owner_name_raw` — invariant respected.
- **25 parcels with blank address**: all vacant land / parking lots / government / "REFERENCE
  FOLIO" — `TRUE_SITE_ADDR` genuinely null in source (unimproved parcels have no street address).
- **1 parcel with null baths** (`0102100301130`): the absurd 1220 value, now stored as
  unknown (raw preserved). No other absurd numerics.
- **year_built**: 76 null (vacant/unimproved); populated range **1920–2024** — all sane.
- **deeds**: `sale_price` null on 0 of 134 — adapter only writes a deed when both sale date
  and price are present.

## Session 2 (2026-06-04) — Historical assessments (prior-year value trajectory)

On branch `historical-assessments` (not merged). Two-phase: research, then build.

### Source found (research phase)
- **The appraiser GIS layer has no history.** Full audit of the Miami-Dade ArcGIS org
  (`8Pc9XBTAsYuxx9Ny`, 909 services): `PaGISView`/`PaParcelView` carry only
  `ASSESSED_VAL_CUR` (current cycle); the dated `Property2021Dec`/`Property2023Dec` layers
  are parcel-geometry only (no values); no sibling layer or related table has prior years.
- **Source used: Miami-Dade PA public JSON proxy** — `GetPropertySearchByFolio` at
  `https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx`
  `?Operation=GetPropertySearchByFolio&clientAppName=PropertySearch&folioNumber=<folio>`.
  Returns a multi-year assessment block per folio. (Geo-sensitive: was intermittently
  unreachable from some networks during research; reachable for the live run.)

### Built
- **`properties/ingest_historical_assessments.py`** (source label `miami_dade_tax_roll`) —
  SEPARATE from the appraiser adapter; the working `ingest.py` was not restructured.
  `fetch_folio()` (network) split from `ingest_assessment_history()` (pure mapping).
  Writes RawSnapshot first; ensures the Parcel spine exists by folio **without** touching
  appraiser-owned descriptors; writes one **dated, append-only** `AssessmentSnapshot` per
  year, idempotent per `(parcel, source, tax_year)`. Reuses `_dec_fit` from `ingest.py`.
- **Field mapping:** `tax_year←Year`, `assessed_value←AssessedValue`,
  `market_value←TotalValue`, `taxable_value←TaxableInfos[year].CountyTaxableValue`,
  `source_record_id="<folio>:<year>"`, `effective_date=Jan 1`. `LandValue`/`BuildingOnlyValue`/
  `ExtraFeatureValue` preserved in RawSnapshot only (no model fields). `annual_tax` left
  null (millage not in this payload).
- **Command** `python manage.py ingest_historical_assessments --zip 33131 [--limit N] | --folio <f>`.
  Joins by folio off already-ingested parcels; refuses county-wide runs.
- **Test-first** (`properties/tests_historical.py`, 4 tests): wrote failing tests
  (module absent) → implemented → green. Assert one dated row per year per parcel (incl. the
  2023 row where assessed≠market), idempotent re-run, no collision with appraiser rows
  (different source), and slice-scale multi-folio idempotency via a stubbed fetch.
- **Cross-adapter guardrail:** extended `_dec_fit` (fit-or-null) to `assessed_value` and
  `sale_price` in the appraiser `ingest.py` too — a bad county money value now yields NULL
  (raw kept) instead of crashing the batch. Full suite: **13 tests passing**.

### Data-quality findings — ZIP 33131 live backfill (200 parcels)
| Metric | Value |
|---|---|
| `miami_dade_tax_roll` AssessmentSnapshot rows | 594 |
| Parcels with ≥1 historical year | 200 / 200 |
| Parcels with full 3 years (2023–2025) | 196 |
| Parcels with 2 years / 1 year | 2 / 2 |
| NULL assessed/market/taxable | 0 / 0 / 0 |
| assessed_value range | $0 – $398,830,669 |

**Live idempotency proof:** re-ran 3 folios → tax_roll rows 594→594 (no dupes), RawSnapshot
+3 (provenance each scrape). Years present: 2023, 2024, 2025 (≈3 prior cycles; the proxy
did not return earlier years for this slice).

**Flagged / suspect — all explained, none are parser bugs:**
- **24 parcels with a $0 assessed year** — all `REFERENCE FOLIO` (admin placeholders) or
  `COMMON AREA` (private park/rec/roadway); legitimately non-taxable. (133 rows have
  `taxable_value=0` for the same reason.)
- **4 parcels with <3 years** — vacant land / parking / reference folios that only appear in
  recent rolls; we ingest whatever years the proxy returns.

## Open decisions for next time
- **Year depth:** this slice returned only 2023–2025 from the proxy (3 cycles). If we want
  5+ years, confirm whether the proxy exposes older rolls via another operation/param, or
  fall back to the Florida DOR NAL files (`floridarevenue.com`, bulk per-year JV/AV/TV).
- **`annual_tax` is null** — the proxy payload carried no millage/tax bill in the block we
  parsed (there is a `MillageList`/non-ad-valorem link we did not ingest). Decide whether to
  compute tax = taxable × millage, or pull the tax-bill endpoint.
- **Value breakdown not modeled** — `LandValue`/`BuildingOnlyValue`/`ExtraFeatureValue` are
  kept only in RawSnapshot. Add model fields if the buyer report needs the land/building split.
- **Backfill is per-folio (one HTTP call each)** — ~1.8s/folio (200 ≈ 5.5 min). Fine for a
  ZIP slice; would need batching/concurrency before any county-scale run.
- **Resolved this session:** prior-year value source (done — PA proxy); `_dec_fit` now covers
  `assessed_value`/`sale_price` across both adapters.
- Still out of scope (by design): other data sources beyond historical assessments, page
  generation, buyer report, scores, entity resolution. Branch not merged/deployed.

## Machine note
If a Django command ever fails with `No module named 'config'`, run
`unset DJANGO_SETTINGS_MODULE` and retry (stale env var). Didn't recur this session.
