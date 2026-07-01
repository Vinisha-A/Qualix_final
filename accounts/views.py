import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import UserProfile
from .forms import LoginForm, AddUserForm
from .decorators import admin_required

logger = logging.getLogger(__name__)


def login_view(request):
    """Login page — identifies user role and shows/hides forgot password."""
    if request.user.is_authenticated:
        return redirect('dashboard:index')

    # SSO Login simulation
    if request.GET.get('sso') == '1':
        ad_user = request.GET.get('ad_user')
        if not ad_user:
            return render(request, 'accounts/sso_select.html')
            
        try:
            user = User.objects.get(username=ad_user)
            login(request, user)
            logger.info(f"User '{user.username}' logged in via SSO simulation.")
            
            # Log the login
            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=user,
                    action=f'User Login (SSO: {ad_user})',
                    entity_type='User',
                    entity_id=user.id,
                    details={'method': 'sso', 'ad_user': ad_user},
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='info',
                )
            except Exception:
                pass
            
            next_url = request.GET.get('next', 'dashboard:index')
            return redirect(next_url)
        except User.DoesNotExist:
            messages.error(request, f'SSO simulation failed: AD user {ad_user} does not exist.')

    form = LoginForm()

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']

            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                logger.info(f"User '{username}' logged in successfully.")

                # Log the login
                try:
                    from logs.models import AuditLog
                    AuditLog.objects.create(
                        user=user,
                        action='User Login',
                        entity_type='User',
                        entity_id=user.id,
                        details={'method': 'form'},
                        ip_address=request.META.get('REMOTE_ADDR'),
                        level='info',
                    )
                except Exception:
                    pass

                next_url = request.GET.get('next', 'dashboard:index')
                return redirect(next_url)
            else:
                messages.error(request, 'Invalid username or password.')
                logger.warning(f"Failed login attempt for username '{username}'.")
        else:
            messages.error(request, 'Please correct the errors below.')

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    """Logout and redirect to login page."""
    if request.user.is_authenticated:
        logger.info(f"User '{request.user.username}' logged out.")
        try:
            from logs.models import AuditLog
            AuditLog.objects.create(
                user=request.user,
                action='User Logout',
                entity_type='User',
                entity_id=request.user.id,
                ip_address=request.META.get('REMOTE_ADDR'),
                level='info',
            )
        except Exception:
            pass
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('accounts:login')


@require_GET
def check_role_view(request):
    """AJAX endpoint: check if a username belongs to an admin (to show forgot password)."""
    username = request.GET.get('username', '')
    try:
        user = User.objects.get(username=username)
        profile = getattr(user, 'profile', None)
        role = profile.role if profile else 'unknown'
    except User.DoesNotExist:
        role = 'unknown'

    return JsonResponse({'role': role})


@login_required
@admin_required
def add_user_view(request):
    """Admin-only: Add a new user."""
    form = AddUserForm()

    if request.method == 'POST':
        form = AddUserForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password'],
                first_name=form.cleaned_data['first_name'],
                last_name=form.cleaned_data.get('last_name', ''),
            )

            # Update profile (auto-created by signal)
            profile = user.profile
            profile.role = form.cleaned_data['role']
            profile.department = form.cleaned_data.get('department', '')
            profile.employee_id = form.cleaned_data.get('employee_id', '')
            profile.created_by = request.user
            profile.save()

            logger.info(f"Admin '{request.user.username}' created user '{user.username}' with role '{profile.role}'.")

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'Created User: {user.username}',
                    entity_type='User',
                    entity_id=user.id,
                    details={
                        'role': profile.role,
                        'email': user.email,
                        'department': profile.department,
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='info',
                )
            except Exception:
                pass

            messages.success(request, f'User "{user.username}" created successfully with role "{profile.get_role_display()}".')
            return redirect('accounts:manage_users')

    return render(request, 'accounts/add_user.html', {'form': form})


@login_required
@admin_required
def manage_users_view(request):
    """Admin-only: List and manage all users."""
    profiles = UserProfile.objects.select_related('user', 'created_by').all()
    return render(request, 'accounts/manage_users.html', {'profiles': profiles})


