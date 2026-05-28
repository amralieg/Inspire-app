# Databricks notebook source
# MAGIC %md
# MAGIC # Inspire AI — Step 1: Generate demo data (LLM code + execution)
# MAGIC
# MAGIC Genie-style flow: FM returns JSON (`display_name`, `tables`, `table_metadata`, `code`), then `exec(code)`.
# MAGIC Writes **5 Delta tables** with UC **table & column comments**, then passes FQNs to Inspire via `uc_metadata`.

# COMMAND ----------

NUM_DEMO_TABLES = 5

dbutils.widgets.text("user_description", "", "1. Describe the demo data you want")
dbutils.widgets.text("inspire_catalog", "", "2. UC catalog (must already exist)")
dbutils.widgets.text("inspire_database", "", "3. Inspire tracking: catalog._inspire")
dbutils.widgets.text("session_id", "", "4. Session ID")
dbutils.widgets.text("business_name", "Demo", "5. Business display name (fallback display_name)")
dbutils.widgets.text("demo_schema", "", "6. Demo schema name (under inspire_catalog)")
dbutils.widgets.text("warehouse_id", "", "7. SQL warehouse (SP CAN_USE)")
dbutils.widgets.text("app_sp_application_id", "", "8. Inspire App service principal applicationId")

user_description = (dbutils.widgets.get("user_description") or "").strip()
inspire_catalog = (dbutils.widgets.get("inspire_catalog") or "").strip()
inspire_database = (dbutils.widgets.get("inspire_database") or "").strip()
session_id = (dbutils.widgets.get("session_id") or "").strip()
business_name = (dbutils.widgets.get("business_name") or "Demo").strip()
demo_schema = (dbutils.widgets.get("demo_schema") or "").strip()
warehouse_id = (dbutils.widgets.get("warehouse_id") or "").strip()
app_sp_application_id = (dbutils.widgets.get("app_sp_application_id") or "").strip()

if not user_description:
    raise ValueError("user_description is required")
if not inspire_catalog:
    raise ValueError("inspire_catalog is required (existing UC catalog with storage)")
if not inspire_database or "." not in inspire_database:
    raise ValueError("inspire_database must be catalog._inspire")
if not session_id:
    raise ValueError("session_id is required")
if not demo_schema:
    raise ValueError("demo_schema is required")

track_catalog, track_schema = inspire_database.split(".", 1)
demo_schema_fq = f"{inspire_catalog}.{demo_schema}"

print(f"My user intent: {user_description}")
print(f"Target catalog.schema: {demo_schema_fq} ({NUM_DEMO_TABLES} tables + UC comments)")

# COMMAND ----------

def _current_uc_principal():
    try:
        row = spark.sql("SELECT current_user() AS u").collect()[0]
        return (row["u"] if hasattr(row, "__getitem__") else row.u).strip()
    except Exception:
        return None


def _run_grant_sql(sql, label=None):
    try:
        spark.sql(sql)
        if label:
            print(f"  ✓ {label}")
        return True
    except Exception as e:
        print(f"  grant note{f' ({label})' if label else ''}: {e}")
        return False


def _grant_schema_access(catalog, schema, principal):
    if not principal:
        print(f"Skip grants for {catalog}.{schema}: no principal")
        return
    fq_schema = f"`{catalog}`.`{schema}`"
    fq_catalog = f"`{catalog}`"
    statements = [
        (f"GRANT USE CATALOG ON CATALOG {fq_catalog} TO `{principal}`", "USE CATALOG"),
        (f"GRANT CREATE SCHEMA ON CATALOG {fq_catalog} TO `{principal}`", "CREATE SCHEMA on catalog"),
        (f"GRANT USE SCHEMA ON SCHEMA {fq_schema} TO `{principal}`", "USE SCHEMA"),
        (f"GRANT CREATE TABLE ON SCHEMA {fq_schema} TO `{principal}`", "CREATE TABLE"),
        (f"GRANT SELECT ON SCHEMA {fq_schema} TO `{principal}`", "SELECT"),
        (f"GRANT MODIFY ON SCHEMA {fq_schema} TO `{principal}`", "MODIFY"),
    ]
    for sql, label in statements:
        _run_grant_sql(sql, label)


