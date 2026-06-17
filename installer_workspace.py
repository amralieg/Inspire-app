# Databricks notebook source
# MAGIC %md
# MAGIC # Inspire AI v8.8 — Workspace Installer
# MAGIC
# MAGIC **Run All** — pick **SQL warehouse** then **Inspire catalog** via the widgets (no `%pip`). Uses **Databricks SDK** (preinstalled on DBR / Serverless).
# MAGIC
# MAGIC **What it does:** clean install → unpacks **`/Workspace/Users/<you>/InspireAI-workspace.zip`** if present (produced by **`npm run deploy`** / `deploy:inspire`), else uses an existing workspace folder under **`InspireAI`**, **`inspire-ai`**, **`InspireAI-*`**, or **`/Workspace/Shared/InspireAI`** (must contain **`app.yaml`**) → publishes **`dbx_inspire_ai_agent.ipynb`** to **`/Shared/inspire-ai/`** → writes **`app.yaml`** → creates/deploys Databricks App **`inspire-ai`** → grants the app **service principal** full rights on **`{catalog}._inspire`**, **USE_CATALOG** + **BROWSE** on other catalogs (metadata only), and **CAN_USE** on the warehouse.
# MAGIC
# MAGIC **Where you use the product:** **Compute → Apps → inspire-ai** (or the link in the last cell) — not this notebook.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup

# COMMAND ----------

# DBTITLE 1,Setup guidance
# MAGIC %md
# MAGIC ### SQL warehouse and Unity Catalog
# MAGIC The notebook now uses a single `main()` entry point in the last cell.
# MAGIC
# MAGIC 1. **`00_sql_warehouse`** — SQL warehouse used for `CREATE SCHEMA`, SQL grants, and the deployed app’s **`INSPIRE_WAREHOUSE_ID`** default.
# MAGIC 2. **`01_inspire_catalog`** — catalog where Inspire creates the **`_inspire`** schema (session / tracking tables).
# MAGIC
# MAGIC **First run:** run the last cell once to load the dropdown choices. If either value is still **— Please select —**, the notebook shows a warning banner and stops cleanly. Pick both values, then **re-run the last cell** (or Run All).
# MAGIC
# MAGIC **Jobs** (`npm run deploy`): set **`WAREHOUSE_OVERRIDE`** (warehouse id) and/or **`CATALOG_OVERRIDE`** in code to skip the matching widget. Values must exist in this workspace.

# COMMAND ----------

# DBTITLE 1,Widget helpers
# Widget helpers used by main() in the last cell.
_WIDGET_PLACEHOLDER = "— Please select —"
W_WH = "00_sql_warehouse"
W_CAT = "01_inspire_catalog"


def _get_widget_value(name):
    try:
        return (dbutils.widgets.get(name) or "").strip()
    except Exception:
        return ""



def _reset_dropdown(name, label, choices, current_value):
    try:
        dbutils.widgets.remove(name)
    except Exception:
        pass

    default_value = current_value if current_value in choices else _WIDGET_PLACEHOLDER
    dbutils.widgets.dropdown(name, default_value, choices, label)



def show_warning_banner(title, message):
    import html

    print(f"⚠️ {title}")
    print(message)

    safe_title = html.escape(title)
    safe_message = html.escape(message).replace("\n", "<br>")
    displayHTML(
        f"""
        <div style="padding:16px 20px;border-radius:12px;background:#fff4e5;border:1px solid #f5c26b;color:#7a4b00;margin:12px 0;">
          <div style="display:flex;align-items:flex-start;gap:12px;">
            <div style="font-size:28px;line-height:1;">⚠️</div>
            <div>
              <div style="font-size:18px;font-weight:700;margin-bottom:6px;">{safe_title}</div>
              <div style="font-size:14px;line-height:1.5;">{safe_message}</div>
            </div>
          </div>
        </div>
        """
    )



