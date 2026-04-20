from __future__ import annotations

import collections
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import peeweedbevolve  # noqa: F401
from peewee import (
    AutoField,
    BooleanField,
    CharField,
    Database,
    DatabaseProxy,
    DateTimeField,
    DoubleField,
    IntegerField,
    Model,
    OperationalError,
    PostgresqlDatabase,
    SqliteDatabase,
    TextField,
)


db_proxy: DatabaseProxy = DatabaseProxy()


def utc_now() -> datetime:
    return datetime.now(UTC)


class BaseModel(Model):
    class Meta:
        database = db_proxy


class PollRun(BaseModel):
    id = AutoField()
    started_at = DateTimeField(default=utc_now)
    finished_at = DateTimeField(null=True)
    status = CharField(default="running")
    is_startup = BooleanField(default=False)
    total_fetched = IntegerField(default=0)
    new_count = IntegerField(default=0)
    price_changed_count = IntegerField(default=0)
    delisted_count = IntegerField(default=0)
    error_message = TextField(null=True)

    class Meta:
        table_name = "poll_runs"


class CurrentListing(BaseModel):
    listing_id = CharField(primary_key=True)
    listing_url = TextField()
    price = IntegerField(null=True)
    state = CharField(null=True)
    market_hash_name = TextField(null=True)
    item_name = TextField(null=True)
    wear_name = TextField(null=True)
    float_value = DoubleField(null=True)
    created_at = TextField(null=True)
    screenshot_url = TextField(null=True)
    image_url = TextField(null=True)
    inspect_link = TextField(null=True)
    seller_description = TextField(null=True)
    raw_json = TextField()
    last_seen_at = DateTimeField(default=utc_now)

    class Meta:
        table_name = "current_listings"


class ItemChange(BaseModel):
    id = AutoField()
    listing_id = CharField(index=True)
    change_type = CharField()
    field_name = CharField()
    old_value = TextField(null=True)
    new_value = TextField(null=True)
    observed_at = DateTimeField(default=utc_now, index=True)
    poll_id = IntegerField(null=True, index=True)

    class Meta:
        table_name = "item_changes"


class Setting(BaseModel):
    key = CharField(primary_key=True)
    value = TextField()
    updated_at = DateTimeField(default=utc_now)

    class Meta:
        table_name = "settings"


class PinWatchState(BaseModel):
    def_index = IntegerField(primary_key=True)
    market_hash_name = TextField(null=True)
    status = CharField(default="active", index=True)
    best_listing_price = IntegerField(null=True)
    best_sale_price = IntegerField(null=True)
    best_known_price = IntegerField(null=True)
    last_alert_listing_id = CharField(null=True)
    last_alert_price = IntegerField(null=True)
    last_sale_listing_id = CharField(null=True)
    last_sale_price = IntegerField(null=True)
    last_sale_sold_at = TextField(null=True)
    purchased_listing_id = CharField(null=True)
    created_at = DateTimeField(default=utc_now)
    updated_at = DateTimeField(default=utc_now)

    class Meta:
        table_name = "pin_watch_states"


class PinRecentSale(BaseModel):
    id = AutoField()
    def_index = IntegerField(index=True)
    market_hash_name = TextField(null=True)
    sale_price = IntegerField()
    sold_at = TextField(null=True)
    listing_id = CharField(null=True)
    recorded_at = DateTimeField(default=utc_now)

    class Meta:
        table_name = "pin_recent_sales"


class PinCallbackAction(BaseModel):
    action_id = CharField(primary_key=True)
    def_index = IntegerField(index=True)
    listing_id = CharField()
    listing_price = IntegerField()
    listing_url = TextField(null=True)
    status = CharField(default="pending", index=True)
    created_at = DateTimeField(default=utc_now)
    updated_at = DateTimeField(default=utc_now)

    class Meta:
        table_name = "pin_callback_actions"


def initialize_database(database_url_or_path: str) -> Database:
    db = _build_database(database_url_or_path)
    db_proxy.initialize(db)
    try:
        db.connect(reuse_if_open=True)
    except OperationalError as exc:
        message = str(exc)
        if "could not translate host name" in message and ".supabase.co" in message:
            raise RuntimeError(
                "Supabase DB host could not be resolved from this network. "
                "Use the exact IPv4 pooler URI from Supabase Dashboard > Connect "
                "(not the direct db.<project-ref>.supabase.co host if your network has no IPv6)."
            ) from exc
        if "no route to host" in message and ".supabase.co" in message:
            raise RuntimeError(
                "Supabase direct DB host appears unreachable from this network (often IPv6 routing). "
                "Use the Supabase IPv4 pooler URI from Dashboard > Connect."
            ) from exc
        raise
    return db


