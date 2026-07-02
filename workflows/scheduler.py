import os
import time
import logging
import threading
from datetime import datetime, date, time as dt_time, timedelta
from django.utils import timezone
from django.db import close_old_connections

logger = logging.getLogger('workflows')

def calculate_next_run(workflow, after_time=None):
    """
    Calculate the next occurrence of a workflow's schedule relative to after_time.
    Supports manual (with schedule_time), daily, weekly, monthly, and db_trigger.
    """
    if not after_time:
        after_time = timezone.now()
        
    tz = timezone.get_current_timezone()
    # Convert after_time to localized datetime in project timezone
    local_after = after_time.astimezone(tz)
    
    if workflow.schedule_type == 'manual':
        if not workflow.schedule_time:
            return None
        # Manual with schedule_time behaves like daily scheduled execution
        scheduled_time = workflow.schedule_time
        next_dt = timezone.make_aware(datetime.combine(local_after.date(), scheduled_time), tz)
        if next_dt <= local_after:
            next_dt += timedelta(days=1)
        return next_dt
        
    elif workflow.schedule_type == 'daily':
        scheduled_time = workflow.schedule_time or dt_time(6, 0)
        next_dt = timezone.make_aware(datetime.combine(local_after.date(), scheduled_time), tz)
        if next_dt <= local_after:
            next_dt += timedelta(days=1)
        return next_dt
        
    elif workflow.schedule_type == 'weekly':
        scheduled_time = workflow.schedule_time or dt_time(6, 0)
        target_day = workflow.schedule_day if workflow.schedule_day is not None else 0  # 0 = Mon, 6 = Sun
        current_day = local_after.weekday()
        days_ahead = target_day - current_day
        if days_ahead < 0 or (days_ahead == 0 and timezone.make_aware(datetime.combine(local_after.date(), scheduled_time), tz) <= local_after):
            days_ahead += 7
        next_dt = timezone.make_aware(datetime.combine(local_after.date() + timedelta(days=days_ahead), scheduled_time), tz)
        return next_dt
        
    elif workflow.schedule_type == 'monthly':
        scheduled_time = workflow.schedule_time or dt_time(6, 0)
        target_day = workflow.schedule_day or 1
        year = local_after.year
        month = local_after.month
        
        # Try finding target day in this month
        try:
            next_dt = timezone.make_aware(datetime.combine(date(year, month, target_day), scheduled_time), tz)
        except ValueError:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            next_dt = timezone.make_aware(datetime.combine(date(year, month, last_day), scheduled_time), tz)
            
        if next_dt <= local_after:
            month += 1
            if month > 12:
                month = 1
                year += 1
            try:
                next_dt = timezone.make_aware(datetime.combine(date(year, month, target_day), scheduled_time), tz)
            except ValueError:
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                next_dt = timezone.make_aware(datetime.combine(date(year, month, last_day), scheduled_time), tz)
        return next_dt
        
    elif workflow.schedule_type == 'db_trigger':
        scheduled_time = workflow.trigger_scheduled_time or dt_time(8, 0)
        next_dt = timezone.make_aware(datetime.combine(local_after.date(), scheduled_time), tz)
        if next_dt <= local_after:
            next_dt += timedelta(days=1)
        return next_dt
        
    return None


def run_due_workflows():
    """
    Check for and execute any active workflows whose scheduled next run time is in the past.
    """
    from .models import Workflow
    from .tasks import execute_workflow_task, start_db_trigger_polling

    now = timezone.now()
    
    # 1. Initialize next_run for any active workflows where next_run is null
    uninitialized = Workflow.objects.filter(is_active=True, next_run__isnull=True)
    for workflow in uninitialized:
        next_run = calculate_next_run(workflow, after_time=now)
        if next_run:
            workflow.next_run = next_run
            workflow.save(update_fields=['next_run'])
            logger.info(f"Scheduler: Initialized next_run for workflow '{workflow.name}' to {next_run}")

    # 2. Find workflows that are due
    due_workflows = Workflow.objects.filter(
        is_active=True,
        next_run__lte=now
    )

    for workflow in due_workflows:
        logger.info(f"Scheduler: Triggering workflow '{workflow.name}' (id={workflow.id}) due at {workflow.next_run}")
        
        # Calculate new next_run relative to now, and update
        old_next_run = workflow.next_run
        workflow.next_run = calculate_next_run(workflow, after_time=now)
        workflow.last_run = now
        workflow.save(update_fields=['next_run', 'last_run'])
        
        # Spawn thread to run the celery task to avoid blocking the main scheduler loop
        if workflow.schedule_type == 'db_trigger':
            t = threading.Thread(
                target=start_db_trigger_polling.delay,
                args=[workflow.id],
                daemon=True,
                name=f"WorkflowTrigger-{workflow.id}"
            )
            t.start()
        else:
            t = threading.Thread(
                target=execute_workflow_task.delay,
                args=[workflow.id, 'scheduled'],
                daemon=True,
                name=f"WorkflowRun-{workflow.id}"
            )
            t.start()


def scheduler_loop():
    """
    Infinite loop running in a background daemon thread.
    """
    logger.info("Background workflow scheduler thread started.")
    # Wait a few seconds to let Django fully initialize
    time.sleep(5)
    
    while True:
        try:
            close_old_connections()
            run_due_workflows()
        except Exception as e:
            logger.error(f"Error in background scheduler loop: {e}")
        time.sleep(10)


def start_scheduler():
    """
    Spawns the background scheduler thread.
    """
    t = threading.Thread(target=scheduler_loop, daemon=True, name="WorkflowScheduler")
    t.start()
