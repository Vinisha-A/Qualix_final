from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('check-role/', views.check_role_view, name='check_role'),
    path('add-user/', views.add_user_view, name='add_user'),
    path('manage-users/', views.manage_users_view, name='manage_users'),
    path('toggle-user/<int:user_id>/', views.toggle_user_view, name='toggle_user'),
    path('delete-user/<int:user_id>/', views.delete_user_view, name='delete_user'),
    path('update-role/<int:user_id>/', views.update_role_view, name='update_role'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),
]
