"""
Celery tasks for workflow execution and DB Trigger polling.
"""
import time
import logging
import threading
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger('workflows')


# ─────────────────────────────────────────────────────────────────────────────
# Existing: Execute Workflow
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True)
def execute_workflow_task(self, workflow_id, trigger_source='scheduled'):
    """Execute a scheduled workflow — creates and runs a validation."""
    from .models import Workflow
    from validations.models import ValidationRun
    from validations.engine import ValidationEngine

    try:
        workflow = Workflow.objects.select_related('mapping').get(id=workflow_id)

        if not workflow.is_active:
            logger.info(f"Workflow '{workflow.name}' is inactive, skipping.")
            return

        # Create validation run
        run = ValidationRun.objects.create(
            mapping=workflow.mapping,
            workflow=workflow,
            trigger_type=trigger_source,
            status='pending',
            selected_columns=workflow.selected_columns,
        )

        # Execute validation
        engine = ValidationEngine(run)
        engine.execute()

        # Update workflow
        workflow.last_run = timezone.now()
        workflow.save(update_fields=['last_run'])

        logger.info(f"Workflow '{workflow.name}' executed successfully. Run {run.id}")

        if workflow.created_by:
            try:
                from dashboard.models import Notification
                status_text = "Passed" if run.failed_checks == 0 else "Failed"
                Notification.objects.create(
                    user=workflow.created_by,
                    title=f"Workflow '{workflow.name}' Completed",
                    message=f"Validation Run {run.id} finished.\nStatus: {status_text} ({run.passed_checks}/{run.total_checks} checks passed)",
                    level='success' if run.failed_checks == 0 else 'warning'
                )
            except Exception:
                pass

        try:
            from logs.models import AuditLog
            AuditLog.objects.create(
                action=f'Workflow Executed: {workflow.name}',
                entity_type='Workflow',
                entity_id=workflow.id,
                details={
                    'run_id': run.id,
                    'passed': run.passed_checks,
                    'failed': run.failed_checks,
                    'trigger_source': trigger_source,
                },
                level='info',
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Workflow {workflow_id} execution failed: {e}")
        try:
            workflow = Workflow.objects.get(id=workflow_id)
            if workflow.created_by:
                from dashboard.models import Notification
                Notification.objects.create(
                    user=workflow.created_by,
                    title=f"Workflow '{workflow.name}' Failed",
                    message=f"Execution failed due to error: {e}",
                    level='error'
                )
        except Exception:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# DB Trigger: Start Polling (called by Celery Beat at trigger_scheduled_time)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True)
def start_db_trigger_polling(self, workflow_id):
    """
    Entry point for DB Trigger polling.
    Called by Celery Beat at the configured trigger_scheduled_time each day.
    Starts a background polling loop (thread-based fallback if eager mode).
    """
    from .models import Workflow
    try:
        workflow = Workflow.objects.get(id=workflow_id)
        if not workflow.is_active:
            logger.info(f"DB Trigger: workflow '{workflow.name}' inactive, skipping.")
            return
        if workflow.schedule_type != 'db_trigger':
            return

        logger.info(f"DB Trigger polling STARTED for workflow '{workflow.name}' (trigger='{workflow.trigger_name}')")

        # Update status
        workflow.trigger_status = 'polling'
        workflow.save(update_fields=['trigger_status'])

        # Audit log: Polling Started
        _audit(
            action=f"DB Trigger Polling Started: {workflow.trigger_name}",
            workflow=workflow,
            level='info',
            details={'trigger_name': workflow.trigger_name, 'poll_duration_hours': workflow.poll_duration_hours},
        )

        poll_start = time.time()

        # Prefer Celery async; fall back to background thread in eager/dev mode
        from django.conf import settings
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            _run_polling_in_thread(workflow_id, poll_start)
        else:
            poll_db_trigger_task.apply_async(
                args=[workflow_id, poll_start],
                countdown=0,
            )

    except Exception as exc:
        logger.error(f"start_db_trigger_polling failed for workflow {workflow_id}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DB Trigger: Self-rescheduling 60-second poller (Celery async path)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=None)
def poll_db_trigger_task(self, workflow_id, poll_start_epoch):
    """
    Polls hwm_chk every 60 seconds.
    Stops when:
      • Trigger found  → executes workflow
      • Timeout        → marks workflow as 'timeout'
      • Workflow deactivated
    """
    from .models import Workflow
    from .trigger_engine import get_trigger_source

    try:
        workflow = Workflow.objects.get(id=workflow_id)

        if not workflow.is_active or workflow.schedule_type != 'db_trigger':
            _audit(action=f"DB Trigger Polling Stopped: {workflow.trigger_name}",
                   workflow=workflow, level='info',
                   details={'reason': 'workflow inactive or type changed'})
            return

        elapsed = time.time() - poll_start_epoch
        max_seconds = workflow.poll_duration_hours * 3600

        # ── Timeout check ────────────────────────────────────────────────────
        if elapsed >= max_seconds:
            logger.warning(
                f"DB Trigger TIMEOUT for workflow '{workflow.name}' "
                f"(trigger='{workflow.trigger_name}', elapsed={elapsed:.0f}s)"
            )
            workflow.trigger_status = 'timeout'
            workflow.save(update_fields=['trigger_status'])
            _audit(
                action=f"DB Trigger Timeout: {workflow.trigger_name}",
                workflow=workflow, level='warning',
                details={'trigger_name': workflow.trigger_name, 'elapsed_seconds': int(elapsed)},
            )
            _audit(
                action=f"DB Trigger Polling Stopped: {workflow.trigger_name}",
                workflow=workflow, level='info',
                details={'reason': 'timeout'},
            )
            _notify(workflow, f"DB Trigger '{workflow.trigger_name}' timed out.", level='warning')
            return

        # ── Poll the trigger source ──────────────────────────────────────────
        trigger_src = get_trigger_source('db_table')
        workflow.trigger_last_polled = timezone.now()
        workflow.save(update_fields=['trigger_last_polled'])

        found = trigger_src.check_trigger(workflow.trigger_name)

        if found:
            # Fetch row details for audit
            row = trigger_src.get_trigger_row(workflow.trigger_name)
            logger.info(
                f"DB Trigger FOUND for workflow '{workflow.name}' "
                f"(trigger='{workflow.trigger_name}', row={row})"
            )
            _audit(
                action=f"DB Trigger Found: {workflow.trigger_name}",
                workflow=workflow, level='success',
                details={'trigger_name': workflow.trigger_name, 'hwm_row': row},
            )

            # Update status
            workflow.trigger_status = 'triggered'
            workflow.trigger_fired_at = timezone.now()
            workflow.save(update_fields=['trigger_status', 'trigger_fired_at'])

            # Execute the workflow
            _audit(
                action=f"Workflow Started via DB Trigger: {workflow.name}",
                workflow=workflow, level='success',
                details={'trigger_name': workflow.trigger_name},
            )
            execute_workflow_task.apply_async(args=[workflow_id, 'db_trigger'])

            _audit(
                action=f"DB Trigger Polling Stopped: {workflow.trigger_name}",
                workflow=workflow, level='info',
                details={'reason': 'trigger fired'},
            )
            _notify(workflow, f"DB Trigger '{workflow.trigger_name}' fired — workflow started.", level='success')
            return

        # ── Not found yet — reschedule in 60 seconds ─────────────────────────
        logger.debug(
            f"DB Trigger not found yet for '{workflow.trigger_name}' "
            f"(elapsed={elapsed:.0f}s / {max_seconds}s)"
        )
        poll_db_trigger_task.apply_async(
            args=[workflow_id, poll_start_epoch],
            countdown=60,
        )

    except Exception as exc:
        logger.error(f"poll_db_trigger_task error for workflow {workflow_id}: {exc}")
        try:
            workflow = Workflow.objects.get(id=workflow_id)
            workflow.trigger_status = 'error'
            workflow.save(update_fields=['trigger_status'])
            _audit(
                action=f"DB Trigger Polling Stopped: {getattr(workflow, 'trigger_name', '?')}",
                workflow=workflow, level='error',
                details={'reason': 'exception', 'error': str(exc)},
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Thread-based fallback poller (for dev/eager mode)
# ─────────────────────────────────────────────────────────────────────────────

def _run_polling_in_thread(workflow_id, poll_start_epoch):
    """
    Launches a background daemon thread for polling when Celery is in eager mode.
    This ensures polling is non-blocking even without a real Celery worker.
    """
    def _poll_loop():
        import django
        from .models import Workflow
        from .trigger_engine import get_trigger_source

        try:
            while True:
                workflow = Workflow.objects.get(id=workflow_id)
                if not workflow.is_active or workflow.schedule_type != 'db_trigger':
                    break

                elapsed = time.time() - poll_start_epoch
                max_seconds = workflow.poll_duration_hours * 3600

                if elapsed >= max_seconds:
                    workflow.trigger_status = 'timeout'
                    workflow.save(update_fields=['trigger_status'])
                    _audit(
                        action=f"DB Trigger Timeout: {workflow.trigger_name}",
                        workflow=workflow, level='warning',
                        details={'elapsed_seconds': int(elapsed)},
                    )
                    _audit(
                        action=f"DB Trigger Polling Stopped: {workflow.trigger_name}",
                        workflow=workflow, level='info',
                        details={'reason': 'timeout'},
                    )
                    _notify(workflow, f"DB Trigger '{workflow.trigger_name}' timed out.", level='warning')
                    break

                trigger_src = get_trigger_source('db_table')
                workflow.trigger_last_polled = timezone.now()
                workflow.save(update_fields=['trigger_last_polled'])
                found = trigger_src.check_trigger(workflow.trigger_name)

                if found:
                    row = trigger_src.get_trigger_row(workflow.trigger_name)
                    _audit(
                        action=f"DB Trigger Found: {workflow.trigger_name}",
                        workflow=workflow, level='success',
                        details={'hwm_row': row},
                    )
                    workflow.trigger_status = 'triggered'
                    workflow.trigger_fired_at = timezone.now()
                    workflow.save(update_fields=['trigger_status', 'trigger_fired_at'])

                    _audit(
                        action=f"Workflow Started via DB Trigger: {workflow.name}",
                        workflow=workflow, level='success',
                        details={'trigger_name': workflow.trigger_name},
                    )
                    # Run synchronously in thread
                    from validations.models import ValidationRun
                    from validations.engine import ValidationEngine
                    run = ValidationRun.objects.create(
                        mapping=workflow.mapping,
                        workflow=workflow,
                        trigger_type='db_trigger',
                        status='pending',
                        selected_columns=workflow.selected_columns,
                    )
                    ValidationEngine(run).execute()
                    workflow.last_run = timezone.now()
                    workflow.save(update_fields=['last_run'])
                    _notify(workflow, f"DB Trigger '{workflow.trigger_name}' fired — workflow started.", level='success')
                    _audit(
                        action=f"DB Trigger Polling Stopped: {workflow.trigger_name}",
                        workflow=workflow, level='info',
                        details={'reason': 'trigger fired'},
                    )
                    break

                time.sleep(60)

        except Exception as exc:
            logger.error(f"Thread polling error for workflow {workflow_id}: {exc}")

    t = threading.Thread(target=_poll_loop, daemon=True, name=f"dbtrigger-{workflow_id}")
    t.start()
    logger.info(f"DB Trigger polling thread started for workflow {workflow_id}: {t.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _audit(action, workflow, level='info', details=None):
    """Write an AuditLog entry."""
    try:
        from logs.models import AuditLog
        AuditLog.objects.create(
            action=action,
            entity_type='Workflow',
            entity_id=workflow.id,
            details=details or {},
            level=level,
        )
    except Exception as exc:
        logger.error(f"AuditLog write failed: {exc}")


def _notify(workflow, message, level='info'):
    """Send in-app notification to the workflow creator."""
    try:
        if workflow.created_by:
            from dashboard.models import Notification
            Notification.objects.create(
                user=workflow.created_by,
                title=f"DB Trigger — {workflow.name}",
                message=message,
                level=level,
            )
    except Exception as exc:
        logger.error(f"Notification send failed: {exc}")
