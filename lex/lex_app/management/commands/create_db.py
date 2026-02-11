import os
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = 'Creates the database defined in settings if it does not exist (PostgreSQL only)'

    def handle(self, *args, **options):
        # 1. Get the active default database configuration
        db_conf = settings.DATABASES['default']
        engine = db_conf['ENGINE']
        target_db_name = db_conf['NAME']

        self.stdout.write(f"Detected Engine: {engine}")
        self.stdout.write(f"Target Database: {target_db_name}")

        # ==========================================
        # CASE A: SQLite (Local Development)
        # ==========================================
        if 'sqlite3' in engine:
            self.stdout.write(self.style.SUCCESS(
                f"SQLite detected. The database file '{target_db_name}' will be created automatically "
                "when you run migrations. No action needed."
            ))
            # Optional: Ensure the directory exists
            db_dir = os.path.dirname(target_db_name)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)
                self.stdout.write(f"Created directory: {db_dir}")
            return

        # ==========================================
        # CASE B: PostgreSQL (Docker, K8S, GCP)
        # ==========================================
        elif 'postgresql' in engine:
            try:
                import psycopg2
                from psycopg2 import sql
            except ImportError:
                self.stdout.write(self.style.ERROR("Error: 'psycopg2' module not found. Please install it to use PostgreSQL."))
                return

            # Get connection details, falling back to empty strings if not set
            db_user = db_conf.get('USER', '')
            db_password = db_conf.get('PASSWORD', '')
            db_host = db_conf.get('HOST', 'localhost')
            db_port = db_conf.get('PORT', '5432')

            self.stdout.write(f"Connecting to 'postgres' system db on {db_host}:{db_port}...")

            try:
                # Connect to the system 'postgres' database to create the new DB
                conn = psycopg2.connect(
                    dbname='postgres',
                    user=db_user,
                    password=db_password,
                    host=db_host,
                    port=db_port
                )
                conn.autocommit = True

                with conn.cursor() as cursor:
                    # Check if DB exists
                    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [target_db_name])
                    exists = cursor.fetchone()

                    if not exists:
                        self.stdout.write(f"Creating database '{target_db_name}'...")
                        # Use sql.Identifier for safe quoting of the DB name
                        cursor.execute(sql.SQL("CREATE DATABASE {}").format(
                            sql.Identifier(target_db_name)
                        ))
                        self.stdout.write(self.style.SUCCESS(f"Successfully created database '{target_db_name}'"))
                    else:
                        self.stdout.write(self.style.WARNING(f"Database '{target_db_name}' already exists."))

                conn.close()

            except psycopg2.OperationalError as e:
                self.stdout.write(self.style.ERROR(f"Operational Error: {e}"))
                self.stdout.write(self.style.ERROR("Check your HOST, USER, and PASSWORD settings."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Unexpected Error: {e}"))

        # ==========================================
        # CASE C: Unknown / Other
        # ==========================================
        else:
            self.stdout.write(self.style.WARNING(f"Unknown database engine: {engine}. Skipping creation."))