"""Additive managed-cell storage and guards shared by every grid write path."""

TYPED_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_typed_cell (
    cell_id TEXT PRIMARY KEY,
    sheet_id INTEGER NOT NULL REFERENCES ledger_sheet(sheet_id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL CHECK (row_number BETWEEN 1 AND 1000),
    column_number INTEGER NOT NULL CHECK (column_number BETWEEN 1 AND 256),
    kind TEXT NOT NULL CHECK (kind IN ('decimal','text','boolean','date')),
    raw_value JSONB,
    unit TEXT,
    display_format TEXT,
    expression JSONB,
    dependencies TEXT[] NOT NULL DEFAULT '{}',
    calculated_value TEXT,
    calculation_status TEXT NOT NULL CHECK (calculation_status IN ('input','pending','current','failed')),
    calculation_error TEXT,
    engine_version TEXT,
    UNIQUE (sheet_id,row_number,column_number)
);

CREATE OR REPLACE FUNCTION protect_ledger_typed_cells() RETURNS trigger AS $$
DECLARE managed RECORD;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM ledger_typed_cell WHERE sheet_id=OLD.sheet_id)
           AND EXISTS (SELECT 1 FROM ledger_spreadsheet WHERE spreadsheet_id=OLD.workspace) THEN
            RAISE EXCEPTION 'Managed sheets cannot be deleted by ordinary operations' USING ERRCODE='23514';
        END IF;
        RETURN OLD;
    END IF;
    IF current_setting('ledger.typed_write', true) = 'on' THEN
        RETURN NEW;
    END IF;
    IF EXISTS (SELECT 1 FROM ledger_typed_cell WHERE sheet_id=OLD.sheet_id) THEN
        IF NEW.workspace IS DISTINCT FROM OLD.workspace
           OR jsonb_array_length(NEW.grid) <> jsonb_array_length(OLD.grid)
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(OLD.grid) WITH ORDINALITY r(value,position)
               WHERE jsonb_array_length(r.value) <> jsonb_array_length(NEW.grid -> (r.position::int-1))
           ) THEN
            RAISE EXCEPTION 'Managed sheet layout requires checked typed operations' USING ERRCODE='23514';
        END IF;
        FOR managed IN SELECT row_number,column_number FROM ledger_typed_cell WHERE sheet_id=OLD.sheet_id LOOP
            IF (NEW.grid -> (managed.row_number-1) -> (managed.column_number-1))
                IS DISTINCT FROM (OLD.grid -> (managed.row_number-1) -> (managed.column_number-1)) THEN
                RAISE EXCEPTION 'Managed inputs and computed cells require checked typed operations' USING ERRCODE='23514';
            END IF;
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER ledger_typed_guard
BEFORE UPDATE OR DELETE ON ledger_sheet
FOR EACH ROW EXECUTE FUNCTION protect_ledger_typed_cells();
"""
