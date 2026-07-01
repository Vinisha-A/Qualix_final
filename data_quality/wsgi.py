"""
WSGI config for data_quality project.
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'data_quality.settings')
application = get_wsgi_application()
