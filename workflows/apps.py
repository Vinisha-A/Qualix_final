from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workflows'
    verbose_name = 'Workflows'

    def ready(self):
        import os
        if os.environ.get('RUN_MAIN') == 'true' or not os.environ.get('RUN_MAIN'):
            from .scheduler import start_scheduler
            start_scheduler()