def _resolve_app_sp_application_id(explicit_id):
    sp = (explicit_id or "").strip()
    if sp:
        return sp
    try:
        import requests

        host, token = _notebook_api_context()
        if not host or not token:
            return None
        url = f"{host}/api/2.0/preview/scim/v2/ServicePrincipals?filter=displayName co \"inspire-ai\"&count=50"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if resp.status_code >= 400:
            return None
        for row in resp.json().get("Resources", []):
            app_id = (row.get("applicationId") or "").strip()
            if app_id:
                print(f"Resolved inspire-ai SP: {row.get('displayName')} ({app_id[:8]}…)")
                return app_id
    except Exception as e:
        print(f"Could not resolve inspire-ai SP: {e}")
    return None


def _grant_warehouse_can_use(sp_app_id, wh_id):
    if not sp_app_id or not wh_id:
        return
    try:
        import requests

        host, token = _notebook_api_context()
        url = f"{host}/api/2.0/permissions/sql/warehouses/{wh_id}"
        resp = requests.patch(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "access_control_list": [
                    {"service_principal_name": sp_app_id, "permission_level": "CAN_USE"}
                ]
            },
            timeout=60,
        )
        if resp.status_code == 200:
            print(f"  ✓ CAN_USE on warehouse {wh_id}")
        else:
            print(f"  warehouse grant note: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  warehouse grant note: {e}")


def _grant_inspire_app_service_principal(sp_app_id, catalog, track_schema, demo_schema, warehouse_id):
    """
    Mirror installer_workspace.py Step 4 so the Databricks App SP can browse UC metadata,
    write session tables, and read demo data after this pipeline completes.
    """
    if not sp_app_id:
        raise RuntimeError(
            "app_sp_application_id is required. Run installer_workspace.py once (deploys inspire-ai App + SP) "
            "or set DATABRICKS_CLIENT_ID / SP_CLIENT_ID in the app environment."
        )

    print(f"Granting Inspire App service principal ({sp_app_id[:8]}…) …")
    fq_cat = f"`{catalog}`"
    fq_track = f"`{catalog}`.`{track_schema}`"
    fq_demo = f"`{catalog}`.`{demo_schema}`"

    core_grants = [
        (f"GRANT USE CATALOG ON CATALOG {fq_cat} TO `{sp_app_id}`", "SP USE CATALOG"),
        (f"GRANT BROWSE ON CATALOG {fq_cat} TO `{sp_app_id}`", "SP BROWSE catalog"),
        (f"GRANT CREATE SCHEMA ON CATALOG {fq_cat} TO `{sp_app_id}`", "SP CREATE SCHEMA on catalog"),
        (f"GRANT USE SCHEMA ON SCHEMA {fq_track} TO `{sp_app_id}`", "SP USE _inspire schema"),
        (f"GRANT CREATE TABLE ON SCHEMA {fq_track} TO `{sp_app_id}`", "SP CREATE in _inspire"),
        (f"GRANT SELECT ON SCHEMA {fq_track} TO `{sp_app_id}`", "SP SELECT _inspire"),
        (f"GRANT MODIFY ON SCHEMA {fq_track} TO `{sp_app_id}`", "SP MODIFY _inspire"),
        (f"GRANT USE SCHEMA ON SCHEMA {fq_demo} TO `{sp_app_id}`", "SP USE demo schema"),
        (f"GRANT CREATE TABLE ON SCHEMA {fq_demo} TO `{sp_app_id}`", "SP CREATE demo schema"),
        (f"GRANT SELECT ON SCHEMA {fq_demo} TO `{sp_app_id}`", "SP SELECT demo schema"),
        (f"GRANT MODIFY ON SCHEMA {fq_demo} TO `{sp_app_id}`", "SP MODIFY demo schema"),
    ]
    for sql, label in core_grants:
        _run_grant_sql(sql, label)

    _skip_catalogs = {"samples", "system", "__databricks_internal", "information_schema"}
    try:
        other_catalogs = [
            r.catalog
            for r in spark.sql("SHOW CATALOGS").collect()
            if r.catalog and r.catalog not in _skip_catalogs and r.catalog != catalog
        ]
        for cat in other_catalogs[:40]:
            _run_grant_sql(f"GRANT USE CATALOG ON CATALOG `{cat}` TO `{sp_app_id}`")
            _run_grant_sql(f"GRANT BROWSE ON CATALOG `{cat}` TO `{sp_app_id}`")
    except Exception as e:
        print(f"  browse-other-catalogs note: {e}")

    _grant_warehouse_can_use(sp_app_id, warehouse_id)
    print("Service principal grants complete.")


