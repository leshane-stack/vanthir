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

## Open decisions for next time
- **Assessed value / value trajectory:** the appraiser GIS view only carries the current,
  pre-certification year. Decide on a source for prior-year assessment rolls to build the
  value-over-time series.
- **Bounded-decimal coverage:** `_dec_fit` is applied to `baths`/`lat`/`lng`. `sale_price`
  and `assessed_value` (both `DecimalField(14,2)`, large headroom) still use plain `_dec`.
  Low risk, but a single bad county number there would crash a batch — consider routing
  them through `_dec_fit` too for full defense-in-depth.
- **Deeds only capture the most recent sale** (`DOS_1`/`PRICE_1`). The source likely exposes
  DOS_2/3… — decide whether to ingest the full sale history per parcel.
- Out of scope and not started (by design): other data sources, page generation, buyer
  report, scores, entity resolution.

## Machine note
If a Django command ever fails with `No module named 'config'`, run
`unset DJANGO_SETTINGS_MODULE` and retry (stale env var). Didn't recur this session.
