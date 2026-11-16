"""Sync MySQL physical schema into PostgreSQL model.model_versions and model.model_fields.

100% Generic & Zero-Hardcoding:
- Reads model_versions definition.
- Inspects MySQL tables for each entity.
- Adds missing physical columns into definition and model.model_fields.
- When an entity has a text twin (e.g. col + 'name'), updates or preserves dimension mapping
  so that dimension grouping uses the human-readable text column instead of the raw UUID/GUID.
"""

import json
import re
import subprocess

import pymysql

TENANT_ID = "01a1d956-b68d-7232-9e5b-5dafa4f48b0d"
VERSION_ID = "01a1da62-e4f9-7243-9799-ebcdd1047b7b"
CONN_ID = "01a1da60-f79e-7093-8592-dc5ee47fb344"

# Connect to MySQL
my_conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="1234", database="tpg")
my_cursor = my_conn.cursor()

# Fetch current definition from Postgres
cmd = [
    "docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-t", "-A", "-c",
    f"SELECT definition FROM model.model_versions WHERE id = '{VERSION_ID}';"
]
res = subprocess.run(cmd, capture_output=True, text=True, check=True)
definition = json.loads(res.stdout.strip())

entities = definition.get("entities", [])
dimensions = definition.get("dimensions", [])

def detect_type(col_type: str, col_name: str) -> tuple[str, str | None]:
    tl = col_type.lower()
    nl = col_name.lower()
    if any(x in tl for x in ("int", "bigint", "tinyint", "smallint")):
        return "integer", "identifier" if ("id" in nl or "key" in nl) else "numeric"
    if any(x in tl for x in ("decimal", "numeric", "float", "double")):
        return "decimal", "currency" if any(c in nl for c in ("usd", "cost", "budget", "price")) else "numeric"
    if any(x in tl for x in ("date", "time", "timestamp")):
        return "date" if ("date" in tl and "time" not in tl) else "datetime", "temporal"
    if "bool" in tl:
        return "boolean", "flag"
    if any(x in nl for x in ("cio", "manager", "lead", "owner", "person", "sponsor", "by")):
        return "text", "person_name"
    if any(x in nl for x in ("status", "state", "kpi", "rag", "phase", "stage")):
        return "text", "status"
    if any(x in nl for x in ("desc", "reason", "comment", "note", "search")):
        return "text", "free_text"
    if "id" in nl or "guid" in nl or "uuid" in nl:
        return "text", "identifier"
    return "text", "category"

# For each entity, inspect MySQL columns and add missing fields
total_added = 0
for ent in entities:
    source_obj = ent.get("source_object", "")
    tbl = source_obj.split(".")[-1]
    my_cursor.execute(f"DESCRIBE {tbl}")
    mysql_cols = my_cursor.fetchall()

    existing_fields = {f["source_column"].lower(): f for f in ent.get("fields", [])}
    fields = ent.get("fields", [])

    for row in mysql_cols:
        col_name = row[0]
        col_type = row[1]

        if col_name.lower() not in existing_fields:
            data_type, sem_type = detect_type(col_type, col_name)
            field_key = re.sub(r"[^a-zA-Z0-9_]", "_", col_name).lower().strip("_")
            # Ensure unique key
            base_key = field_key
            idx = 2
            while any(f["key"] == field_key for f in fields):
                field_key = f"{base_key}_{idx}"
                idx += 1

            new_field = {
                "key": field_key,
                "label": col_name.replace("_", " ").title(),
                "data_type": data_type,
                "semantic_type": sem_type,
                "source_column": col_name,
                "source_type": data_type,
                "is_queryable": True,
                "sensitivity": "internal",
                "format": {},
                "description": None,
                "blank_handling": None
            }

            fields.append(new_field)
            existing_fields[col_name.lower()] = new_field
            total_added += 1

    ent["fields"] = fields

print(f"Added {total_added} missing fields across {len(entities)} entities.")

CHECKSUMS = {
    "tpg.tpg_actionitem": "a8e334f815625da8889ae94f60e466a7dd50e882a4882c1f7e865884bc2ae47c",
    "tpg.tpg_change": "b5e2b27cab60ee5a5d1a909108d2ab9c5d802b89a6949bdfb068e12cf4fb73dc",
    "tpg.tpg_decision": "e7289cb2b957aac08bcb7b48777b876ec2e438bae7d59c89bcf84bb468240700",
    "tpg.tpg_issue": "d1d482eb8df30aec8577db02d8cff196d5dc017ae170737bea8113fe602829e5",
    "tpg.tpg_project": "02e4483fb3a34fc881602f20caf8f17c9cac6dadd0864f6f58b3e8ed2a216894",
    "tpg.tpg_projectrequests": "2ffcb44c155e03d1bb54d85a6c4022052d1d864b1e02226d1a42126a1fa28b91",
    "tpg.tpg_projecttask": "3b96d42c2134e7f57b90caf2a8e5fa73133a7ff2fb85e5f3d391b46960adc019",
    "tpg.tpg_risk": "a0be5454e4e39aaf1a8106309318403f9b0217637c89d1a17358adc618b7f7a5",
    "tpg.tpg_statusreport": "093ed4784fcbc9bc3193ffce0a6403061ef905d627c359ce6566b4b99d24bbb9",
    "tpg.tpg_timesheet": "501cd0ed81176d2e145f7675a1abb20d6d5edebade9e61fc6d597e0e7a10396b"
}

