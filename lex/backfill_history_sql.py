#!/usr/bin/env python3
"""
Direct History Backfill using Raw SQL (Dynamic)

1. Discovers all ArmiraCashflowDB tables.
2. Infers History (suffix 'historical') and MetaHistory (suffix '_meta_history') tables.
3. Backfills Main -> History (Level 1) with valid_from=NOW() IF NOT EXISTS.
4. Backfills History -> MetaHistory (Level 2) with sys_from=NOW() IF NOT EXISTS.
"""
import psycopg2
import os
import sys

# DATABASE_DEPLOYMENT_TARGET=default should be set
DB = os.environ.get("DB_NAME", "db_armiracashflowdb")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "postgres")
DB_HOST = os.environ.get("DB_HOST", "localhost")

def backfill_history():
    print("=" * 60)
    print("🚀 Dynamic Bitemporal History Backfill v2")
    print("=" * 60)
    
    try:
        conn = psycopg2.connect(dbname=DB, user=DB_USER, password=DB_PASS, host=DB_HOST)
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("Ensure DATABASE_DEPLOYMENT_TARGET=default context or DB creds are set.")
        sys.exit(1)

    cursor = conn.cursor()
    
    # 1. Discover Live Tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
          AND table_name LIKE 'ArmiraCashflowDB_%' 
          AND table_name NOT LIKE '%historical%'
          AND table_name NOT LIKE '%_meta_history%'
    """)
    live_tables = [r[0] for r in cursor.fetchall()]
    
    total_l1 = 0
    total_l2 = 0
    
    for live_table in live_tables:
        # Construct History Table Name
        # Logic: ArmiraCashflowDB_suffix -> ArmiraCashflowDB_historicalsuffix
        prefix = "ArmiraCashflowDB_"
        suffix = live_table[len(prefix):]
        history_table = f"{prefix}historical{suffix}"
        # Correct Meta Table Name: It seems to correspond to the LIVE table name + _meta_history,
        # NOT the history table name.
        # Based on: ArmiraCashflowDB_investor -> ArmiraCashflowDB_investor_meta_history
        meta_table = f"{live_table}_meta_history"
        
        # Check if History Tables Exist
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s
            ), EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s
            )
        """, (history_table, meta_table))
        
        hist_exists, meta_exists = cursor.fetchone()
        
        if not hist_exists:
            continue
            
        print(f"\nProcessing {live_table}...")
        
        try:
            # --- L1 Backfill (Main -> History) ---
            cursor.execute(f'SELECT COUNT(*) FROM "{history_table}"')
            l1_count = cursor.fetchone()[0]
            
            if l1_count > 0:
                print(f"  ⏭️  Skipping L1: {history_table} is not empty ({l1_count} rows).")
            else:
                # Get columns
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (live_table,))
                live_cols = {r[0] for r in cursor.fetchall()}
                
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (history_table,))
                hist_cols = {r[0] for r in cursor.fetchall()}
                
                # Intersect columns
                common_cols = list(live_cols.intersection(hist_cols))
                # history_id is auto-generated in history table
                common_cols = [c for c in common_cols if c not in ('history_id', 'valid_from', 'valid_to', 'history_type', 'history_change_reason', 'history_user_id')]
                
                if common_cols:
                    cols_sql = ', '.join([f'"{c}"' for c in common_cols])
                    
                    insert_l1 = f'''
                        INSERT INTO "{history_table}" 
                        ({cols_sql}, "valid_from", "valid_to", "history_type", "history_change_reason")
                        SELECT {cols_sql}, NOW(), NULL, '+', 'V1 Migration'
                        FROM "{live_table}"
                    '''
                    cursor.execute(insert_l1)
                    count_l1 = cursor.rowcount
                    total_l1 += count_l1
                    print(f"  ✅ Backfilled L1: {count_l1} rows")
                else:
                    print("  ⚠️  No common columns for L1.")

            # --- L2 Backfill (History -> MetaHistory) ---
            if meta_exists:
                # Check coverage. Does Meta have fewer rows than History?
                # Actually, strictly 1:1 for the initial snapshot.
                # Query: Insert into Meta records from History that don't have a Meta record yet.
                
                # Meta columns
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (meta_table,))
                meta_cols = {r[0] for r in cursor.fetchall()}
                
                # History columns (source for Meta data)
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (history_table,))
                hist_cols = {r[0] for r in cursor.fetchall()}
                
                # history_id is likely a data field in Meta (copy of L1 PK), so we should keep it.
                # history_object_id is the FK to L1.
                
                common_meta_cols = list(hist_cols.intersection(meta_cols))
                
                # we exclude L2 specific control fields.
                common_meta_cols = [c for c in common_meta_cols if c not in ('sys_from', 'sys_to', 'meta_history_type', 'meta_history_change_reason', 'history_object_id', 'meta_history_id')]
                
                print(f"  [DEBUG] Common Meta Cols: {common_meta_cols}")
                
                if common_meta_cols:
                    meta_cols_sql = ', '.join([f'"{c}"' for c in common_meta_cols])
                    print(f"  [DEBUG] Meta SQL: INSERT INTO {meta_table} ({meta_cols_sql}, ...)")
                    
                    # "history_object_id" in Meta maps to "history_id" in History
                    # We also need to populate required meta fields like meta_task_status
                    insert_l2 = f'''
                        INSERT INTO "{meta_table}"
                        ({meta_cols_sql}, "history_object_id", "sys_from", "sys_to", "meta_history_type", "meta_history_change_reason", "meta_task_status", "meta_task_name")
                        SELECT {meta_cols_sql}, "history_id", NOW(), NULL, '+', 'V1 Migration', 'DONE', 'V1 Migration - ' || "history_id"::text
                        FROM "{history_table}" H
                        WHERE NOT EXISTS (
                            SELECT 1 FROM "{meta_table}" M
                            WHERE M."history_object_id" = H."history_id"
                        )
                    '''
                    
                    cursor.execute(insert_l2)
                    count_l2 = cursor.rowcount
                    if count_l2 > 0:
                        total_l2 += count_l2
                        print(f"  ✅ Backfilled L2: {count_l2} rows")
                    else:
                        print(f"  ⏭️  Skipping L2: {meta_table} appears up-to-date.")

        except Exception as e:
            conn.rollback()
            print(f"  ❌ Error: {e}")
            continue
        
        conn.commit()

    conn.close()
    print("=" * 60)
    print(f"DONE. Total L1: {total_l1}, Total L2: {total_l2}")

if __name__ == "__main__":
    backfill_history()
