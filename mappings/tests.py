from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from connections.models import DataConnection
from mappings.models import Mapping
from accounts.models import UserProfile

class MappingSearchTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        profile, created = UserProfile.objects.get_or_create(user=self.user)
        profile.role = 'contributor'
        profile.save()
        
        self.client.login(username='testuser', password='password123')
        
        self.conn1 = DataConnection.objects.create(
            name='Sales DB',
            connection_type='postgresql',
            host='sales-host',
            database_name='sales_db',
            created_by=self.user
        )
        self.conn2 = DataConnection.objects.create(
            name='Analytics Warehouse',
            connection_type='postgresql',
            host='analytics-host',
            database_name='analytics_db',
            created_by=self.user
        )
        
        self.mapping1 = Mapping.objects.create(
            name='Sales Daily Sync',
            description='Daily pipeline for syncing sales data',
            source_connection=self.conn1,
            source_table='orders',
            target_connection=self.conn2,
            target_table='f_orders',
            created_by=self.user
        )
        
        self.mapping2 = Mapping.objects.create(
            name='Customer Profiles',
            description='Customer CRM extraction pipeline',
            source_connection=self.conn1,
            source_table='customers',
            target_connection=self.conn2,
            target_table='dim_customers',
            created_by=self.user
        )

    def test_list_all_mappings(self):
        response = self.client.get(reverse('mappings:list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 2)

    def test_search_by_name(self):
        response = self.client.get(reverse('mappings:list'), {'query': 'Sync'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 1)
        self.assertEqual(response.context['mappings'][0].name, 'Sales Daily Sync')

    def test_search_by_description(self):
        response = self.client.get(reverse('mappings:list'), {'query': 'CRM'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 1)
        self.assertEqual(response.context['mappings'][0].name, 'Customer Profiles')

    def test_search_by_source_table(self):
        response = self.client.get(reverse('mappings:list'), {'query': 'orders'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 1)
        self.assertEqual(response.context['mappings'][0].name, 'Sales Daily Sync')

    def test_search_by_connection_name(self):
        response = self.client.get(reverse('mappings:list'), {'query': 'Warehouse'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 2)

    def test_search_no_results(self):
        response = self.client.get(reverse('mappings:list'), {'query': 'NonexistentPipeline'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['mappings']), 0)
