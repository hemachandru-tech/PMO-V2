# ruff: noqa: RUF001 - homoglyphs and smart quotes are deliberate attack inputs
"""Adversarial SQL corpus (US-027; TEST-071, TEST-072): stacked queries, comments, UNION to system tables,
SELECT INTO, sleeps, locks, DDL/DML, homoglyphs and quote tricks, combined with carrier templates.

``malicious_statements()`` yields > 500 full statements per dialect that the runner's guard must refuse.
``injection_values()`` yields strings used as filter values, which must only ever travel as bound values.
"""

from __future__ import annotations

PAYLOADS = [
    "; DROP TABLE projects", "; DELETE FROM projects", "; UPDATE projects SET status = 'x'", "; TRUNCATE projects",
    "; INSERT INTO projects (project_id) VALUES ('x')", "; CREATE TABLE x (a int)", "; ALTER TABLE projects ADD x int",
    "; GRANT ALL ON projects TO PUBLIC", "; COPY projects TO '/tmp/x'", "; SET ROLE postgres", "; COMMIT",
    " UNION SELECT usename, passwd FROM pg_catalog.pg_shadow", " UNION SELECT table_name, 1 FROM information_schema.tables",
    " UNION SELECT rolname, 1 FROM pg_roles", " -- comment", " /* comment */", "; SELECT pg_sleep(10)",
    " AND pg_sleep(10) IS NULL", " AND 1=(SELECT 1 FROM pg_sleep(5))", "; WAITFOR DELAY '0:0:10'", " AND SLEEP(5)",
    " AND BENCHMARK(1000000, MD5('x'))", " AND pg_read_file('/etc/passwd') IS NOT NULL",
    " AND load_file('/etc/passwd') IS NOT NULL", "; EXEC xp_cmdshell 'dir'",
    " FOR UPDATE", " FOR SHARE", " INTO OUTFILE '/tmp/x'", "; LOCK TABLE projects",
    " AND current_setting('is_superuser') = 'on'", " AND set_config('x', 'y', false) IS NOT NULL",
    " AND dblink('host=evil', 'select 1') IS NOT NULL", " AND lo_import('/etc/passwd') > 0",
]
TEMPLATES = [
    "SELECT region FROM public.projects WHERE status = 'Active'{p}",
    "SELECT count(*) FROM public.projects{p}",
    "SELECT * FROM public.projects WHERE project_id = '1'{p}",
    "WITH x AS (SELECT * FROM public.projects) SELECT * FROM x{p}",
    "SELECT a.region FROM public.projects a JOIN public.project_requests b ON a.project_request_id = b.project_request_id{p}",
    "SELECT region, count(*) FROM public.projects GROUP BY region{p}",
    "SELECT * FROM (SELECT * FROM public.projects) AS t{p}",
    "SELECT name FROM public.projects ORDER BY name{p}",
    "SELECT name FROM public.projects LIMIT 5{p}",
    "SELECT max(planned_deployment_date) FROM public.projects{p}",
    "SELECT DISTINCT cio FROM public.projects{p}",
    "SELECT region FROM public.projects WHERE region IN ('KSA'){p}",
    "SELECT region FROM public.projects WHERE name LIKE 'P%'{p}",
    "SELECT * FROM public.projects WHERE cio = 'x' OR '1'='1'{p}",
    "SELECT * FROM public.projects UNION ALL SELECT * FROM public.projects{p}",
    "SELECT * FROM public.projects WHERE EXISTS (SELECT 1 FROM public.project_requests){p}",
]
STANDALONE = [
    "DROP TABLE public.projects", "DELETE FROM public.projects", "UPDATE public.projects SET status = 'x'",
    "INSERT INTO public.projects (project_id) VALUES ('x')", "TRUNCATE public.projects", "CREATE TABLE x (a int)",
    "SELECT * INTO new_table FROM public.projects", "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.columns", "SELECT * FROM secrets", "SELECT * FROM public.other_table",
    "WITH projects AS (SELECT * FROM public.project_requests) SELECT * FROM projects",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT * FROM r",
    "SELECT * FROM otherdb.public.projects", "SET search_path = evil", "SHOW ALL", "EXPLAIN SELECT 1",
    "VACUUM", "ANALYZE public.projects", "COPY public.projects TO STDOUT", "BEGIN", "COMMIT", "CALL proc()",
    "SELECT pg_sleep(1) FROM public.projects", "SELECT * FROM public.projects FOR UPDATE",
    "SELECT * FROM public.projects; SELECT 1", "ＳＥＬＥＣＴ * FROM public.projects; DROP TABLE public.projects",
    "SELECT * FROM public.projects WHERE region = 'KЅA'; DELETE FROM public.projects",
    "SELECT nextval('s') FROM public.projects", "SELECT * FROM pg_stat_activity",
]


def malicious_statements() -> list[str]:
    out = [t.format(p=p) for t in TEMPLATES for p in PAYLOADS]
    return out + STANDALONE


def injection_values() -> list[str]:
    values = [p.strip() for p in PAYLOADS]
    values += [f"x'{p}" for p in PAYLOADS] + [f"x\"{p}" for p in PAYLOADS] + [f"x\\'{p}" for p in PAYLOADS]
    values += ["' OR '1'='1", "’ OR 1=1 --", "%(p0)s", "$1", ":p0", "\x00", "Robert'); DROP TABLE projects;--"]
    return values