def _ensure_catalog_exists(catalog):
    try:
        spark.sql(f"DESCRIBE CATALOG `{catalog}`").collect()
        print(f"✓ Catalog exists: {catalog}")
    except Exception as e:
        raise RuntimeError(
            f"UC catalog `{catalog}` is not usable. Pick a catalog that already exists in this workspace "
            f"(e.g. your personal catalog from INSPIRE_DATABASE). Original error: {e}"
        ) from e


_runner = _current_uc_principal()

_ensure_catalog_exists(inspire_catalog)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{track_catalog}`.`{track_schema}`")
_grant_schema_access(track_catalog, track_schema, _runner)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{inspire_catalog}`.`{demo_schema}`")
_grant_schema_access(inspire_catalog, demo_schema, _runner)
spark.sql(f"USE CATALOG `{inspire_catalog}`")
spark.sql(f"USE SCHEMA `{demo_schema}`")
print(f"Ensured schemas: {inspire_database} and {demo_schema_fq}")

# COMMAND ----------

import json
import re


def _notebook_api_context():
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    host = (ctx.apiUrl().get() or "").rstrip("/")
    token = ctx.apiToken().get() or ""
    return host, token


def _sql_escape_comment(text):
    return str(text or "").replace("'", "''")


def _build_genie_prompt(user_intent, catalog_name, schema_name, num_tables):
    table_examples = [
        f'"{catalog_name}.{schema_name}.demo_topic_table_{i}"' for i in range(1, num_tables + 1)
    ]
    tables_json = ",\n    ".join(table_examples)
    table_nums = ", ".join(str(i) for i in range(1, num_tables + 1))

    return f"""
You are a Python code generator that produces realistic fake datasets for analytics demos.

The user will provide a query (e.g., "generate fake retail sales data" or "create fake airline bookings").

From the query, derive a short topic slug (lowercase, underscores only) for table names, e.g. `qatar_airways` → table base `qatar_airways_table`.

**Fixed Unity Catalog location (MANDATORY — do not change):**
- catalog_name = "{catalog_name}"  (already exists — NEVER create a catalog)
- schema_name = "{schema_name}"
- table base name = <topic>_table
- Create exactly {num_tables} related business tables named:
  {{catalog_name}}.{{schema_name}}.<topic>_table_1 … _{num_tables}

Additionally:
- display_name: exactly TWO capitalized words from the query (e.g. "Qatar Airways", "Retail Sales").

OUTPUT FORMAT (STRICT) — return EXACTLY ONE JSON object:
{{
  "display_name": "<Two Word Name>",
  "tables": [
    {tables_json}
  ],
  "table_metadata": [
    {{
      "fqn": "{catalog_name}.{schema_name}.<topic>_table_1",
      "description": "<1-2 sentence business description of this table visible in Unity Catalog>",
      "columns": {{
        "<column_name>": "<column description for Unity Catalog>",
        "...": "..."
      }}
    }},
    ... repeat for all {num_tables} tables (one object per table, same order as tables array)
  ],
  "code": "<ENTIRE PYTHON CODE AS A SINGLE JSON STRING>"
}}

Rules:
- No prose before or after the JSON.
- "tables" must list exactly {num_tables} three-part FQNs under {catalog_name}.{schema_name}.
- "table_metadata" must have exactly {num_tables} entries; each "columns" map must include EVERY column written to that table with a meaningful UC comment.
- "code" must be valid Python only (JSON-escaped).

Code requirements (inside "code"):
- import pandas, numpy, random; from datetime import datetime, timedelta
- catalog_name = "{catalog_name}"; schema_name = "{schema_name}"; table_base = "<topic>_table"
- Do NOT call CREATE CATALOG.
- spark.sql(f'CREATE SCHEMA IF NOT EXISTS {{catalog_name}}.{{schema_name}}')
- spark.sql(f'USE CATALOG {{catalog_name}}'); spark.sql(f'USE {{catalog_name}}.{{schema_name}}')
- Build {num_tables} related pandas DataFrames (realistic domain data; ~80–200 rows on larger fact tables, fewer on dimensions).
- Write each as Delta overwrite: saveAsTable(f"{{catalog_name}}.{{schema_name}}.{{table_base}}_N") for N in ({table_nums}).
- Do NOT run COMMENT statements in code (comments are applied from table_metadata after execution).

User query to process: {user_intent}
"""


