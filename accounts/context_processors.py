from .models import UserProfile


def user_profile_context(request):
    """Add user profile to all template contexts."""
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            profile = None
        return {'user_profile': profile}
    return {'user_profile': None}
