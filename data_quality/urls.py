"""
Root URL configuration for data_quality project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from dashboard import views as dashboard_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('connections/', include('connections.urls')),
    path('mappings/', include('mappings.urls')),
    path('validations/', include('validations.urls')),
    path('workflows/', include('workflows.urls')),
    path('logs/', include('logs.urls')),
    
    # Help Center
    path('help/', dashboard_views.help_documentation_view, name='help_index'),
    path('help/<str:category>/', dashboard_views.help_documentation_view, name='help_category'),

    # Root redirect to dashboard
    path('', lambda request: __import__('django.shortcuts', fromlist=['redirect']).redirect('/accounts/login/')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