def ensure_parameter_widgets(warehouse_choices=None, catalog_choices=None):
    warehouse_choices = warehouse_choices or [_WIDGET_PLACEHOLDER]
    catalog_choices = catalog_choices or [_WIDGET_PLACEHOLDER]

    _reset_dropdown(
        W_WH,
        "1) SQL warehouse for grants & app default",
        warehouse_choices,
        _get_widget_value(W_WH),
    )
    _reset_dropdown(
        W_CAT,
        "2) Unity Catalog for Inspire tracking tables (_inspire)",
        catalog_choices,
        _get_widget_value(W_CAT),
    )

    print("Notebook parameters are ready above.")
    print("Run the last cell to continue after choosing values.")

_existing_wh = ""
_existing_cat = ""
try:
    _existing_wh = (dbutils.widgets.get(W_WH) or "").strip()
except Exception:
    pass
try:
    _existing_cat = (dbutils.widgets.get(W_CAT) or "").strip()
except Exception:
    pass

_already_configured = (
    _existing_wh
    and _existing_wh != _WIDGET_PLACEHOLDER
    and _existing_cat
    and _existing_cat != _WIDGET_PLACEHOLDER
)

if _already_configured:
    print("Parameters already set above. Change them if needed, then re-run the setup cell.")
else:
    for _w in (W_WH, W_CAT):
        try:
            dbutils.widgets.remove(_w)
        except Exception:
            pass

    dbutils.widgets.dropdown(
        W_WH,
        _WIDGET_PLACEHOLDER,
        [_WIDGET_PLACEHOLDER],
        "1) SQL warehouse for grants & app default",
    )
    dbutils.widgets.dropdown(
        W_CAT,
        _WIDGET_PLACEHOLDER,
        [_WIDGET_PLACEHOLDER],
        "2) Unity Catalog for Inspire tracking tables (_inspire)",
    )

    print("Notebook parameters are ready above (empty until you choose values).")
    print("Run the next cell to load choices. If it fails, select both dropdowns and re-run that cell.")

# COMMAND ----------

# DBTITLE 1,Installer helpers
import base64, glob, os, requests, shutil, time, zipfile
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState




def _wh_sort_key(wh):
    st = wh.state.value if wh.state else ""
    srv = bool(getattr(wh, "enable_serverless_compute", False))
    return (0 if st == "RUNNING" and srv else 1 if st == "RUNNING" else 2, (wh.name or "").lower())



def _yaml_quote(value):
    if value is None:
        return '""'
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'



