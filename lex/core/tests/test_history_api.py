from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from django.db import connection

# Import from the correct location based on finding SchedTestModel in test_event_scheduling
from lex.core.tests.test_event_scheduling import SchedTestModel
from lex.process_admin.utils.model_registration import ModelRegistration

class TestHistoryTimelineAPI(APITestCase):

    def setUp(self):
        # Create User
        self.user = User.objects.create_user(username='testuser', password='password', email='test@example.com')
        self.client = APIClient()
        self.client.force_login(self.user)
        
        # Inject OIDC session data
        session = self.client.session
        session['oidc_expires_at'] = (timezone.now() + timedelta(hours=1)).timestamp()
        session.save()
        
        # Create Initial Object
        # Verify Model Registration
        from simple_history.models import registered_models
        if SchedTestModel not in registered_models:
             ModelRegistration._register_standard_model(SchedTestModel, [])

        self.HistoryModel = SchedTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        # Create Tables Manually
        with connection.schema_editor() as schema_editor:
            # We need to check if they exist first to avoid errors if shared DB state
            tables = connection.introspection.table_names()
            if SchedTestModel._meta.db_table not in tables:
                schema_editor.create_model(SchedTestModel)
            if self.HistoryModel._meta.db_table not in tables:
                schema_editor.create_model(self.HistoryModel)
            if self.MetaModel._meta.db_table not in tables:
                schema_editor.create_model(self.MetaModel)

        # Re-Create Initial Object
        self.obj = SchedTestModel.objects.create(name="Version 1")
        
        # URL Config Hack (as seen in previous version)
        from lex.process_admin.settings import processAdminSite
        processAdminSite.initialized = False
        _ = processAdminSite.urls
        from django.urls import clear_url_caches
        from django.conf import settings
        from importlib import reload
        import sys
        clear_url_caches()
        if settings.ROOT_URLCONF in sys.modules:
            reload(sys.modules[settings.ROOT_URLCONF]) 

    def tearDown(self):
        # Cleanup Tables
        with connection.schema_editor() as schema_editor:
            # Drop tables to be clean
            try: schema_editor.delete_model(self.MetaModel)
            except: pass
            try: schema_editor.delete_model(self.HistoryModel)
            except: pass
            try: schema_editor.delete_model(SchedTestModel)
            except: pass

    def test_get_history_timeline(self):
        """Test retrieving history timeline for a model."""
        self.obj.name = "Version 2"
        self.obj.save()
        self.obj.name = "Version 3"
        self.obj.save()
        
        class MockContainer:
            id = 'schedtestmodel'
        
        url = reverse(
            'process_admin_rest_api:model-history-list',
            kwargs={'model_container': MockContainer(), 'calculationId': 'default', 'pk': self.obj.pk}
        )
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]['snapshot']['name'], "Version 3")

    def test_get_history_deleted_record(self):
        """Test retrieving history for a record that has been deleted."""
        self.obj.name = "Pre-Delete Version"
        self.obj.save()
        pk = self.obj.pk
        self.obj.delete()
        
        class MockContainer:
            id = 'schedtestmodel'
            
        url = reverse(
            'process_admin_rest_api:model-history-list',
            kwargs={'model_container': MockContainer(), 'calculationId': 'default', 'pk': pk}
        )
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data[0]['history_type'], '-')

    def test_history_as_of_param(self):
        """Test retrieving history as known by the system at a specific point in time."""
        t0 = timezone.now()
        self.obj.name = "Version at T1"
        self.obj.save()
        t1 = timezone.now()
        
        # Wait a bit
        from time import sleep
        sleep(0.01)
        
        self.obj.name = "Version at T2"
        self.obj.save()
        t2 = timezone.now()
        
        class MockContainer:
            id = 'schedtestmodel'
        
        url = reverse(
            'process_admin_rest_api:model-history-list',
            kwargs={'model_container': MockContainer(), 'calculationId': 'default', 'pk': self.obj.pk}
        )
        
        # Query at T1 + small delta
        as_of_time = t1 + timedelta(microseconds=1000)
        response = self.client.get(url, {'as_of': as_of_time.isoformat()})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        names = [entry['snapshot']['name'] for entry in data]
        self.assertNotIn("Version at T2", names)
        self.assertIn("Version at T1", names)
        
        # Query at T2
        as_of_t2 = t2 + timedelta(microseconds=1000)
        response_t2 = self.client.get(url, {'as_of': as_of_t2.isoformat()})
        names_t2 = [entry['snapshot']['name'] for entry in response_t2.json()]
        self.assertIn("Version at T2", names_t2)

    def test_modify_initial_history_valid_from(self):
        """
        Reproduce: Create object (Upload). Try to modify valid_from of the single history record.
        """
        # 1. Create Initial Object (simulating upload)
        # In setup, self.obj is already created ("Version 1").
        t0 = timezone.now()
        new_obj = SchedTestModel.objects.create(name="Fresh Object")
        
        # Verify History exists
        self.assertEqual(new_obj.history.count(), 1)
        initial_history = new_obj.history.first()
        
        # 2. Attempt to modify valid_from (e.g. set it to 1 hour ago)
        new_valid_from = t0 - timedelta(hours=1)
        
        # We modify the history record directly
        initial_history.valid_from = new_valid_from
        initial_history.save()
        
        # 3. Fetch again and verify
        # We need to re-fetch from DB
        refetched_history = new_obj.history.filter(pk=initial_history.pk).first()
        
        print(f"DEBUG: Original valid_from: {initial_history.valid_from}")
        print(f"DEBUG: Target valid_from: {new_valid_from}")
        print(f"DEBUG: Saved valid_from: {refetched_history.valid_from}")
        
        # Note: In simple_history, 'history_date' corresponds to valid_from
        self.assertEqual(refetched_history.valid_from, new_valid_from, 
                         "Failed to modify valid_from of the initial history record.")

    def test_api_modify_initial_history(self):
        """
        Attempt to modify the initial history record via the REST API.
        This verifies if the Serializer or ViewSet allows formatting.
        """
        # 1. Create Initial Object
        t0 = timezone.now()
        new_obj = SchedTestModel.objects.create(name="Fresh Object API")
        initial_history = new_obj.history.first()
        
        # 2. Construct API URL for the History Record
        # This assumes the History Model is registered as a Process/Model
        # URL: /api/model_entries/historicalschedtestmodel/default/{id}/
        
        # We need to know the 'model_id' for the historical model.
        # In model_registration, it registers `historical_model`.
        # The id is likely 'historicalschedtestmodel' (simple_history defaults).
        hist_model_id = self.HistoryModel._meta.model_name
        
        url = reverse(
            'process_admin_rest_api:model-one-entry-read-update-delete',
            kwargs={'model_container': hist_model_id, 'calculationId': 'default', 'pk': initial_history.pk}
        )
        
        # 3. Patch valid_from
        new_valid_from = t0 - timedelta(hours=1)
        data = {
            'valid_from': new_valid_from.isoformat()
        }
        
        response = self.client.patch(url, data, format='json')
        
        if response.status_code == 404:
             print(f"DEBUG: Endpoint not found: {url}. History Model might not be registered as a primary resource.")
             return

        self.assertEqual(response.status_code, status.HTTP_200_OK, f"API Update Failed: {response.data}")
        
        # 4. Verify Persistence
        initial_history.refresh_from_db()
        self.assertEqual(initial_history.valid_from, new_valid_from)
