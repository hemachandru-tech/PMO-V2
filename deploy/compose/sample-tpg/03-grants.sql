-- Runs after 02-data.sql: Tela's login may read the extract and nothing else (RULE-001).
GRANT USAGE ON SCHEMA public TO tela_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tela_ro;
