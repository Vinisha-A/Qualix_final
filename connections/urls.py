from django.urls import path
from . import views

app_name = 'connections'

urlpatterns = [
    path('', views.connection_list_view, name='list'),
    path('create/', views.connection_create_view, name='create'),
    path('edit/<int:conn_id>/', views.connection_edit_view, name='edit'),
    path('delete/<int:conn_id>/', views.connection_delete_view, name='delete'),
    # API endpoints
    path('api/test/<int:conn_id>/', views.api_test_connection, name='api_test'),
    path('api/catalogs/', views.api_get_catalogs, name='api_catalogs'),
    path('api/schemas/', views.api_get_schemas, name='api_schemas'),
    path('api/tables/', views.api_get_tables, name='api_tables'),
    path('api/columns/', views.api_get_columns, name='api_columns'),
    path('preview/source/', views.preview_source_view, name='preview_source'),
    path('preview/target/', views.preview_target_view, name='preview_target'),
]
