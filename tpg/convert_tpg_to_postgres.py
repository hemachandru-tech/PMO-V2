#!/usr/bin/env python3
"""
Convert Dataverse TPG Schema + Synthetic Records JSON to PostgreSQL SQL Script.
"""

import json
import os
import sys

def map_pg_type(table_name, col_name, attr_type):
    if table_name == "systemuser" and col_name == "address1_addressid":
        return "TEXT"  # Synthetic dataset contains mock text in this field
    
    mapping = {
        "Uniqueidentifier": "UUID",
        "Lookup": "UUID",
        "Owner": "UUID",
        "String": "TEXT",
        "Memo": "TEXT",
        "Virtual": "TEXT",
        "Boolean": "BOOLEAN",
        "Integer": "INTEGER",
        "Picklist": "INTEGER",
        "State": "INTEGER",
        "Status": "INTEGER",
        "BigInt": "BIGINT",
        "Decimal": "NUMERIC",
        "Money": "NUMERIC(18, 4)",
        "Double": "DOUBLE PRECISION",
        "DateTime": "TIMESTAMPTZ",
        "EntityName": "VARCHAR(100)",
    }
    return mapping.get(attr_type, "TEXT")

def get_column_value(row, col_name, attr):
    # 1. Direct value in row
    if col_name in row and row[col_name] is not None:
        return row[col_name]
    
    # 2. Lookup _<col>_value in row
    lookup_key = f"_{col_name}_value"
    if lookup_key in row and row[lookup_key] is not None:
        return row[lookup_key]
    
    # 3. Formatted display value from parent attributeOf (e.g. tpg_priorityname -> tpg_priority)
    attr_of = attr.get("attributeOf")
    if attr_of:
        val = row.get(f"{attr_of}@OData.Community.Display.V1.FormattedValue")
        if val is None:
            val = row.get(f"_{attr_of}_value@OData.Community.Display.V1.FormattedValue")
        if val is not None:
            return val
            
    # 4. Direct formatted display value
    fmt_key = f"{col_name}@OData.Community.Display.V1.FormattedValue"
    if fmt_key in row and row[fmt_key] is not None:
        return row[fmt_key]
        
    return None

def format_sql_value(val, pg_type):
    if val is None:
        return "NULL"
    if pg_type == "BOOLEAN":
        return "TRUE" if val else "FALSE"
    if pg_type in ("INTEGER", "BIGINT"):
        try:
            return str(int(val))
        except (ValueError, TypeError):
            return "NULL"
    if pg_type in ("NUMERIC", "NUMERIC(18, 4)", "DOUBLE PRECISION"):
        try:
            float(val)
            return str(val)
        except (ValueError, TypeError):
            return "NULL"
    
    # Strings, UUIDs, Timestamps, Text
    val_str = str(val).replace("'", "''")
    return f"'{val_str}'"

# Audit and ownership lookups exist on every table and all point at systemuser/team/businessunit.
# As foreign keys they would turn into ~230 model links, so they stay plain indexed columns.
SYSTEM_LOOKUPS = {"createdby", "modifiedby", "createdonbehalfby", "modifiedonbehalfby",
                  "ownerid", "owninguser", "owningteam", "owningbusinessunit"}

ON_DELETE = {"Cascade": "CASCADE", "RemoveLink": "SET NULL", "Restrict": "RESTRICT"}

