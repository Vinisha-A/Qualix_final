from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import AuditLog


@login_required
def log_list_view(request):
    """Display audit logs with filtering."""
    level = request.GET.get('level', '')
    action_filter = request.GET.get('action', '')

    logs = AuditLog.objects.select_related('user').all()

    if level:
        logs = logs.filter(level=level)

    if action_filter:
        if action_filter == 'User Login':
            logs = logs.filter(action__icontains='User Login')
        elif action_filter == 'User Logout':
            logs = logs.filter(action__icontains='User Logout')
        elif action_filter == 'User Created':
            logs = logs.filter(Q(action__icontains='User Created') | Q(action__icontains='Created User'))
        elif action_filter == 'Data Source Created':
            logs = logs.filter(Q(action__icontains='Created Connection') | Q(action__icontains='Data Source Created'))
        elif action_filter == 'Mapping Created':
            logs = logs.filter(Q(action__icontains='Created Mapping') | Q(action__icontains='Mapping Created'))
        elif action_filter == 'Mapping Deleted':
            logs = logs.filter(Q(action__icontains='Deleted Mapping') | Q(action__icontains='Mapping Deleted'))
        elif action_filter == 'Validation Run':
            logs = logs.filter(Q(action__icontains='Validation Run') | Q(action__icontains='Run Validation') | Q(action__icontains='Started Validation') | Q(action__icontains='Validation Started'))
        elif action_filter == 'Validation Completed':
            logs = logs.filter(Q(action__icontains='Validation Completed') | Q(action__icontains='Completed Validation'))
        elif action_filter == 'Validation Failed':
            logs = logs.filter(action__icontains='Validation Failed')
        elif action_filter == 'Workflow Created':
            logs = logs.filter(Q(action__icontains='Created Workflow') | Q(action__icontains='Workflow Created'))
        elif action_filter == 'Workflow Triggered':
            logs = logs.filter(Q(action__icontains='Workflow Executed') | Q(action__icontains='Workflow Triggered') | Q(action__icontains='Triggered Workflow') | Q(action__icontains='Run Workflow'))
        elif action_filter == 'Workflow Deleted':
            logs = logs.filter(Q(action__icontains='Deleted Workflow') | Q(action__icontains='Workflow Deleted'))
        elif action_filter == 'Report Generated':
            logs = logs.filter(Q(action__icontains='Report Generated') | Q(action__icontains='Generated Report'))
        elif action_filter == 'Report Downloaded':
            logs = logs.filter(Q(action__icontains='Report Downloaded') | Q(action__icontains='Downloaded Report') | Q(action__icontains='Report Exported'))
        elif action_filter == 'Other':
            # Exclude all prefix filters
            logs = logs.exclude(
                Q(action__icontains='User Login') |
                Q(action__icontains='User Logout') |
                Q(action__icontains='User Created') | Q(action__icontains='Created User') |
                Q(action__icontains='Created Connection') | Q(action__icontains='Data Source Created') |
                Q(action__icontains='Created Mapping') | Q(action__icontains='Mapping Created') |
                Q(action__icontains='Deleted Mapping') | Q(action__icontains='Mapping Deleted') |
                Q(action__icontains='Validation Run') | Q(action__icontains='Run Validation') | Q(action__icontains='Started Validation') | Q(action__icontains='Validation Started') |
                Q(action__icontains='Validation Completed') | Q(action__icontains='Completed Validation') |
                Q(action__icontains='Validation Failed') |
                Q(action__icontains='Created Workflow') | Q(action__icontains='Workflow Created') |
                Q(action__icontains='Workflow Executed') | Q(action__icontains='Workflow Triggered') | Q(action__icontains='Triggered Workflow') | Q(action__icontains='Run Workflow') |
                Q(action__icontains='Deleted Workflow') | Q(action__icontains='Workflow Deleted') |
                Q(action__icontains='Report Generated') | Q(action__icontains='Generated Report') |
                Q(action__icontains='Report Downloaded') | Q(action__icontains='Downloaded Report') | Q(action__icontains='Report Exported')
            )

    logs = logs[:200]  # Limit to recent 200

    return render(request, 'logs/list.html', {
        'logs': logs,
        'current_level': level,
        'current_action': action_filter,
    })