def write_app_yaml_deploy(path, inspire_db, warehouse_id, notebook_path, sp_resource_name):
    """Emit app.yaml without PyYAML (no extra pip on the cluster)."""
    lines = [
        "command:",
        '  - "bash"',
        '  - "start.sh"',
        "",
        "env:",
        '  - name: "NODE_ENV"',
        '    value: "production"',
        '  - name: "INSPIRE_DATABASE"',
        f"    value: {_yaml_quote(inspire_db)}",
        '  - name: "INSPIRE_AUTO_SETUP"',
        '    value: "true"',
    ]
    if warehouse_id:
        lines += ["  - name: \"INSPIRE_WAREHOUSE_ID\"", f"    value: {_yaml_quote(warehouse_id)}"]
    if notebook_path:
        lines += ["  - name: \"NOTEBOOK_PATH\"", f"    value: {_yaml_quote(notebook_path)}"]
    lines += [
        "",
        "resources:",
        f"  - name: {_yaml_quote(sp_resource_name)}",
        '    type: "service-principal"',
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")



def export_workspace_b64(ctx, source_path, export_formats=("JUPYTER",)):
    """Export workspace notebook/file as base64 and fall back to local disk when needed."""
    api = ctx["api"]
    path = str(source_path or "").strip()
    if not path:
        return None

    export_paths = []
    stripped = path.replace("/Workspace", "", 1) if path.startswith("/Workspace") else path
    for export_path in (stripped, path):
        if export_path and export_path not in export_paths:
            export_paths.append(export_path)

    for export_path in export_paths:
        for fmt in export_formats:
            try:
                resp = api(
                    "GET",
                    f"/api/2.0/workspace/export?path={requests.utils.quote(export_path)}&format={fmt}",
                )
                if resp.status_code == 200:
                    content = (resp.json() or {}).get("content") or ""
                    if content:
                        print(f"  Exported {export_path} ({fmt}): {len(content)} bytes base64")
                        return content
            except Exception as exc:
                print(f"  export note {export_path} ({fmt}): {exc}")

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        b64 = base64.b64encode(raw).decode("utf-8")
        print(f"  Read from disk {path}: {len(b64)} bytes base64")
        return b64
    except OSError as exc:
        print(f"  ⚠️ Cannot read {path}: {exc}")
        return None



def build_runtime_context():
    w = WorkspaceClient()
    current_user = w.current_user.me()
    user_email = current_user.user_name
    workspace_host = w.config.host

    warehouse_override = None  # Set to a warehouse UUID string for Jobs/CI to skip the dropdown
    catalog_override = None  # Set to a catalog name for Jobs/CI to skip the catalog dropdown

    all_warehouses = [wh for wh in w.warehouses.list() if getattr(wh, "id", None)]
    all_warehouses.sort(key=_wh_sort_key)

    warehouse_labels = []
    warehouse_ids_list = []
    for wh in all_warehouses:
        st = wh.state.value if wh.state else "?"
        nm = wh.name or wh.id
        warehouse_labels.append(f"{nm} [{st}] {wh.id}")
        warehouse_ids_list.append(wh.id)

    if not warehouse_ids_list:
        raise RuntimeError("No SQL warehouses found. Create a SQL warehouse in this workspace, then re-run.")

    available_catalogs = []
    try:
        for cat in w.catalogs.list():
            if cat.name not in ("system", "information_schema", "__databricks_internal"):
                available_catalogs.append(cat.name)
    except Exception:
        available_catalogs = ["workspace"]
    if not available_catalogs:
        available_catalogs = ["workspace"]
    available_catalogs = sorted(set(available_catalogs))

    ensure_parameter_widgets(
        warehouse_choices=[_WIDGET_PLACEHOLDER] + warehouse_labels,
        catalog_choices=[_WIDGET_PLACEHOLDER] + available_catalogs,
    )

    picked_wh = _get_widget_value(W_WH)
    picked_cat = _get_widget_value(W_CAT)
    missing_messages = []

    warehouse_id = None
    warehouse_name = None
    if warehouse_override:
        if warehouse_override not in warehouse_ids_list:
            raise ValueError(
                f"WAREHOUSE_OVERRIDE={warehouse_override!r} not in warehouse ids (showing first 5): {warehouse_ids_list[:5]}"
            )
        warehouse_id = warehouse_override
        warehouse_name = warehouse_id
        for wh in all_warehouses:
            if wh.id == warehouse_id:
                warehouse_name = wh.name or warehouse_id
                break
    else:
        if not picked_wh or picked_wh == _WIDGET_PLACEHOLDER:
            missing_messages.append(f"Pick a value for {W_WH}.")
        elif picked_wh not in warehouse_labels:
            missing_messages.append(f"Pick a valid value for {W_WH}.")
        else:
            wh_idx = warehouse_labels.index(picked_wh)
            warehouse_id = warehouse_ids_list[wh_idx]
            warehouse_name = all_warehouses[wh_idx].name or warehouse_id

    catalog = None
    if catalog_override:
        if catalog_override not in available_catalogs:
            raise ValueError(f"CATALOG_OVERRIDE={catalog_override!r} not in catalogs: {available_catalogs}")
        catalog = catalog_override
    else:
        if not picked_cat or picked_cat == _WIDGET_PLACEHOLDER:
            missing_messages.append(f"Pick a value for {W_CAT}.")
        elif picked_cat not in available_catalogs:
            missing_messages.append(f"Pick a valid value for {W_CAT}.")
        else:
            catalog = picked_cat

    if missing_messages:
        show_warning_banner(
            "Choose notebook parameter values first",
            "Select both dropdown values at the top of the notebook, then rerun the last main() cell.\n\n" + "\n".join(missing_messages),
        )
        return {"ready": False}

    schema = "_inspire"
    inspire_db = f"{catalog}.{schema}"
    app_name = "inspire-ai"
    sp_name = f"{app_name}-sp"

    print(f"Selected warehouse: {warehouse_name} ({warehouse_id})")
    print(f"Selected catalog:   {catalog}  →  {inspire_db}")

    auto_zip = f"/Workspace/Users/{user_email}/InspireAI-workspace.zip"
    source_zip = auto_zip if os.path.exists(auto_zip) else ""
    if source_zip:
        print(f"Using bundle zip: {source_zip}")

    source_folder = None
    if source_zip:
        if not os.path.exists(source_zip):
            raise FileNotFoundError(f"source_zip not found: {source_zip}")
        stage = "/tmp/inspire_ws_zip_stage"
        dest = f"/Workspace/Users/{user_email}/InspireAI"
        print(f"Removing prior workspace bundle folder (clean run): {dest}")
        try:
            w.workspace.delete(dest, recursive=True)
        except Exception as exc:
            print(f"  (API delete skipped or failed) {exc}")
        if os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)
        print(f"Unpacking zip -> {dest} ...")
        if os.path.exists(stage):
            shutil.rmtree(stage)
        os.makedirs(stage, exist_ok=True)
        with zipfile.ZipFile(source_zip, "r") as zf:
            zf.extractall(stage)
        entries = os.listdir(stage)
        root_dir = os.path.join(stage, entries[0]) if len(entries) == 1 and os.path.isdir(os.path.join(stage, entries[0])) else stage
        if not os.path.exists(os.path.join(root_dir, "app.yaml")):
            raise FileNotFoundError(f"No app.yaml after unzip under {root_dir}. Use a bundle from scripts/package-for-workspace.sh")
        shutil.copytree(root_dir, dest)
        source_folder = dest
        print(f"  Source from zip: {source_folder}")
    else:
        user_root = f"/Workspace/Users/{user_email}"
        # Resolve the notebook's own directory as the highest-priority source candidate
        try:
            _nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
            _nb_dir = "/Workspace" + "/".join(_nb_path.split("/")[:-1])
        except Exception:
            _nb_dir = None
        fixed_candidates = [
            *([_nb_dir] if _nb_dir else []),  # notebook's own directory first
            f"{user_root}/inspire-ai",
            f"{user_root}/InspireAI-main",
            f"{user_root}/InspireAI",
            f"{user_root}/InspireAI-dev_v_47",
            "/Workspace/Shared/InspireAI",
        ]
        star_candidates = sorted(
            path for path in glob.glob(f"{user_root}/InspireAI-*") if os.path.isdir(path)
        )
        source_candidates = []
        seen = set()
        for candidate in fixed_candidates + star_candidates:
            if candidate not in seen:
                seen.add(candidate)
                source_candidates.append(candidate)
        for candidate in source_candidates:
            if os.path.exists(candidate) and os.path.exists(f"{candidate}/app.yaml"):
                source_folder = candidate
                break
        if not source_folder:
            raise FileNotFoundError(
                f"InspireAI source not found. Run `npm run deploy` from your laptop to upload `InspireAI-workspace.zip` to {auto_zip}, "
                f"or sync the repo to {user_root}/InspireAI (or inspire-ai, InspireAI-main, any {user_root}/InspireAI-*/ with app.yaml, /Workspace/Shared/InspireAI)."
            )

    api_base = workspace_host.rstrip("/")
    try:
        auth_header = w.config.authenticate()
        api_headers = auth_header if isinstance(auth_header, dict) else {"Authorization": f"Bearer {auth_header}"}
    except Exception:
        api_headers = {
            "Authorization": f"Bearer {dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()}"
        }

    def api(method, path, body=None):
        fn = {
            "GET": requests.get,
            "POST": requests.post,
            "PUT": requests.put,
            "PATCH": requests.patch,
            "DELETE": requests.delete,
        }[method]
        kwargs = {"headers": api_headers}
        if body is not None:
            kwargs["json"] = body
        return fn(f"{api_base}{path}", **kwargs)

    def api_get(path):
        response = api("GET", path)
        response.raise_for_status()
        return response.json()

    ctx = {
        "ready": True,
        "w": w,
        "USER_EMAIL": user_email,
        "WORKSPACE_HOST": workspace_host,
        "WAREHOUSE_ID": warehouse_id,
        "WAREHOUSE_NAME": warehouse_name,
        "CATALOG": catalog,
        "SCHEMA": schema,
        "INSPIRE_DB": inspire_db,
        "APP_NAME": app_name,
        "SP_NAME": sp_name,
        "AUTO_ZIP": auto_zip,
        "SOURCE_ZIP": source_zip,
        "SOURCE_FOLDER": source_folder,
        "available_catalogs": available_catalogs,
        "api": api,
        "api_get": api_get,
    }
    print(f"Source:  {source_folder}")
    return ctx