def _call_foundation_model(user_intent, catalog_name, schema_name):
    host, token = _notebook_api_context()
    if not host or not token:
        raise RuntimeError("Could not resolve Databricks workspace host/token from notebook context")

    import requests

    prompt = _build_genie_prompt(user_intent, catalog_name, schema_name, NUM_DEMO_TABLES)
    url = f"{host}/serving-endpoints/chat/completions"
    payload = {
        "model": "databricks-claude-sonnet-4",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 65536,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=300,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Foundation model error {resp.status_code}: {resp.text[:800]}")

    data = resp.json()
    raw = (
        (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    ).strip()
    if not raw:
        raise RuntimeError("Foundation model returned empty content")

    print(raw)

    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()

    return json.loads(raw)


def _assert_tables_in_target_schema(table_fqns, catalog_name, schema_name, expected_count):
    prefix = f"{catalog_name}.{schema_name}."
    out = []
    for fq in table_fqns:
        if not fq.startswith(prefix):
            raise RuntimeError(
                f"LLM returned table outside target schema: {fq} (expected prefix {prefix})"
            )
        out.append(fq)
    if len(out) != expected_count:
        raise RuntimeError(f"Expected exactly {expected_count} tables; got: {table_fqns!r}")
    return out


def _strip_create_catalog_from_code(code_str):
    lines = []
    for line in code_str.splitlines():
        if re.search(r"CREATE\s+CATALOG\b", line, re.I):
            print(f"  (stripped) {line.strip()}")
            continue
        lines.append(line)
    return "\n".join(lines)


def _apply_uc_table_metadata(table_metadata, expected_fqns):
    """Apply Unity Catalog table + column comments (visible in Catalog Explorer)."""
    if not table_metadata:
        raise RuntimeError("LLM response missing table_metadata (required for UC descriptions)")

    by_fqn = {}
    for entry in table_metadata:
        fqn = (entry.get("fqn") or "").strip()
        if fqn:
            by_fqn[fqn] = entry

    for fqn in expected_fqns:
        entry = by_fqn.get(fqn)
        if not entry:
            raise RuntimeError(f"table_metadata missing entry for {fqn}")

        parts = fqn.split(".")
        if len(parts) != 3:
            raise RuntimeError(f"Invalid FQN in table_metadata: {fqn}")
        catalog, schema, table = parts

        table_desc = (entry.get("description") or entry.get("table_description") or "").strip()
        if not table_desc:
            raise RuntimeError(f"table_metadata[{fqn}] missing description")

        spark.sql(
            f"COMMENT ON TABLE `{catalog}`.`{schema}`.`{table}` IS '{_sql_escape_comment(table_desc)}'"
        )
        print(f"  📝 table comment: {fqn}")

        columns = entry.get("columns") or {}
        if not columns:
            raise RuntimeError(f"table_metadata[{fqn}] missing columns map")

        for col_name, col_desc in columns.items():
            col_desc = str(col_desc or "").strip()
            if not col_desc:
                continue
            spark.sql(
                f"ALTER TABLE `{catalog}`.`{schema}`.`{table}` "
                f"ALTER COLUMN `{col_name}` COMMENT '{_sql_escape_comment(col_desc)}'"
            )
        print(f"     → {len(columns)} column comments")


def _read_table_comment(catalog, schema, table):
    rows = spark.sql(f"DESCRIBE TABLE EXTENDED `{catalog}`.`{schema}`.`{table}`").collect()
    for row in rows:
        key = row[0] if not hasattr(row, "col_name") else row.col_name
        if str(key).lower() == "comment":
            val = row[1] if len(row) > 1 else getattr(row, "data_type", None)
            return (val or "").strip()
    return ""


def _count_column_comments(catalog, schema, table):
    rows = spark.sql(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`").collect()
    commented = 0
    total = 0
    for row in rows:
        col = row.col_name if hasattr(row, "col_name") else row[0]
        if not col or str(col).startswith("#"):
            continue
        total += 1
        comment = (row.comment if hasattr(row, "comment") else (row[2] if len(row) > 2 else "")) or ""
        if str(comment).strip():
            commented += 1
    return commented, total


def _verify_tables_for_inspire(table_fqns):
    verified = []
    for fq in table_fqns:
        catalog, schema, table = [p.strip() for p in fq.split(".")]
        try:
            cols = spark.sql(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`").collect()
        except Exception as e:
            raise RuntimeError(f"Table not readable after code execution: {fq} — {e}") from e
        if not cols:
            raise RuntimeError(f"Table has no columns: {fq}")

        row_count = spark.sql(f"SELECT COUNT(*) AS c FROM `{catalog}`.`{schema}`.`{table}`").collect()[0][0]
        tbl_comment = _read_table_comment(catalog, schema, table)
        col_commented, col_total = _count_column_comments(catalog, schema, table)

        if not tbl_comment:
            raise RuntimeError(f"Unity Catalog table description missing on {fq}")
        if col_total > 0 and col_commented < max(1, col_total // 2):
            raise RuntimeError(
                f"Too few column comments on {fq} ({col_commented}/{col_total}). "
                "Unity Catalog column descriptions are required for Inspire metadata."
            )

        print(
            f"  ✓ {fq} — {col_total} columns ({col_commented} commented), "
            f"{row_count} rows, table description set"
        )
        verified.append(fq)
    return verified


# ── 1) LLM: generate code + metadata ──
parsed = _call_foundation_model(user_description, inspire_catalog, demo_schema)

display_name = (parsed.get("display_name") or business_name or "Demo Data").strip()
tables = _assert_tables_in_target_schema(
    parsed.get("tables") or [], inspire_catalog, demo_schema, NUM_DEMO_TABLES
)
table_metadata = parsed.get("table_metadata") or []
code_str = _strip_create_catalog_from_code(parsed.get("code") or "")

if not code_str.strip():
    raise RuntimeError("LLM response missing 'code'")

# ── 2) Execute generated Python (creates 5 Delta tables) ──
try:
    exec(code_str, {"spark": spark})
except Exception as e:
    raise RuntimeError(f"Generated code execution failed: {e}") from e

# ── 3) Apply UC table + column descriptions ──
print("Applying Unity Catalog table and column comments…")
_apply_uc_table_metadata(table_metadata, tables)

# ── 4) Verify tables, comments, build uc_metadata for Inspire ──
tables = _verify_tables_for_inspire(tables)
uc_metadata = ",".join(tables)

print(f"display_name: {display_name}")
print(f"uc_metadata ({len(tables)} tables): {uc_metadata}")

# ── 5) Grant Inspire App service principal (installer_workspace parity) ──
_sp_id = _resolve_app_sp_application_id(app_sp_application_id)
_grant_inspire_app_service_principal(
    _sp_id,
    inspire_catalog,
    track_schema,
    demo_schema,
    warehouse_id,
)

# COMMAND ----------


def _set_task_value(key, value):
    dbutils.jobs.taskValues.set(key=key, value=str(value) if value is not None else "")


_set_task_value("uc_metadata", uc_metadata)
_set_task_value("display_name", display_name)
_set_task_value("business_name", business_name)
_set_task_value("catalog", inspire_catalog)
_set_task_value("schema", demo_schema)
_set_task_value("demo_catalog", inspire_catalog)
_set_task_value("demo_schema", demo_schema_fq)
_set_task_value("inspire_database", inspire_database)
_set_task_value("session_id", session_id)
_set_task_value("user_description", user_description)
_set_task_value("table_count", len(tables))
_set_task_value("data_source", "llm_genie_generator")

for i, t in enumerate(tables):
    _set_task_value(f"table_{i}", t)

print(f"Step 1 complete — {len(tables)} Delta tables with UC descriptions ready for Inspire.")
