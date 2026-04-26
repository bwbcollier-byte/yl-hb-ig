# CLAUDE.md — `yl-hb-ig` (Instagram enrichment via Airtable)

This file teaches Claude how this repo is laid out. Conventions shared across
the `yl-hb-*` fleet live in [`SCRAPER-CLAUDE-TEMPLATE.md`](../SCRAPER-CLAUDE-TEMPLATE.md)
— read both. **This repo diverges from the template in a few specific ways**
because it doesn't talk to Supabase at all.

## What this repo does

Pulls Airtable records from a specific Instagram view, queries the Simple
Instagram API on RapidAPI (rotating through 11 keys to avoid per-key limits),
and bulk-PATCHes Airtable records (10 at a time) with follower counts, bio,
contact links, etc.

## Stack

**Custom variant — Airtable-only.** Single Python script
(`enrich_instagram_airtable.py`), no Supabase, no `requirements.txt` at the
repo root (deps are installed inline in the workflow). Runs as a single
GitHub Actions workflow.

## Repo layout

```
enrich_instagram_airtable.py          # the entire repo
.github/workflows/
  enrich_instagram.yml                # cron + workflow_dispatch
```

## Auth

> Convention divergence: this repo does **not** use `SUPABASE_*` env vars.

```
AIRTABLE_API_KEY        # required
AIRTABLE_BASE_ID        # default appuvMdeNd1hgrGNU baked into the script
AIRTABLE_TABLE_ID       # default tblClKjrh2wJwxXuI
AIRTABLE_VIEW_ID        # default viwVsDgCKCtD9Rzvg
RAPIDAPI_KEY_1 … _11    # 11 separate RapidAPI keys, rotated to dodge per-key rate limits
```

The Airtable IDs default-fall-back to in-script values for local dev. The
RapidAPI keys are rotated round-robin through requests.

## Workflow lifecycle convention

> Convention divergence: this repo does **not** call `log_workflow_run`.
> No Supabase auth means no `public.workflows` row to update. The dashboard
> won't see runs of this job. If observability is wanted, add the
> service-role auth and the standard start/result blocks per template.

## Tables this repo touches

Airtable only:

| Source | Operation | Notes |
|---|---|---|
| Airtable base `appuvMdeNd1hgrGNU`, view `viwVsDgCKCtD9Rzvg` | SELECT (paged) and PATCH (10/req) | Followers, biography, contact info, etc. |

No Supabase tables touched.

## Running locally

```bash
pip install requests
export AIRTABLE_API_KEY=...
export RAPIDAPI_KEY_1=... RAPIDAPI_KEY_2=... # … _11 for full throughput
python3 enrich_instagram_airtable.py --limit 10     # try a small run first
python3 enrich_instagram_airtable.py --all          # full sweep
```

## Per-repo gotchas

- **Hardcoded fallback values for Airtable IDs and RapidAPI keys were
  removed for security** (note in the script: "FALLBACKS REMOVED FOR
  SECURITY (GitHub Push Protection)"). Don't reintroduce hardcoded keys.
- **11 RapidAPI keys are required for full throughput.** Fewer keys means
  more 429s and slower runs. The rotation is round-robin under a `Lock()`.
- **Airtable rate limits at 5 req/sec per base.** The bulk-PATCH-10 pattern
  is essential — don't switch to single-record updates.
- **No retry-with-backoff for 5xx Airtable responses** in the current
  script. If you see partial-update bugs in CI, check that.

## Conventions Claude should follow when editing this repo

- **Don't add a Supabase client to this repo unless the data layer
  intentionally migrates from Airtable.** This is one of the few `yl-hb-*`
  repos that's deliberately Airtable-only.
- **Don't reintroduce hardcoded RapidAPI keys.** The script has been
  scrubbed of fallbacks; keep it that way.

## Related repos

- `yl-hb-sk`, `yl-hb-sc`, `yl-hb-tw` — sibling Airtable-only enrichers (Songkick, SoundCloud, Twitter).
- The other `yl-hb-*` repos write to Supabase; this one stops at Airtable.
