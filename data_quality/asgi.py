"""
ASGI config for data_quality project.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'data_quality.settings')
application = get_asgi_application()
