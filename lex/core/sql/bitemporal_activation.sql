-- In-database activation of future-dated bitemporal records.
--
-- Installed by the lex.core migration ``0001_bitemporal_activation`` (PostgreSQL
-- only; other backends keep the in-process fallback). Called every minute by the
-- pg_cron job the instance controller registers per instance database:
--
--     SELECT lex_apply_due_activations();
--
-- Design and rationale:
--     docs/superpowers/specs/2026-09-16-bitemporal-activation-applier-design.md
--
-- One table and three functions, one contract:
--
--   lex_bitemporal_tables()      discovery  - every (main, history, meta, pk) triple,
--                                             derived from the catalog on every call.
--                                             Nothing is registered; nothing can be stale.
--   lex_pending_activations()    visibility - every SCHEDULED meta row across every
--                                             triple, due or not, with how long it has
--                                             been due. Read-only. The monitoring surface.
--   lex_apply_due_activations()  the applier - for every record with a due SCHEDULED
--                                             meta row: converge the main row to the
--                                             history row that is effective *now*, then
--                                             flip the due meta rows to DONE, in place.
--                                             Writes a heartbeat row on every run.
--
-- All three are SECURITY DEFINER: the cron job runs as the server admin the controller
-- uses, and the functions execute with the privileges of the app role that owns the
-- tables (the role that ran the migration). search_path is pinned with pg_catalog
-- first so nothing in public can shadow a catalog function.
--
-- The applier computes an end state; it never replays a sequence. Two runs produce one
-- result, and running it beside the Python path (activate_history_version) is safe:
-- both lock the history row, both upsert the main row, both flip only SCHEDULED rows.


-- ---------------------------------------------------------------------------
-- Heartbeat
-- ---------------------------------------------------------------------------
-- One row, id = 1, upserted at the end of every applier run whether or not anything was
-- due. lex-app reads last_run_at at save time (activation_applier.applier_is_alive) to
-- decide whether a future-dated save still needs an in-process timer; operators read
-- the rest. Deliberately not a Django model: the framework registers every concrete
-- model it discovers under lex/ for history tracking and for the model list, and an
-- operational heartbeat belongs in neither.
CREATE TABLE IF NOT EXISTS lex_activation_applier_state (
    id                   smallint    PRIMARY KEY CHECK (id = 1),
    last_run_at          timestamptz NOT NULL,
    last_run_applied     integer     NOT NULL DEFAULT 0,
    last_run_failed      integer     NOT NULL DEFAULT 0,
    last_run_duration_ms integer     NOT NULL DEFAULT 0
);


