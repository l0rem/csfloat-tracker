# CSFloat Listing Monitor

Local Python monitor for CSFloat listings with:
- Telegram notifications for new listings, price changes, and delists
- Targeted def-index tracking (defaults: Alyx + Valeria pins)
- Photo-first Telegram alerts (in-game screenshot when available, icon fallback)
- New listing alerts include inspect link and seller note when available
- `% vs sold average` enrichment based on historical delisted prices
- Auto schema migrations on startup via `peewee-db-evolve`

## Requirements

- `uv`
- Python `3.13.12` (project is pinned in `.python-version`)

## Setup

```bash
cp .env.example .env
# Fill in .env with your real API token values.
uv sync
```

## Resolve Telegram Chat ID

Send at least one message to your bot first, then run:

```bash
uv run python monitor.py resolve-chat-id --username loremsh
```

This stores the resolved chat id in the `settings` table.

## Run Monitor

```bash
uv run python monitor.py run
```

The script will:
1. Auto-run unattended migrations.
2. Execute startup diff against existing DB snapshot (no spam on first run when snapshot is empty).
3. Poll every 30 seconds (or `POLL_INTERVAL_SECONDS`) for tracked def indexes.
4. Persist append-only row-level change history in `item_changes`.
5. Send immediate Telegram messages for `new`, `price changed`, and `delisted` events.

## Tests

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

## Notes

- Rotate exposed credentials and keep `.env` local.
- Listing URL defaults to `https://csfloat.com/item/{listing_id}`.
- Screenshot URL defaults to `https://csfloat.pics/m/{screenshot_id}/playside.png?v=3`.
- Supabase/Postgres is configured through `DATABASE_URL`.
- Use the exact Postgres URI from Supabase Dashboard (`Connect`), because host/user formats vary by region/pooler.
- `SQLITE_PATH` still works as an optional local fallback for dev/testing.
- `peewee-db-evolve` is used for unattended startup migration checks, with a SQLite compatibility shim in `models.py`.
- Price notifications are shown in `DISPLAY_CURRENCY` (default `EUR`) using CSFloat `meta/exchange-rates` data.

## Docker

Build and run:

```bash
docker build -t csfloat-monitor .
docker run --rm --env-file .env csfloat-monitor
```

## Dokploy

Use the provided `Dockerfile` and set these environment variables:

- `CSFLOAT_API_KEY`
- `CSFLOAT_LISTINGS_URL`
- `ITEM_URL_TEMPLATE`
- `SCREENSHOT_URL_TEMPLATE`
- `CSFLOAT_PROXY` (optional; `host:port:user:pass` or full proxy URL)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DATABASE_URL` (Supabase, include `?sslmode=require`)
- `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `POLL_INTERVAL_SECONDS`
- `CSFLOAT_TARGET_DEF_INDEXES` (comma-separated def indexes; defaults to Alyx + Valeria pins)
- `HTTP_TIMEOUT_SECONDS`
- `HTTP_MAX_RETRIES`
- `HTTP_429_RETRIES`
- `HTTP_BACKOFF_SECONDS`
- `HTTP_MAX_BACKOFF_SECONDS`
- `HTTP_PAGE_DELAY_SECONDS`
- `DISPLAY_CURRENCY`
- `EXCHANGE_RATE_CACHE_TTL_SECONDS`
- `MARKET_AVG_CACHE_TTL_SECONDS`
- `MARKET_AVG_MIN_SAMPLES`

For 429-heavy environments (containers/VPS), increase resilience:
- `HTTP_MAX_RETRIES=8`
- `HTTP_429_RETRIES=1`
- `HTTP_BACKOFF_SECONDS=1.5`
- `HTTP_MAX_BACKOFF_SECONDS=90`
- `HTTP_PAGE_DELAY_SECONDS=0.35`

Key log events to watch after deploy:
- `startup_config` (effective runtime config without secrets)
- `migrations_start` / `migrations_complete`
- `poll_start` / `poll_fetch_complete` / `poll_diff_complete` / `poll_complete`
- `fetch_transient_error` (429/5xx retries with delay)
- `notify_photo_sent` or `notify_text_sent`
- `poll_failed` / `startup_poll_failed` / `fatal_error`
