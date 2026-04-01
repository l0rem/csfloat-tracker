# CSFloat Listing Monitor

Local Python monitor for CSFloat listings with:
- PostgreSQL snapshot + append-only item change history
- Telegram notifications for new listings, price changes, and delists
- Photo-first Telegram alerts (in-game screenshot when available, icon fallback)
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
2. Execute startup diff against existing DB snapshot.
3. Poll every 30 seconds (or `POLL_INTERVAL_SECONDS`).
4. Persist append-only row-level change history in `item_changes`.
5. Send immediate Telegram messages with changed fields and old/new values.

## Tests

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

## Notes

- Rotate exposed credentials and keep `.env` local.
- Listing button URL defaults to `https://csfloat.com/item/{listing_id}`.
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
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DATABASE_URL` (Supabase, include `?sslmode=require`)
- `POLL_INTERVAL_SECONDS`
- `HTTP_TIMEOUT_SECONDS`
- `HTTP_MAX_RETRIES`
- `HTTP_BACKOFF_SECONDS`
- `DISPLAY_CURRENCY`
- `EXCHANGE_RATE_CACHE_TTL_SECONDS`
