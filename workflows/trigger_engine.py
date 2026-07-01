"""
Trigger Engine — Pluggable trigger source framework for QualiX workflows.

Architecture:
    BaseTriggerSource (abstract)
        ├── DBTableTrigger      ← hwm_chk polling (implemented)
        ├── FileTrigger         ← future: file arrival detection
        ├── APICallbackTrigger  ← future: webhook / REST callback
        └── MessageQueueTrigger ← future: Kafka / SQS / RabbitMQ

To add a new trigger source:
    1. Subclass BaseTriggerSource
    2. Implement check_trigger(trigger_name: str) -> bool
    3. Register it in TRIGGER_SOURCE_REGISTRY below
"""
import logging
from abc import ABC, abstractmethod
from datetime import date

logger = logging.getLogger('workflows')


# ─── Abstract Base ─────────────────────────────────────────────────────────────

class BaseTriggerSource(ABC):
    """Abstract base class for all trigger sources."""

    source_type: str = 'base'

    @abstractmethod
    def check_trigger(self, trigger_name: str) -> bool:
        """
        Check whether the named trigger has fired.

        Args:
            trigger_name: The logical trigger identifier.

        Returns:
            True  — trigger is active, workflow should start.
            False — trigger not yet active, keep polling.
        """
        raise NotImplementedError

    def on_trigger_found(self, trigger_name: str, workflow_id: int):
        """Optional hook called when a trigger fires. Override in subclasses."""
        logger.info(f"[{self.source_type}] Trigger '{trigger_name}' fired for workflow {workflow_id}")

    def on_timeout(self, trigger_name: str, workflow_id: int):
        """Optional hook called on poll timeout. Override in subclasses."""
        logger.warning(f"[{self.source_type}] Trigger '{trigger_name}' timed out for workflow {workflow_id}")


# ─── DB Table Trigger ──────────────────────────────────────────────────────────

class DBTableTrigger(BaseTriggerSource):
    """
    Polls the hwm_chk table in the application database.

    Query executed:
        SELECT *
        FROM hwm_chk
        WHERE trigger_name = :trigger_name
          AND hwm_flag = 'Y'
          AND etl_date = CURRENT_DATE;
    """

    source_type = 'db_table'

    def check_trigger(self, trigger_name: str) -> bool:
        """Return True if a matching Y-flagged row exists for today."""
        from django.db import connection
        today = date.today().isoformat()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM hwm_chk
                    WHERE trigger_name = %s
                      AND hwm_flag = 'Y'
                      AND etl_date = %s
                    """,
                    [trigger_name, today]
                )
                row = cursor.fetchone()
                count = row[0] if row else 0
                logger.debug(
                    f"[db_table] hwm_chk check — trigger='{trigger_name}' date={today} count={count}"
                )
                return count > 0
        except Exception as exc:
            logger.error(f"[db_table] hwm_chk query failed for trigger '{trigger_name}': {exc}")
            return False

    def get_trigger_row(self, trigger_name: str):
        """Fetch the full matching row for audit/logging purposes."""
        from django.db import connection
        today = date.today().isoformat()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, trigger_name, hwm_flag, etl_date, created_at
                    FROM hwm_chk
                    WHERE trigger_name = %s AND hwm_flag = 'Y' AND etl_date = %s
                    LIMIT 1
                    """,
                    [trigger_name, today]
                )
                row = cursor.fetchone()
                if row:
                    return {
                        'id': row[0], 'trigger_name': row[1],
                        'hwm_flag': row[2], 'etl_date': str(row[3]),
                        'created_at': str(row[4]),
                    }
        except Exception as exc:
            logger.error(f"[db_table] get_trigger_row failed: {exc}")
        return None


# ─── Future Stubs (extensibility placeholders) ─────────────────────────────────

class FileTrigger(BaseTriggerSource):
    """Future: triggers when a file arrives in a configured path."""
    source_type = 'file'

    def check_trigger(self, trigger_name: str) -> bool:
        raise NotImplementedError("FileTrigger is not yet implemented.")


class APICallbackTrigger(BaseTriggerSource):
    """Future: triggers via an inbound REST/webhook callback."""
    source_type = 'api_callback'

    def check_trigger(self, trigger_name: str) -> bool:
        raise NotImplementedError("APICallbackTrigger is not yet implemented.")


class MessageQueueTrigger(BaseTriggerSource):
    """Future: triggers on message arrival in a queue (Kafka/SQS/RabbitMQ)."""
    source_type = 'message_queue'

    def check_trigger(self, trigger_name: str) -> bool:
        raise NotImplementedError("MessageQueueTrigger is not yet implemented.")


# ─── Registry ──────────────────────────────────────────────────────────────────

TRIGGER_SOURCE_REGISTRY: dict[str, type[BaseTriggerSource]] = {
    'db_table': DBTableTrigger,
    'file': FileTrigger,
    'api_callback': APICallbackTrigger,
    'message_queue': MessageQueueTrigger,
}


def get_trigger_source(source_type: str = 'db_table') -> BaseTriggerSource:
    """
    Factory function — returns an instance of the requested trigger source.

    Args:
        source_type: Key from TRIGGER_SOURCE_REGISTRY (default: 'db_table')

    Returns:
        Instantiated BaseTriggerSource subclass.

    Raises:
        ValueError: If source_type is not registered.
    """
    cls = TRIGGER_SOURCE_REGISTRY.get(source_type)
    if cls is None:
        raise ValueError(
            f"Unknown trigger source '{source_type}'. "
            f"Available: {list(TRIGGER_SOURCE_REGISTRY.keys())}"
        )
    return cls()
