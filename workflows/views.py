import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Workflow
from mappings.models import Mapping
from accounts.decorators import contributor_or_admin_required

logger = logging.getLogger(__name__)


@login_required
def workflow_list_view(request):
    """List all workflows."""
    workflows = Workflow.objects.select_related('mapping', 'created_by').all()
    return render(request, 'workflows/list.html', {'workflows': workflows})


@login_required
@contributor_or_admin_required
def workflow_create_view(request):
    """Create a new workflow."""
    mappings = Mapping.objects.filter(is_active=True)

    if request.method == 'POST':
        try:
            schedule_type = request.POST.get('schedule_type', 'manual')
            selected_cols = request.POST.getlist('selected_columns')
            selected_columns_str = ",".join(selected_cols)

            # ── DB Trigger fields ─────────────────────────────────────────────
            trigger_name = ''
            trigger_scheduled_time = None
            poll_duration_hours = 3

            if schedule_type == 'db_trigger':
                trigger_name = request.POST.get('trigger_name', '').strip()
                trigger_scheduled_time = request.POST.get('trigger_scheduled_time') or None
                try:
                    poll_duration_hours = int(request.POST.get('poll_duration_hours', 3))
                    if poll_duration_hours < 1:
                        poll_duration_hours = 1
                except (ValueError, TypeError):
                    poll_duration_hours = 3

                if not trigger_name:
                    messages.error(request, 'Trigger Name is required for DB Trigger schedule.')
                    return render(request, 'workflows/create.html', {'mappings': mappings})
                if not trigger_scheduled_time:
                    messages.error(request, 'Scheduled Time is required for DB Trigger schedule.')
                    return render(request, 'workflows/create.html', {'mappings': mappings})

            workflow = Workflow.objects.create(
                name=request.POST.get('name', ''),
                description=request.POST.get('description', ''),
                mapping_id=request.POST.get('mapping'),
                schedule_type=schedule_type,
                schedule_time=request.POST.get('schedule_time') or None,
                schedule_day=request.POST.get('schedule_day') or None,
                cron_expression=request.POST.get('cron_expression', ''),
                # DB Trigger
                trigger_name=trigger_name,
                trigger_scheduled_time=trigger_scheduled_time,
                poll_duration_hours=poll_duration_hours,
                trigger_status='idle',
                selected_columns=selected_columns_str,
                created_by=request.user,
            )

            # Register with scheduler
            if schedule_type not in ('manual',):
                _register_celery_schedule(workflow)

            try:
                from logs.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action=f'Created Workflow: {workflow.name}',
                    entity_type='Workflow',
                    entity_id=workflow.id,
                    details={
                        'schedule': workflow.schedule_type,
                        'trigger_name': workflow.trigger_name or None,
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                    level='info',
                )
            except Exception:
                pass

            messages.success(request, f'Workflow "{workflow.name}" created successfully.')
            return redirect('workflows:list')

        except Exception as e:
            messages.error(request, f'Error: {str(e)}')

    return render(request, 'workflows/create.html', {'mappings': mappings})


@login_required
def workflow_detail_view(request, workflow_id):
    """View workflow details."""
    workflow = get_object_or_404(
        Workflow.objects.select_related('mapping', 'created_by'), id=workflow_id
    )
    return render(request, 'workflows/detail.html', {'workflow': workflow})


