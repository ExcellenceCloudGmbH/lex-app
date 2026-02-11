from django.test import TestCase
from django.conf import settings
from django.db import connection


class create_db(TestCase):

    def test(self):
        # Print the actual DB name being used
        db_name = settings.DATABASES['default']['NAME']
        print(f"\n\n--> ACTUAL DB NAME CREATED: {db_name}\n")

        # Or check the connection properties directly
        print(f"--> CONNECTION DB NAME: {connection.settings_dict['NAME']}\n")
        pass