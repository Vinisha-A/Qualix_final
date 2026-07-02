from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from .models import Notification

class NotificationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        
    def test_notification_creation(self):
        notif = Notification.objects.create(
            user=self.user,
            title='Test Notification',
            message='This is a test notification message.',
            level='info'
        )
        self.assertEqual(notif.user, self.user)
        self.assertEqual(notif.title, 'Test Notification')
        self.assertEqual(notif.level, 'info')
        self.assertFalse(notif.is_read)

    def test_api_get_notifications(self):
        self.client.login(username='testuser', password='password123')
        
        # Create unread notification
        Notification.objects.create(
            user=self.user,
            title='Unread Notif',
            message='Unread msg',
            level='success',
            is_read=False
        )
        
        response = self.client.get(reverse('dashboard:api_get_notifications'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['unread_count'], 1)
        self.assertEqual(len(data['notifications']), 1)
        self.assertEqual(data['notifications'][0]['title'], 'Unread Notif')

    def test_api_clear_notifications(self):
        self.client.login(username='testuser', password='password123')
        
        # Create unread notification
        Notification.objects.create(
            user=self.user,
            title='To Clear',
            message='Clear msg',
            level='warning',
            is_read=False
        )
        
        # Retrieve and verify unread count is 1
        response = self.client.get(reverse('dashboard:api_get_notifications'))
        self.assertEqual(response.json()['unread_count'], 1)
        
        # Clear notifications (must be a POST request)
        clear_response = self.client.post(reverse('dashboard:api_clear_notifications'))
        self.assertEqual(clear_response.status_code, 200)
        self.assertTrue(clear_response.json()['success'])
        
        # Retrieve and verify unread count is now 0, and notifications list is empty
        response = self.client.get(reverse('dashboard:api_get_notifications'))
        self.assertEqual(response.json()['unread_count'], 0)
        self.assertEqual(len(response.json()['notifications']), 0)


class HelpCenterTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='helpuser', password='password123')

    def test_unauthenticated_redirect(self):
        response = self.client.get(reverse('help_index'))
        self.assertEqual(response.status_code, 302) # Redirect to login

    def test_authenticated_help_index(self):
        self.client.login(username='helpuser', password='password123')
        response = self.client.get(reverse('help_index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "String Operations Documentation")
        self.assertContains(response, "null_check")

    def test_authenticated_help_category(self):
        self.client.login(username='helpuser', password='password123')
        response = self.client.get(reverse('help_category', kwargs={'category': 'float'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Float Operations Documentation")
        self.assertContains(response, "precision_check")

    def test_help_search_filtering(self):
        self.client.login(username='helpuser', password='password123')
        # Search match
        response = self.client.get(reverse('help_index') + "?query=Null")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Null Check")
        
        # Search mismatch (no results)
        response_empty = self.client.get(reverse('help_index') + "?query=xyz_nonexistent_search_query")
        self.assertEqual(response_empty.status_code, 200)
        self.assertContains(response_empty, "No operations matched your search")