def get_database() -> Database:
    db = getattr(db_proxy, "obj", None)
    if db is None:
        raise RuntimeError("Database is not initialized")
    return db


def _build_database(database_url_or_path: str) -> Database:
    target = (database_url_or_path or "").strip()
    if target.lower().startswith("postgresql://") or target.lower().startswith("postgres://"):
        return _build_postgres_database(target)

    db_path = Path(target or "./data/monitor.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteDatabase(
        db_path,
        pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
            "cache_size": -64 * 1000,
        },
    )


def _build_postgres_database(database_url: str) -> PostgresqlDatabase:
    parsed = urlparse(database_url)
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("DATABASE_URL is missing database name")

    query_params = _parse_query(parsed.query)
    sslmode = query_params.get("sslmode", "require")

    return PostgresqlDatabase(
        database=database,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        sslmode=sslmode,
    )


def _parse_query(query: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in (query or "").split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        result[unquote(key)] = unquote(value)
    return result


def _patch_peeweedbevolve_sqlite_support() -> None:
    if getattr(peeweedbevolve, "_csfloat_sqlite_patch_applied", False):
        return

    original_get_columns = peeweedbevolve.get_columns_by_table
    original_get_foreign_keys = peeweedbevolve.get_foreign_keys_by_table

    def patched_get_columns_by_table(db, schema=None):  # noqa: ANN001
        if peeweedbevolve.is_sqlite(db):
            columns_by_table = collections.defaultdict(list)
            tables = db.get_tables(schema=schema) if schema else db.get_tables()
            for table in tables:
                safe_table_name = table.replace('"', '""')
                cursor = db.execute_sql(f'PRAGMA table_info("{safe_table_name}")')
                for row in cursor.fetchall():
                    # cid, name, type, notnull, dflt_value, pk
                    column = peeweedbevolve.ColumnMetadata(
                        row[1],
                        peeweedbevolve.normalize_column_type(row[2] or ""),
                        not bool(row[3]),
                        bool(row[5]),
                        table,
                        row[4],
                        None,
                        None,
                        None,
                    )
                    columns_by_table[table].append(column)
            return columns_by_table
        return original_get_columns(db, schema=schema)

    def patched_get_foreign_keys_by_table(db, schema=None):  # noqa: ANN001
        if peeweedbevolve.is_sqlite(db):
            fks_by_table = collections.defaultdict(list)
            tables = db.get_tables(schema=schema) if schema else db.get_tables()
            for table in tables:
                safe_table_name = table.replace('"', '""')
                cursor = db.execute_sql(f'PRAGMA foreign_key_list("{safe_table_name}")')
                for row in cursor.fetchall():
                    # id, seq, table, from, to, on_update, on_delete, match
                    name = f"fk_{table}_{row[3]}_{row[2]}_{row[4]}"
                    fk = peeweedbevolve.ForeignKeyMetadata(row[3], row[2], row[4], table, name)
                    fks_by_table[table].append(fk)
            return fks_by_table
        return original_get_foreign_keys(db, schema=schema)

    peeweedbevolve.get_columns_by_table = patched_get_columns_by_table
    peeweedbevolve.get_foreign_keys_by_table = patched_get_foreign_keys_by_table
    peeweedbevolve._csfloat_sqlite_patch_applied = True


def run_unattended_migrations() -> None:
    db = get_database()
    try:
        _patch_peeweedbevolve_sqlite_support()
        db.create_tables(
            [
                PollRun,
                CurrentListing,
                ItemChange,
                Setting,
                PinWatchState,
                PinRecentSale,
                PinCallbackAction,
            ],
            safe=True,
        )
        db.evolve(interactive=False)
    except Exception as exc:
        # peewee-db-evolve does not support altering SQLite column types.
        # Fallback to safe table creation so startup stays unattended.
        if peeweedbevolve.is_sqlite(db) and "change a column type" in str(exc).lower():
            db.create_tables(
                [
                    PollRun,
                    CurrentListing,
                    ItemChange,
                    Setting,
                    PinWatchState,
                    PinRecentSale,
                    PinCallbackAction,
                ],
                safe=True,
            )
            return
        raise RuntimeError(f"Automatic migrations failed: {exc}") from exc
