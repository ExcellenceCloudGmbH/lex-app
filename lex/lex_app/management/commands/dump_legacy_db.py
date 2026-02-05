import os
import json
import datetime
import decimal
import uuid
from django.db import connection
from django.conf import settings
import django

from django.core.management.base import BaseCommand
# # Setup Django standalone
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
# django.setup()


class DjangoJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        return super().default(o)
class Command(BaseCommand):
    help = "Scan pending model-level changes (add/delete/rename) before writing migrations."

    def handle(self, *args, **opts):
        output_path = "legacy_v1_full_dump.json"
        dump_database(output_path)


def dump_database(output_file):
    data = {}
    with connection.cursor() as cursor:
        # Get all tables in public schema
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        print(f"Found {len(tables)} tables. Starting dump...")
        
        for table in tables:
            print(f"Dumping {table}...")
            cursor.execute(f'SELECT * FROM "{table}"')
            columns = [col[0] for col in cursor.description]
            rows = []
            for row in cursor.fetchall():
                record = dict(zip(columns, row))
                rows.append(record)
            data[table] = rows

    with open(output_file, 'w') as f:
        json.dump(data, f, cls=DjangoJSONEncoder, indent=2)
    
    print(f"✅ Database dumped to {output_file} ({len(data)} tables)")

# if __name__ == "__main__":
#     output_path = "legacy_v1_full_dump.json"
#     dump_database(output_path)