def main():
    ctx = build_runtime_context()
    if not ctx.get("ready"):
        return None

    step_cleanup(ctx)
    step_schema_and_notebooks(ctx)
    step_deploy_and_find_sp(ctx)
    step_grant_permissions(ctx)
    step_render_done(ctx)
    return ctx

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Clean up old app + SPs

# COMMAND ----------

# DBTITLE 1,Cleanup step
def step_cleanup(ctx):
    api = ctx["api"]
    api_get = ctx["api_get"]
    app_name = ctx["APP_NAME"]
    sp_name = ctx["SP_NAME"]

    print("Cleaning up...")
    try:
        api("DELETE", f"/api/2.0/apps/{app_name}")
        print(f"  Deleted app '{app_name}' — waiting for deletion to complete...")
        for _ in range(24):  # up to 2 minutes
            resp = api("GET", f"/api/2.0/apps/{app_name}")
            if resp.status_code == 404:
                print("  App gone ✅")
                break
            state = resp.json().get("compute_status", {}).get("state", "?")
            print(f"  Still {state}, waiting...")
            time.sleep(5)
    except Exception:
        print("  No existing app")

    for search in [app_name, sp_name, "inspire-ai", "inspire_ai"]:
        try:
            sps = api_get(f"/api/2.0/preview/scim/v2/ServicePrincipals?filter=displayName co \"{search}\"")
            for sp in sps.get("Resources", []):
                sp_id = sp.get("id")
                print(f"  Deleting SP: {sp.get('displayName')} (ID: {sp_id}, AppID: {sp.get('applicationId')})")
                api("DELETE", f"/api/2.0/preview/scim/v2/ServicePrincipals/{sp_id}")
        except Exception:
            pass

    print("  Clean ✅")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Schema + Notebook