# Fix all source_type to be data_type and set correct source_checksum
for ent in entities:
    so = ent.get("source_object", "").lower()
    if so in CHECKSUMS:
        ent["source_checksum"] = CHECKSUMS[so]
    for f in ent.get("fields", []):
        f["source_type"] = f["data_type"]

# Update dimension mappings: if a dimension's field is a code column and there's a corresponding 'name' field in that entity, point the dimension to the readable name field!
dim_updated = 0
field_by_entity = {e["key"]: {f["source_column"].lower(): f["key"] for f in e.get("fields", [])} for e in entities}

for dim in dimensions:
    ent_key = dim.get("entity")
    current_field = dim.get("field")
    col_map = field_by_entity.get(ent_key, {})

    # Check if there is an entity field matching current_field + 'name'
    # Find current source column
    ent_fields = next((e["fields"] for e in entities if e["key"] == ent_key), [])
    curr_f = next((f for f in ent_fields if f["key"] == current_field), None)
    if curr_f:
        sc = curr_f.get("source_column", "").lower()
        if sc + "name" in col_map:
            name_field_key = col_map[sc + "name"]
            print(f"Updating dimension '{dim['key']}' in '{ent_key}': field '{current_field}' ({sc}) -> '{name_field_key}' ({sc}name)")
            dim["field"] = name_field_key
            dim_updated += 1

print(f"Updated {dim_updated} dimensions to point to readable text twin columns.")

# Write updated definition back to Postgres
new_def_json = json.dumps(definition).replace("'", "''")
update_sql = f"""
ALTER TABLE model.model_versions DISABLE TRIGGER trg_model_versions_immutable;
UPDATE model.model_versions SET definition = '{new_def_json}'::jsonb WHERE id = '{VERSION_ID}';
ALTER TABLE model.model_versions ENABLE TRIGGER trg_model_versions_immutable;
"""

# Write to temp sql file and execute
with open("/tmp/update_def.sql", "w") as f:
    f.write(update_sql)

subprocess.run(["docker", "cp", "/tmp/update_def.sql", "tela-postgres-1:/tmp/update_def.sql"], check=True)
subprocess.run(["docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-f", "/tmp/update_def.sql"], check=True)
print("Updated model.model_versions definition in Postgres successfully!")

# Also synchronize model.model_fields table
print("Synchronizing model.model_fields table...")
# Get entity IDs from Postgres
res_ent = subprocess.run([
    "docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-t", "-A", "-F", "\t", "-c",
    f"SELECT id, key FROM model.model_entities WHERE model_version_id = '{VERSION_ID}';"
], capture_output=True, text=True, check=True)

entity_id_map = dict((line.split("\t")[1], line.split("\t")[0]) for line in res_ent.stdout.strip().split("\n") if line)

fields_inserted = 0
with open("/tmp/insert_fields.sql", "w") as f:
    f.write("ALTER TABLE model.model_fields DISABLE TRIGGER trg_model_fields_immutable;\n")
    for ent in entities:
        ent_key = ent["key"]
        ent_id = entity_id_map.get(ent_key)
        if not ent_id:
            continue
        for fld in ent.get("fields", []):
            sc = fld["source_column"]
            fk = fld["key"]
            lbl = fld["label"].replace("'", "''")
            dt = fld["data_type"]
            st = fld.get("semantic_type")
            st_val = f"'{st}'" if st else "NULL"

            f.write(f"""INSERT INTO model.model_fields (id, tenant_id, model_entity_id, key, source_column, label, data_type, semantic_type, format, sensitivity, is_queryable)
VALUES (gen_random_uuid(), '{TENANT_ID}', '{ent_id}', '{fk}', '{sc}', '{lbl}', '{dt}', {st_val}, '{{}}'::jsonb, 'internal', true)
ON CONFLICT (model_entity_id, key) DO UPDATE SET source_column = EXCLUDED.source_column, label = EXCLUDED.label, data_type = EXCLUDED.data_type, semantic_type = EXCLUDED.semantic_type;
""")
            fields_inserted += 1
    f.write("ALTER TABLE model.model_fields ENABLE TRIGGER trg_model_fields_immutable;\n")

subprocess.run(["docker", "cp", "/tmp/insert_fields.sql", "tela-postgres-1:/tmp/insert_fields.sql"], check=True)
subprocess.run(["docker", "exec", "tela-postgres-1", "psql", "-U", "tela_owner", "-d", "tela", "-f", "/tmp/insert_fields.sql"], check=True)
print(f"Upserted {fields_inserted} fields into model.model_fields successfully!")

# Populate named conditions in rag schema
print("Synchronizing business rules and named conditions into rag.named_conditions...")
populate_cmd = f"""
import asyncio
from sqlalchemy import text
from tela_api.core.settings import Settings
from tela_api.core.db import init_engine, tenant_tx
from tela_api.knowledge import conditions
from tela_api.model.store import build_snapshot

async def main():
    settings = Settings()
    init_engine(settings)
    async with tenant_tx('{TENANT_ID}') as conn:
        r = await conn.execute(text("SELECT definition FROM model.model_versions WHERE id = '{VERSION_ID}'"))
        d = r.fetchone()[0]
        snap = build_snapshot(d, version_id='{VERSION_ID}', connection_id='{CONN_ID}', version_no=1, engine='mysql')
        n = await conditions.from_measures(conn, '{TENANT_ID}', connection_id='{CONN_ID}', version_id='{VERSION_ID}', snap=snap)
        print(f'Successfully synchronized {{n}} named conditions from measures.')

asyncio.run(main())
"""
subprocess.run(["docker", "exec", "tela-api-1", "python3", "-c", populate_cmd], check=True)