-- ---------------------------------------------------------------------------
-- Discovery
-- ---------------------------------------------------------------------------
-- Rules (design §5.2):
--   1. a meta table has all of meta_task_status, history_object_id, sys_from, sys_to;
--   2. the main table is the meta table minus its "_meta_history" suffix;
--   3. the history table is the main table with "historical" inserted before the LAST
--      underscore (model names never contain "_", app labels may);
--   4. keep the triple only if all three tables exist and the main table has a
--      single-column primary key. Anything else is skipped with a NOTICE.
CREATE OR REPLACE FUNCTION lex_bitemporal_tables()
RETURNS TABLE (main_table text, history_table text, meta_table text, pk_column text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
    v_schema constant text := 'public';
    r record;
    v_main text;
    v_hist text;
    v_pk_count int;
    v_pk text;
BEGIN
    FOR r IN
        SELECT c.table_name::text AS meta_t
          FROM information_schema.columns c
         WHERE c.table_schema = v_schema
           AND c.column_name IN ('meta_task_status', 'history_object_id', 'sys_from', 'sys_to')
         GROUP BY c.table_name
        HAVING count(DISTINCT c.column_name) = 4
         ORDER BY c.table_name
    LOOP
        IF right(r.meta_t, 13) <> '_meta_history' THEN
            RAISE NOTICE 'lex_bitemporal_tables: % has the meta shape but not the meta name; skipped', r.meta_t;
            CONTINUE;
        END IF;
        v_main := left(r.meta_t, length(r.meta_t) - 13);
        v_hist := regexp_replace(v_main, '_([^_]+)$', '_historical\1');

        IF to_regclass(format('%I.%I', v_schema, v_main)) IS NULL THEN
            RAISE NOTICE 'lex_bitemporal_tables: main table % missing for %; skipped', v_main, r.meta_t;
            CONTINUE;
        END IF;
        IF to_regclass(format('%I.%I', v_schema, v_hist)) IS NULL THEN
            RAISE NOTICE 'lex_bitemporal_tables: history table % missing for %; skipped', v_hist, r.meta_t;
            CONTINUE;
        END IF;

        SELECT count(*), min(a.attname::text)
          INTO v_pk_count, v_pk
          FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
         WHERE i.indrelid = to_regclass(format('%I.%I', v_schema, v_main))
           AND i.indisprimary;
        IF v_pk_count <> 1 THEN
            RAISE NOTICE 'lex_bitemporal_tables: % has % primary-key column(s); skipped', v_main, v_pk_count;
            CONTINUE;
        END IF;

        main_table    := v_main;
        history_table := v_hist;
        meta_table    := r.meta_t;
        pk_column     := v_pk;
        RETURN NEXT;
    END LOOP;
END
$fn$;


-- ---------------------------------------------------------------------------
-- Visibility
-- ---------------------------------------------------------------------------
-- Every SCHEDULED meta row, joined to its history row. A meta row whose history row is
-- gone (history_object_id NULL) is listed with NULL pk/valid_from: an orphan the applier
-- will never touch and an operator should see.
CREATE OR REPLACE FUNCTION lex_pending_activations()
RETURNS TABLE (
    main_table       text,
    pk               text,
    history_id       bigint,
    meta_history_id  bigint,
    valid_from       timestamptz,
    due              boolean,
    due_for          interval,
    meta_task_status text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
    t record;
BEGIN
    FOR t IN SELECT * FROM lex_bitemporal_tables() LOOP
        RETURN QUERY EXECUTE format(
            $q$SELECT %1$L::text,
                      h.%2$I::text,
                      h.history_id::bigint,
                      m.meta_history_id::bigint,
                      h.valid_from::timestamptz,
                      (h.valid_from <= now()),
                      GREATEST(now() - h.valid_from, interval '0'),
                      m.meta_task_status::text
                 FROM %3$I m
            LEFT JOIN %4$I h ON h.history_id = m.history_object_id
                WHERE m.meta_task_status = 'SCHEDULED'
                ORDER BY h.valid_from NULLS LAST, m.meta_history_id$q$,
            t.main_table, t.pk_column, t.meta_table, t.history_table);
    END LOOP;
END
$fn$;


-- ---------------------------------------------------------------------------
-- The applier
-- ---------------------------------------------------------------------------
-- Returns the number of records converged in this run. Per record, steps 1-3 run in
-- their own sub-transaction: one failing record raises a WARNING (visible in
-- cron.job_run_details) and the loop continues.
CREATE OR REPLACE FUNCTION lex_apply_due_activations()
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
    v_schema constant text := 'public';
    t record;
    d record;
    e record;
    v_cols text;
    v_set text;
    v_applied int := 0;
    v_failed int := 0;
    v_started timestamptz := clock_timestamp();
    v_err text;
BEGIN
    FOR t IN SELECT * FROM lex_bitemporal_tables() LOOP
        FOR d IN EXECUTE format(
            $q$SELECT DISTINCT h.%1$I AS pk
                 FROM %2$I m
                 JOIN %3$I h ON h.history_id = m.history_object_id
                WHERE m.meta_task_status = 'SCHEDULED'
                  AND h.valid_from <= now()$q$,
            t.pk_column, t.meta_table, t.history_table)
        LOOP
            BEGIN
                -- 1. The history row effective now, locked. Same predicate, same lock
                --    order (history before main) as BitemporalSynchronizer.
                EXECUTE format(
                    $q$SELECT history_id, history_type
                         FROM %1$I
                        WHERE %2$I = $1
                          AND valid_from <= now()
                          AND (valid_to > now() OR valid_to IS NULL)
                        ORDER BY valid_from DESC, history_id DESC
                        LIMIT 1
                          FOR UPDATE$q$,
                    t.history_table, t.pk_column)
                   INTO e USING d.pk;

                IF e.history_id IS NULL OR e.history_type = '-' THEN
                    -- 2a. Nothing is valid now, or the effective row is a deletion:
                    --     the main row must not exist. Mirrors the Python path.
                    EXECUTE format('DELETE FROM %I WHERE %I = $1', t.main_table, t.pk_column)
                      USING d.pk;
                ELSE
                    -- 2b. columns(main) ∩ columns(history), derived at apply time, so a
                    --     migration between save and apply cannot break the copy. The
                    --     history table's own columns fall out: main does not have them.
                    SELECT string_agg(format('%I', c.column_name), ', ' ORDER BY c.ordinal_position),
                           string_agg(format('%1$I = EXCLUDED.%1$I', c.column_name), ', ' ORDER BY c.ordinal_position)
                               FILTER (WHERE c.column_name <> t.pk_column)
                      INTO v_cols, v_set
                      FROM information_schema.columns c
                     WHERE c.table_schema = v_schema
                       AND c.table_name = t.main_table
                       AND EXISTS (SELECT 1
                                     FROM information_schema.columns h
                                    WHERE h.table_schema = v_schema
                                      AND h.table_name = t.history_table
                                      AND h.column_name = c.column_name);
                    IF v_set IS NULL THEN
                        EXECUTE format(
                            'INSERT INTO %1$I (%2$s) SELECT %2$s FROM %3$I WHERE history_id = $1 '
                            'ON CONFLICT (%4$I) DO NOTHING',
                            t.main_table, v_cols, t.history_table, t.pk_column)
                          USING e.history_id;
                    ELSE
                        EXECUTE format(
                            'INSERT INTO %1$I (%2$s) SELECT %2$s FROM %3$I WHERE history_id = $1 '
                            'ON CONFLICT (%4$I) DO UPDATE SET %5$s',
                            t.main_table, v_cols, t.history_table, t.pk_column, v_set)
                          USING e.history_id;
                    END IF;
                END IF;

                -- 3. Flip every due SCHEDULED meta version for this record, in place:
                --    the same UPDATE the Python task issues. No new meta version.
                EXECUTE format(
                    $q$UPDATE %1$I m
                          SET meta_task_status = 'DONE'
                         FROM %2$I h
                        WHERE h.history_id = m.history_object_id
                          AND h.%3$I = $1
                          AND h.valid_from <= now()
                          AND m.meta_task_status = 'SCHEDULED'$q$,
                    t.meta_table, t.history_table, t.pk_column)
                  USING d.pk;

                v_applied := v_applied + 1;
            EXCEPTION WHEN OTHERS THEN
                v_failed := v_failed + 1;
                GET STACKED DIAGNOSTICS v_err = MESSAGE_TEXT;
                RAISE WARNING 'lex_apply_due_activations: % pk % failed: %', t.main_table, d.pk, v_err;
            END;
        END LOOP;
    END LOOP;

    -- Heartbeat. lex-app reads last_run_at at save time to decide whether it still
    -- needs an in-process timer (design §5.8); operators read the rest.
    INSERT INTO lex_activation_applier_state (id, last_run_at, last_run_applied, last_run_failed, last_run_duration_ms)
    VALUES (1, now(), v_applied, v_failed,
            (extract(epoch FROM clock_timestamp() - v_started) * 1000)::int)
    ON CONFLICT (id) DO UPDATE
       SET last_run_at          = EXCLUDED.last_run_at,
           last_run_applied     = EXCLUDED.last_run_applied,
           last_run_failed      = EXCLUDED.last_run_failed,
           last_run_duration_ms = EXCLUDED.last_run_duration_ms;

    RETURN v_applied;
END
$fn$;