# COMMAND ----------

# DBTITLE 1,Schema and notebook step
def step_schema_and_notebooks(ctx):
    w = ctx["w"]
    warehouse_id = ctx["WAREHOUSE_ID"]
    catalog = ctx["CATALOG"]
    schema = ctx["SCHEMA"]
    source_folder = ctx["SOURCE_FOLDER"]
    app_name = ctx["APP_NAME"]
    api = ctx["api"]

    if warehouse_id:
        stmt = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
            wait_timeout="30s",
        )
        print(f"Schema: {'✅' if stmt.status and stmt.status.state == StatementState.SUCCEEDED else '⚠️ ' + str(stmt.status)}")

    notebook_dest = f"/Shared/{app_name}/dbx_inspire_ai_agent"
    notebook_path = None

    notebook_source = None
    for candidate in [f"{source_folder}/dbx_inspire_ai_agent", f"{source_folder}/dbx_inspire_ai_agent.ipynb"]:
        if os.path.exists(candidate):
            notebook_source = candidate
            break

    if not notebook_source:
        print(f"⚠️ Notebook not found in: {os.listdir(source_folder)[:20]}")
        raise FileNotFoundError("dbx_inspire_ai_agent not found in source folder")

    print(f"Notebook source: {notebook_source}")

    b64 = export_workspace_b64(ctx, notebook_source, export_formats=("JUPYTER",))
    if not b64:
        raise FileNotFoundError(f"Could not export or read notebook: {notebook_source}")

    try:
        api("POST", "/api/2.0/workspace/mkdirs", {"path": f"/Shared/{app_name}"})
    except Exception:
        pass
    try:
        api("POST", "/api/2.0/workspace/delete", {"path": notebook_dest})
    except Exception:
        pass

    resp = api(
        "POST",
        "/api/2.0/workspace/import",
        {"path": notebook_dest, "format": "JUPYTER", "content": b64, "language": "PYTHON", "overwrite": True},
    )
    if resp.status_code in (200, 201):
        notebook_path = notebook_dest
        print(f"Published to: ✅ {notebook_path}")
    else:
        print(f"Publish failed: ⚠️ {resp.status_code} {resp.text[:300]}")

    try:
        verify = api("GET", f"/api/2.0/workspace/get-status?path={requests.utils.quote(notebook_dest)}")
        if verify.status_code == 200:
            print(f"Verified: ✅ {verify.json().get('object_type')} at {notebook_dest}")
    except Exception:
        pass

    if not notebook_path:
        raise RuntimeError("❌ Notebook publish failed.")

    ctx["NOTEBOOK_DEST"] = notebook_dest
    ctx["NOTEBOOK_PATH"] = notebook_path
    print(f"NOTEBOOK_PATH = {notebook_path}")

    generate_demo_dest = f"/Shared/{app_name}/dbx_generate_demo_data"
    generate_source = None
    for candidate in [f"{source_folder}/dbx_generate_demo_data.py", f"{source_folder}/dbx_generate_demo_data"]:
        if os.path.exists(candidate):
            generate_source = candidate
            break

    if generate_source:
        print(f"Demo generator source: {generate_source}")
        gen_b64 = export_workspace_b64(ctx, generate_source, export_formats=("SOURCE", "JUPYTER"))
        if gen_b64:
            try:
                api("POST", "/api/2.0/workspace/mkdirs", {"path": f"/Shared/{app_name}"})
            except Exception:
                pass
            gen_resp = api(
                "POST",
                "/api/2.0/workspace/import",
                {"path": generate_demo_dest, "format": "SOURCE", "content": gen_b64, "language": "PYTHON", "overwrite": True},
            )
            if gen_resp.status_code in (200, 201):
                print(f"Published demo generator: ✅ {generate_demo_dest}")
            else:
                print(f"Demo generator publish: ⚠️ {gen_resp.status_code} {gen_resp.text[:200]}")
        else:
            print(f"⚠️ Could not read demo generator from {generate_source}")
    else:
        print("⚠️ dbx_generate_demo_data not found in source — skip demo pipeline notebook publish")

    ctx["GENERATE_DEMO_DEST"] = generate_demo_dest

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Snapshot SPs, deploy, find new SP

