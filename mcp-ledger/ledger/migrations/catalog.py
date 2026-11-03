"""Read owned PostgreSQL definitions without changing database objects."""

from typing import Any

from sqlalchemy import Connection

CATALOG_QUERIES = {
    "tables": "SELECT n.nspname AS schema_name, c.relname AS table_name, c.relkind::text AS relkind, c.relpersistence::text AS relpersistence, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m') AND n.nspname='public' ORDER BY 1,2",
    "columns": "SELECT table_schema, table_name, column_name, data_type, udt_schema, udt_name, domain_schema, domain_name, is_nullable, column_default, is_identity, identity_generation, is_generated, generation_expression, character_maximum_length, numeric_precision, numeric_scale, datetime_precision, collation_schema, collation_name, CASE WHEN is_identity='YES' THEN pg_get_serial_sequence(format('%I.%I', table_schema, table_name), column_name) ELSE NULL END AS identity_sequence FROM information_schema.columns WHERE table_schema='public' ORDER BY table_schema,table_name,column_name",
    "constraints": "SELECT n.nspname AS schema_name, c.relname AS table_name, k.conname, k.contype::text AS contype, k.condeferrable, k.condeferred, k.convalidated, pg_get_constraintdef(k.oid,true) AS definition FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1,2,3",
    "indexes": "SELECT n.nspname AS schema_name, t.relname AS table_name, i.relname AS index_name, x.indisvalid, x.indisready, pg_get_indexdef(i.oid) AS definition FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class t ON t.oid=x.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2,3",
    "triggers": "SELECT n.nspname AS schema_name, c.relname AS table_name, t.tgname, t.tgenabled::text AS tgenabled, pg_get_triggerdef(t.oid,true) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname='public' ORDER BY 1,2,3",
    "functions": "SELECT n.nspname AS schema_name, p.proname, pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p') AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2",
    "sequences": "SELECT schemaname, sequencename, data_type::text, start_value, min_value, max_value, increment_by, cycle, cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY 1,2",
    "extensions": "SELECT extname, n.nspname AS schema_name FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY extname",
}


def read_catalog(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    """Read schema definitions on the caller's migration connection.

    Args:
        connection: Dedicated connection with the migration lock.

    Returns:
        Public database definitions, without stored application rows.
    """
    return {
        kind: [dict(row) for row in connection.exec_driver_sql(query).mappings()]
        for kind, query in CATALOG_QUERIES.items()
    }
