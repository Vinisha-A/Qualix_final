from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def role_required(*roles):
    """Decorator to restrict view access to specific roles."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            profile = getattr(request.user, 'profile', None)
            if profile is None or profile.role not in roles:
                messages.error(request, 'You do not have permission to access this page.')
                return redirect('dashboard:index')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def admin_required(view_func):
    """Shortcut decorator for admin-only views."""
    return role_required('admin')(view_func)


def contributor_or_admin_required(view_func):
    """Shortcut decorator for contributor or admin views."""
    return role_required('admin', 'contributor')(view_func)