# COMMAND ----------

# DBTITLE 1,Deploy step
def step_deploy_and_find_sp(ctx):
    api = ctx["api"]
    api_get = ctx["api_get"]
    source_folder = ctx["SOURCE_FOLDER"]
    inspire_db = ctx["INSPIRE_DB"]
    warehouse_id = ctx["WAREHOUSE_ID"]
    notebook_path = ctx["NOTEBOOK_PATH"]
    sp_name = ctx["SP_NAME"]
    app_name = ctx["APP_NAME"]
    catalog = ctx["CATALOG"]

    sp_before = set()
    try:
        data = api_get("/api/2.0/preview/scim/v2/ServicePrincipals?count=500")
        for sp in data.get("Resources", []):
            sp_before.add(sp.get("applicationId", ""))
    except Exception:
        pass
    print(f"SPs before deploy: {len(sp_before)}")

    app_yaml_path = f"{source_folder}/app.yaml"
    write_app_yaml_deploy(app_yaml_path, inspire_db, warehouse_id, notebook_path, sp_name)
    print("app.yaml: ✅")

    app_url = ""
    resp = api("POST", "/api/2.0/apps", {"name": app_name, "description": "Inspire AI v8.8"})
    if resp.status_code in (200, 201):
        app_url = resp.json().get("url", "")
        print(f"App created: {app_url}")
    elif "already exists" in resp.text.lower():
        app_url = api_get(f"/api/2.0/apps/{app_name}").get("url", "")
        print(f"App exists: {app_url}")
    else:
        raise RuntimeError(f"Create failed: {resp.text[:300]}")

    compute_ready = False
    for _ in range(30):
        state = api_get(f"/api/2.0/apps/{app_name}").get("compute_status", {}).get("state", "?")
        if state == "ACTIVE":
            print("Compute: ACTIVE ✅")
            compute_ready = True
            break
        print(f"  Compute: {state}")
        time.sleep(10)

    if not compute_ready:
        raise RuntimeError(
            f"App compute did not reach ACTIVE after 5 minutes (still STARTING). "
            f"Start it manually: Compute → Apps → {app_name} → Start, then re-run."
        )

    resp = api("POST", f"/api/2.0/apps/{app_name}/deployments", {"source_code_path": source_folder})
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Deploy failed: {resp.text[:300]}")
    deploy_id = resp.json().get("deployment_id", "")

    for _ in range(30):
        data = api_get(f"/api/2.0/apps/{app_name}")
        pending, active = data.get("pending_deployment", {}), data.get("active_deployment", {})
        deployment = (
            pending if pending.get("deployment_id") == deploy_id
            else active if active.get("deployment_id") == deploy_id
            else pending or active
        )
        state = deployment.get("status", {}).get("state", "?")
        print(f"  Deploy: {state}")
        if state == "SUCCEEDED":
            app_url = data.get("url", app_url)
            break
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Deploy {state}: {deployment.get('status', {}).get('message', '')}")
        time.sleep(10)

    print("Deployed ✅")
    print("\nFinding new service principal...")
    time.sleep(5)

    new_sp_app_id = None
    try:
        data = api_get("/api/2.0/preview/scim/v2/ServicePrincipals?count=500")
        for sp in data.get("Resources", []):
            app_id = sp.get("applicationId", "")
            if app_id and app_id not in sp_before:
                new_sp_app_id = app_id
                print(f"  NEW SP: {sp.get('displayName')} | applicationId={app_id} | id={sp.get('id')}")
    except Exception as exc:
        print(f"  ⚠️ Could not list SPs: {exc}")

    if not new_sp_app_id:
        print("  ⚠️ Could not find new SP by diff. Trying name search...")
        try:
            sps = api_get(f"/api/2.0/preview/scim/v2/ServicePrincipals?filter=displayName co \"{app_name}\"")
            for sp in sps.get("Resources", []):
                app_id = sp.get("applicationId", "")
                if app_id:
                    new_sp_app_id = app_id
                    print(f"  Found: {sp.get('displayName')} | applicationId={app_id}")
                    break
        except Exception:
            pass

    if not new_sp_app_id:
        print("\n⚠️  COULD NOT FIND SP. You need to grant manually:")
        print("    Run: fetch('/api/health').then(r=>r.json()).then(d=>console.log(d.envVars.SP_CLIENT_ID_resolved))")
        print(f"    Then: GRANT USE_CATALOG ON CATALOG `{catalog}` TO `<applicationId>`")
    else:
        print(f"\n✅ Runtime SP applicationId: {new_sp_app_id}")

    ctx["app_url"] = app_url
    ctx["NEW_SP_APP_ID"] = new_sp_app_id

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Grant permissions