def _register_celery_schedule(workflow):
    """Register a workflow with Celery Beat for periodic execution."""
    try:
        from django_celery_beat.models import PeriodicTask, CrontabSchedule
        import json

        if workflow.schedule_type == 'daily':
            hour = workflow.schedule_time.hour if workflow.schedule_time else 0
            minute = workflow.schedule_time.minute if workflow.schedule_time else 0
            crontab, _ = CrontabSchedule.objects.get_or_create(
                minute=str(minute), hour=str(hour),
                day_of_week='*', day_of_month='*', month_of_year='*',
            )
            task_fn = 'workflows.tasks.execute_workflow_task'
            task_args = json.dumps([workflow.id])

        elif workflow.schedule_type == 'weekly':
            hour = workflow.schedule_time.hour if workflow.schedule_time else 0
            minute = workflow.schedule_time.minute if workflow.schedule_time else 0
            day = workflow.schedule_day if workflow.schedule_day is not None else 0
            crontab, _ = CrontabSchedule.objects.get_or_create(
                minute=str(minute), hour=str(hour),
                day_of_week=str(day), day_of_month='*', month_of_year='*',
            )
            task_fn = 'workflows.tasks.execute_workflow_task'
            task_args = json.dumps([workflow.id])

        elif workflow.schedule_type == 'monthly':
            hour = workflow.schedule_time.hour if workflow.schedule_time else 0
            minute = workflow.schedule_time.minute if workflow.schedule_time else 0
            day = workflow.schedule_day if workflow.schedule_day else 1
            crontab, _ = CrontabSchedule.objects.get_or_create(
                minute=str(minute), hour=str(hour),
                day_of_week='*', day_of_month=str(day), month_of_year='*',
            )
            task_fn = 'workflows.tasks.execute_workflow_task'
            task_args = json.dumps([workflow.id])

        elif workflow.schedule_type == 'db_trigger':
            # Celery Beat fires start_db_trigger_polling daily at trigger_scheduled_time
            t = workflow.trigger_scheduled_time
            hour = t.hour if t else 0
            minute = t.minute if t else 0
            crontab, _ = CrontabSchedule.objects.get_or_create(
                minute=str(minute), hour=str(hour),
                day_of_week='*', day_of_month='*', month_of_year='*',
            )
            task_fn = 'workflows.tasks.start_db_trigger_polling'
            task_args = json.dumps([workflow.id])

        elif workflow.schedule_type == 'custom_cron' and workflow.cron_expression:
            parts = workflow.cron_expression.split()
            if len(parts) == 5:
                crontab, _ = CrontabSchedule.objects.get_or_create(
                    minute=parts[0], hour=parts[1],
                    day_of_week=parts[4], day_of_month=parts[2], month_of_year=parts[3],
                )
            else:
                return
            task_fn = 'workflows.tasks.execute_workflow_task'
            task_args = json.dumps([workflow.id])
        else:
            return

        task_name = f'workflow_{workflow.id}_{workflow.name}'
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                'task': task_fn,
                'crontab': crontab,
                'args': task_args,
                'enabled': workflow.is_active,
            }
        )
        workflow.celery_task_name = task_name
        workflow.save(update_fields=['celery_task_name'])

    except ImportError:
        logger.warning("django-celery-beat not available, skipping schedule registration")
    except Exception as e:
        logger.error(f"Failed to register Celery schedule: {e}")


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@login_required
@contributor_or_admin_required
@require_POST
def api_trigger_workflow(request, workflow_id):
    """Manually trigger a workflow."""
    if hasattr(request.user, 'profile') and request.user.profile.role == 'auditor':
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
    workflow = get_object_or_404(Workflow, id=workflow_id)

    try:
        from .tasks import execute_workflow_task
        execute_workflow_task.delay(workflow.id, 'manual')
    except Exception:
        from validations.models import ValidationRun
        from validations.engine import ValidationEngine
        run = ValidationRun.objects.create(
            mapping=workflow.mapping,
            trigger_type='manual',
            status='pending',
            selected_columns=workflow.selected_columns,
        )
        engine = ValidationEngine(run)
        try:
            engine.execute()
            if workflow.created_by:
                from dashboard.models import Notification
                status_text = "Passed" if run.failed_checks == 0 else "Failed"
                Notification.objects.create(
                    user=workflow.created_by,
                    title=f"Workflow '{workflow.name}' Completed",
                    message=f"Validation Run {run.id} finished.\nStatus: {status_text} ({run.passed_checks}/{run.total_checks} checks passed)",
                    level='success' if run.failed_checks == 0 else 'warning'
                )
        except Exception as e:
            if workflow.created_by:
                from dashboard.models import Notification
                Notification.objects.create(
                    user=workflow.created_by,
                    title=f"Workflow '{workflow.name}' Failed",
                    message=f"Execution failed due to error: {e}",
                    level='error'
                )

    return JsonResponse({'success': True})


@login_required
@contributor_or_admin_required
@require_POST
def api_toggle_workflow(request, workflow_id):
    """Toggle workflow active state."""
    if hasattr(request.user, 'profile') and request.user.profile.role == 'auditor':
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
    workflow = get_object_or_404(Workflow, id=workflow_id)
    data = json.loads(request.body) if request.body else {}
    workflow.is_active = data.get('is_active', not workflow.is_active)
    workflow.save(update_fields=['is_active'])

    try:
        from django_celery_beat.models import PeriodicTask
        if workflow.celery_task_name:
            PeriodicTask.objects.filter(name=workflow.celery_task_name).update(enabled=workflow.is_active)
    except Exception:
        pass

    return JsonResponse({'success': True, 'is_active': workflow.is_active})


@login_required
def api_trigger_status(request, workflow_id):
    """Return current DB Trigger status for a workflow."""
    workflow = get_object_or_404(Workflow, id=workflow_id)
    return JsonResponse({
        'trigger_status': workflow.trigger_status,
        'trigger_status_display': workflow.get_trigger_status_display(),
        'trigger_name': workflow.trigger_name,
        'poll_duration_hours': workflow.poll_duration_hours,
        'trigger_last_polled': workflow.trigger_last_polled.isoformat() if workflow.trigger_last_polled else None,
        'trigger_fired_at': workflow.trigger_fired_at.isoformat() if workflow.trigger_fired_at else None,
    })


@login_required
@contributor_or_admin_required
@require_POST
def api_start_db_trigger(request, workflow_id):
    """Manually start DB Trigger polling for a workflow (for testing / on-demand)."""
    if hasattr(request.user, 'profile') and request.user.profile.role == 'auditor':
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)
    workflow = get_object_or_404(Workflow, id=workflow_id)
    if workflow.schedule_type != 'db_trigger':
        return JsonResponse({'success': False, 'error': 'Not a DB Trigger workflow.'}, status=400)

    try:
        from .tasks import start_db_trigger_polling
        start_db_trigger_polling.delay(workflow_id)
        return JsonResponse({'success': True, 'message': 'Polling started.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
