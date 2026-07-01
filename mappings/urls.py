from django.urls import path
from . import views

app_name = 'mappings'

urlpatterns = [
    path('', views.mapping_list_view, name='list'),
    path('create/', views.mapping_create_view, name='create'),
    path('<int:mapping_id>/', views.mapping_detail_view, name='detail'),
    path('<int:mapping_id>/edit/', views.mapping_edit_view, name='edit'),
    path('<int:mapping_id>/delete/', views.mapping_delete_view, name='delete'),
    path('api/columns/<int:mapping_id>/', views.api_mapping_columns, name='api_columns'),
]