def plan_foreign_keys(tables):
    """(enforced, skipped) Dataverse lookups for the dump.

    One foreign key per (table, target) pair: Tela joins two entities along the first link
    between them, so a second lookup to the same table would make that join ambiguous. The
    most-filled lookup wins (ties keep the Dataverse order); the rest are listed as skipped.
    """
    candidates, skipped = {}, []
    for tname, tdata in tables.items():
        attrs = {a["logicalName"]: a for a in tdata.get("attributes", [])}
        rows = tdata.get("records", {}).get("rows", [])
        for rel in tdata.get("relationships", {}).get("lookupsOut", []):
            col, target = rel["viaColumn"], rel["toTable"]
            if col in SYSTEM_LOOKUPS or target not in tables or col not in attrs:
                continue
            filled = sum(1 for r in rows if get_column_value(r, col, attrs[col]) is not None)
            if not filled:
                skipped.append((tname, col, target, "no values in the sample data"))
                continue
            candidates.setdefault((tname, target), []).append((filled, col, rel))
    enforced = []
    for (tname, target), options in candidates.items():
        best = max(options, key=lambda o: o[0])
        enforced.append((tname, best[1], target, best[2]))
        for _, col, _ in options:
            if col != best[1]:
                skipped.append((tname, col, target, f"{best[1]} is the join path to {target}"))
    return enforced, skipped

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(here, "TPG Schema + fully synthetic sample records.json")
    output_sql_path = os.path.join(here, "tpg_dataverse_postgresql.sql")
    
    print(f"Loading {json_path}...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    meta = data.get("meta", {})
    tables = data.get("tables", {})
    
    print(f"Loaded metadata. Tables count: {len(tables)}. Generating SQL...")
    
    with open(output_sql_path, "w", encoding="utf-8") as out:
        # Header
        out.write("-- =====================================================================\n")
        out.write("-- PostgreSQL Schema & Synthetic Data for Microsoft Dataverse (TPG)\n")
        out.write(f"-- Generated from: {os.path.basename(json_path)}\n")
        out.write(f"-- Origin: {meta.get('origin', 'N/A')}\n")
        out.write(f"-- Extracted At: {meta.get('extractedAt', 'N/A')}\n")
        out.write(f"-- Synthetic Generated: {meta.get('syntheticGenerated', 'N/A')}\n")
        out.write(f"-- Total Tables: {len(tables)}\n")
        out.write("-- =====================================================================\n\n")
        
        out.write("BEGIN;\n\n")
        
        # Schema definition
        out.write("-- 1. Schema setup\n")
        out.write("CREATE SCHEMA IF NOT EXISTS dataverse;\n")
        out.write("SET search_path TO dataverse, public;\n\n")
        
        # OptionSet Reference Table
        out.write("-- =====================================================================\n")
        out.write("-- 2. Reference Table: Dataverse OptionSets / Picklists Metadata\n")
        out.write("-- =====================================================================\n")
        out.write("DROP TABLE IF EXISTS optionset_metadata CASCADE;\n")
        out.write("CREATE TABLE optionset_metadata (\n")
        out.write("    table_name TEXT NOT NULL,\n")
        out.write("    column_name TEXT NOT NULL,\n")
        out.write("    kind TEXT,\n")
        out.write("    option_set_name TEXT,\n")
        out.write("    option_value INTEGER NOT NULL,\n")
        out.write("    option_label TEXT NOT NULL,\n")
        out.write("    PRIMARY KEY (table_name, column_name, option_value)\n")
        out.write(");\n\n")
        
        # Populate OptionSets
        opt_rows = []
        for tname, tdata in tables.items():
            for col_name, oset in tdata.get("optionSets", {}).items():
                kind = oset.get("kind", "")
                oset_name = oset.get("optionSetName", "")
                for opt in oset.get("options", []):
                    oval = opt.get("value")
                    olabel = opt.get("label", "").replace("'", "''")
                    opt_rows.append(f"('{tname}', '{col_name}', '{kind}', '{oset_name}', {oval}, '{olabel}')")
                    
        if opt_rows:
            batch_size = 100
            for i in range(0, len(opt_rows), batch_size):
                batch = opt_rows[i:i + batch_size]
                out.write("INSERT INTO optionset_metadata (table_name, column_name, kind, option_set_name, option_value, option_label) VALUES\n")
                out.write(",\n".join(f"  {r}" for r in batch))
                out.write(";\n")
        out.write("\n")
        
        # Tables DDL & DML
        for tname, tdata in tables.items():
            primary_id = tdata.get("primaryId")
            attributes = tdata.get("attributes", [])
            display_name = tdata.get("displayName", tname).replace("'", "''")
            rows = tdata.get("records", {}).get("rows", [])
            row_count = len(rows)
            
            out.write("-- =====================================================================\n")
            out.write(f"-- Table: {tname} ({display_name}) - {row_count} records\n")
            out.write("-- =====================================================================\n")
            out.write(f"DROP TABLE IF EXISTS \"{tname}\" CASCADE;\n")
            out.write(f"CREATE TABLE \"{tname}\" (\n")
            
            col_defs = []
            col_info_list = []
            
            for attr in attributes:
                col_name = attr["logicalName"]
                attr_type = attr.get("type", "String")
                pg_type = map_pg_type(tname, col_name, attr_type)
                
                col_info_list.append((col_name, pg_type, attr))
                
                if col_name == primary_id:
                    col_defs.append(f"    \"{col_name}\" {pg_type} NOT NULL")
                else:
                    col_defs.append(f"    \"{col_name}\" {pg_type}")
                    
            if primary_id:
                col_defs.append(f"    CONSTRAINT \"pk_{tname}\" PRIMARY KEY (\"{primary_id}\")")
                
            out.write(",\n".join(col_defs))
            out.write("\n);\n\n")
            
            # Table & Column comments
            out.write(f"COMMENT ON TABLE \"{tname}\" IS 'Dataverse entity {tname} ({display_name})';\n")
            for col_name, pg_type, attr in col_info_list:
                desc = attr.get("description")
                disp = attr.get("displayName")
                comment_parts = []
                if disp:
                    comment_parts.append(disp)
                if desc:
                    comment_parts.append(desc)
                if comment_parts:
                    comment_str = " - ".join(comment_parts).replace("'", "''")
                    out.write(f"COMMENT ON COLUMN \"{tname}\".\"{col_name}\" IS '{comment_str}';\n")
            out.write("\n")
            
            # Insert Records
            if rows:
                col_names = [col_name for col_name, _, _ in col_info_list]
                cols_sql = ", ".join(f"\"{c}\"" for c in col_names)
                
                batch_size = 50
                for b_start in range(0, len(rows), batch_size):
                    batch = rows[b_start:b_start + batch_size]
                    out.write(f"INSERT INTO \"{tname}\" ({cols_sql}) VALUES\n")
                    row_sqls = []
                    for r in batch:
                        vals_sql = []
                        for col_name, pg_type, attr in col_info_list:
                            raw_val = get_column_value(r, col_name, attr)
                            vals_sql.append(format_sql_value(raw_val, pg_type))
                        row_sqls.append(f"  ({', '.join(vals_sql)})")
                    out.write(",\n".join(row_sqls))
                    out.write(";\n\n")
                    
            # Create indexes on Lookups / Foreign Keys
            lookups = [c for c, pt, a in col_info_list if pt == "UUID" and c != primary_id]
            if lookups:
                out.write(f"-- Indexes for {tname}\n")
                for lcol in lookups:
                    idx_name = f"idx_{tname}_{lcol}"[:63] # PostgreSQL 63-char limit
                    out.write(f"CREATE INDEX IF NOT EXISTS \"{idx_name}\" ON \"{tname}\" (\"{lcol}\");\n")
                out.write("\n")

        # Relationships, added after all rows so table order doesn't matter
        enforced, skipped = plan_foreign_keys(tables)
        out.write("-- =====================================================================\n")
        out.write("-- 3. Relationships: Dataverse lookups as foreign keys\n")
        out.write("-- =====================================================================\n")
        for tname, col, target, rel in enforced:
            fk_name = f"fk_{tname}_{col}"[:63]
            on_delete = ON_DELETE.get(rel.get("cascadeDelete"), "NO ACTION")
            out.write(f"ALTER TABLE \"{tname}\" ADD CONSTRAINT \"{fk_name}\" FOREIGN KEY (\"{col}\") "
                      f"REFERENCES \"{target}\" (\"{tables[target]['primaryId']}\") ON DELETE {on_delete}; "
                      f"-- {rel['schemaName']}\n")
        out.write("\n-- Lookups left without a foreign key (add them as model links by hand if needed):\n")
        for tname, col, target, reason in skipped:
            out.write(f"--   {tname}.{col} -> {target}: {reason}\n")
        out.write("\n")

        out.write("COMMIT;\n")
        out.write("-- =====================================================================\n")
        out.write("-- End of Dataverse TPG PostgreSQL Script\n")
        out.write("-- =====================================================================\n")
        
    print(f"Successfully generated {output_sql_path} ({os.path.getsize(output_sql_path) / 1024 / 1024:.2f} MB)")

if __name__ == "__main__":
    main()
