from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard_view, name='index'),
    path('api/drafts/save/', views.api_save_draft, name='api_save_draft'),
    path('api/drafts/get/', views.api_get_draft, name='api_get_draft'),
    path('api/drafts/cancel/', views.api_cancel_draft, name='api_cancel_draft'),
    path('api/notifications/', views.api_get_notifications, name='api_get_notifications'),
    path('api/notifications/clear/', views.api_clear_notifications, name='api_clear_notifications'),
]