@login_required
@admin_required
def toggle_user_view(request, user_id):
    """Admin-only: Activate/deactivate a user."""
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        if target_user == request.user:
            messages.error(request, 'You cannot deactivate your own account.')
        else:
            target_user.is_active = not target_user.is_active
            target_user.save()
            status = 'activated' if target_user.is_active else 'deactivated'
            messages.success(request, f'User "{target_user.username}" has been {status}.')

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'User {status}: {target_user.username}',
                    entity_type='User',
                    entity_id=target_user.id,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='warning',
                )
            except Exception:
                pass

    return redirect('accounts:manage_users')


@login_required
@admin_required
def delete_user_view(request, user_id):
    """Admin-only: Delete a user."""
    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        if target_user == request.user:
            messages.error(request, 'You cannot delete your own account.')
        else:
            username = target_user.username
            target_user.delete()
            messages.success(request, f'User "{username}" has been deleted successfully.')

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'User Deleted: {username}',
                    entity_type='User',
                    entity_id=user_id,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='warning',
                )
            except Exception:
                pass

    return redirect('accounts:manage_users')

def forgot_password_view(request):
    """Password reset request (authorized by Admin)."""
    users = User.objects.all().order_by('username')
    
    if request.method == 'POST':
        admin_username = request.POST.get('admin_username', '').strip()
        admin_password = request.POST.get('admin_password', '').strip()
        target_username = request.POST.get('target_username', '').strip()
        new_password = request.POST.get('new_password', '').strip()

        # Authenticate admin
        admin_user = authenticate(request, username=admin_username, password=admin_password)
        if admin_user is not None:
            profile = getattr(admin_user, 'profile', None)
            is_admin = (profile and profile.role == 'admin') or admin_user.is_superuser
            if is_admin:
                try:
                    target_user = User.objects.get(username=target_username)
                    target_user.set_password(new_password)
                    target_user.save()
                    messages.success(request, f"Password for user '{target_username}' has been successfully reset.")
                    
                    # Log the reset
                    try:
                        from logs.models import AuditLog
                        AuditLog.objects.create(
                            user=admin_user,
                            action=f'Password Reset: {target_username}',
                            entity_type='User',
                            entity_id=target_user.id,
                            details={'reset_by': admin_username},
                            ip_address=request.META.get('REMOTE_ADDR'),
                            level='warning',
                        )
                    except Exception:
                        pass
                except User.DoesNotExist:
                    messages.error(request, f"User '{target_username}' not found.")
            else:
                messages.error(request, 'Authorized user is not an Administrator.')
        else:
            messages.error(request, 'Invalid Admin credentials.')

        return redirect('accounts:forgot_password')
    return render(request, 'accounts/forgot_password.html', {'users': users})


@login_required
@admin_required
def update_role_view(request, user_id):
    """Admin-only: Update a user's role."""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            new_role = data.get('role')
        except Exception:
            new_role = request.POST.get('role')
            
        if new_role not in ('admin', 'contributor', 'auditor'):
            return JsonResponse({'success': False, 'error': 'Invalid role'}, status=400)
            
        target_user = get_object_or_404(User, id=user_id)
        if target_user == request.user:
            return JsonResponse({'success': False, 'error': 'You cannot change your own role.'}, status=400)
            
        profile = target_user.profile
        old_role = profile.role
        profile.role = new_role
        profile.save()
        
        logger.info(f"Admin '{request.user.username}' updated role of '{target_user.username}' from '{old_role}' to '{new_role}'.")
        
        try:
            from logs.models import AuditLog
            AuditLog.objects.create(
                user=request.user,
                action=f"Updated User Role: {target_user.username}",
                entity_type='User',
                entity_id=target_user.id,
                details={'old_role': old_role, 'new_role': new_role},
                ip_address=request.META.get('REMOTE_ADDR'),
                level='warning',
            )
        except Exception:
            pass
            
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json' or 'application/json' in request.META.get('CONTENT_TYPE', ''):
            return JsonResponse({'success': True, 'message': f"Role for user '{target_user.username}' updated successfully."})
            
        messages.success(request, f"Role for user '{target_user.username}' updated successfully.")
        return redirect('accounts:manage_users')
        
    return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)
