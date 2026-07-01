from django.urls import path
from . import views

app_name = 'logs'

urlpatterns = [
    path('', views.log_list_view, name='list'),
]