# COMMAND ----------

# DBTITLE 1,Grant step
def step_grant_permissions(ctx):
    new_sp_app_id = ctx.get("NEW_SP_APP_ID")
    warehouse_id = ctx["WAREHOUSE_ID"]
    w = ctx["w"]
    catalog = ctx["CATALOG"]
    schema = ctx["SCHEMA"]
    api = ctx["api"]
    available_catalogs = ctx["available_catalogs"]

    if new_sp_app_id and warehouse_id:
        sp = new_sp_app_id
        print(f"Granting permissions to {sp}...")

        grants = [
            f"GRANT USE_CATALOG ON CATALOG `{catalog}` TO `{sp}`",
            f"GRANT BROWSE ON CATALOG `{catalog}` TO `{sp}`",
            f"GRANT CREATE SCHEMA ON CATALOG `{catalog}` TO `{sp}`",
            f"GRANT USE_SCHEMA ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
            f"GRANT CREATE_TABLE ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
            f"GRANT SELECT ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
            f"GRANT MODIFY ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
        ]

        all_catalogs = set(available_catalogs)
        try:
            for cat in w.catalogs.list():
                all_catalogs.add(cat.name)
        except Exception:
            pass

        skip_catalogs = {"samples", "system", "__databricks_internal", "information_schema"}
        for cat in all_catalogs:
            if cat in skip_catalogs or cat == catalog:
                continue
            grants.append(f"GRANT USE_CATALOG ON CATALOG `{cat}` TO `{sp}`")
            grants.append(f"GRANT BROWSE ON CATALOG `{cat}` TO `{sp}`")

        for sql in grants:
            try:
                stmt = w.statement_execution.execute_statement(
                    warehouse_id=warehouse_id, statement=sql, wait_timeout="15s"
                )
                ok = stmt.status and stmt.status.state == StatementState.SUCCEEDED
                label = sql.split(" ON ")[0].replace("GRANT ", "") + " ON " + sql.split(" ON ")[1].split(" TO ")[0]
                detail = stmt.status.error.message[:80] if stmt.status and stmt.status.error else str(stmt.status)
                print(f"  {label}: {'✅' if ok else '⚠️ ' + detail}")
            except Exception as exc:
                print(f"  ⚠️ {str(exc)[:120]}")

        resp = api(
            "PATCH",
            f"/api/2.0/permissions/sql/warehouses/{warehouse_id}",
            {"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_USE"}]},
        )
        print(f"  CAN_USE on warehouse: {'✅' if resp.status_code == 200 else '⚠️ ' + resp.text[:100]}")
    elif not new_sp_app_id:
        print("⚠️ Skipped — SP not found. Grant manually after checking /api/health.")
    elif not warehouse_id:
        print("⚠️ Skipped — no warehouse.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done!

# COMMAND ----------

# DBTITLE 1,Done step
def step_render_done(ctx):
    api_get = ctx["api_get"]
    app_name = ctx["APP_NAME"]
    inspire_db = ctx["INSPIRE_DB"]
    warehouse_name = ctx["WAREHOUSE_NAME"]
    new_sp_app_id = ctx.get("NEW_SP_APP_ID")

    app_data = api_get(f"/api/2.0/apps/{app_name}")
    app_url = app_data.get("url", "")
    ctx["app_url"] = app_url

    print("=" * 60)
    print("  Inspire AI v8.8 — Ready!")
    print("=" * 60)
    print(f"  Databricks App: {app_name}  (Compute → Apps → {app_name})")
    print("  Open this URL to use Inspire AI (Apps runtime, not this notebook):")
    print(f"  URL:         {app_url}")
    print(f"  Database:    {inspire_db}")
    print(f"  Warehouse:   {warehouse_name}")
    print(f"  SP:          {new_sp_app_id or 'NOT FOUND'}")
    print("=" * 60)

    displayHTML(
        f"""
        <div style="padding:24px;background:linear-gradient(135deg,#1a1a2e,#16213e);border-radius:12px;text-align:center;margin:20px 0">
        <h2 style="color:#e0e0e0;margin-bottom:8px">Inspire AI v8.8 — Databricks App ready</h2>
        <p style="color:#aaa;font-size:13px;margin-bottom:8px">Installer finished. Run the product from the <strong>Databricks App</strong> below (hosted Apps compute + <code>start.sh</code>).</p>
        <p style="color:#888;font-size:12px;margin-bottom:20px">Do not rely on this notebook for day-to-day use — open the App.</p>
        <a href="{app_url}" target="_blank" style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#ff6b35,#f7931e);color:white;text-decoration:none;border-radius:8px;font-size:18px;font-weight:600;box-shadow:0 4px 15px rgba(255,107,53,0.3)">Open Inspire AI (Databricks App)</a>
        <p style="color:#666;margin-top:14px;font-size:12px">{app_url}</p>
        </div>"""
    )

# COMMAND ----------

# DBTITLE 1,Main entry point
try:
    main()
except (FileNotFoundError, RuntimeError, requests.exceptions.HTTPError) as exc:
    show_warning_banner("Setup incomplete", str(exc))
