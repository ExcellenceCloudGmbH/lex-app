import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from django.conf import settings
import django

# Setup Django to get DB config
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
django.setup()

def recreate_db():
    db_config = settings.DATABASES['default']
    db_name = db_config['NAME']
    
    # Connect to 'postgres' system DB to drop/create the target DB
    # Assuming same credentials work for 'postgres' db or we fallback to env vars
    conn_params = {
        'dbname': 'postgres',
        'user': db_config.get('USER', 'postgres'),
        'password': db_config.get('PASSWORD', ''),
        'host': db_config.get('HOST', 'localhost'),
        'port': db_config.get('PORT', '5432'),
    }

    print(f"🔌 Connecting to system DB to recreate '{db_name}'...")
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        print(f"🔥 Dropping database '{db_name}'...")
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        
        print(f"✨ Creating database '{db_name}'...")
        cur.execute(f'CREATE DATABASE "{db_name}"')
        
        cur.close()
        conn.close()
        print("✅ Database recreated successfully.")
    except Exception as e:
        print(f"❌ Error recreating database: {e}")
        exit(1)

if __name__ == "__main__":
    recreate_db()
