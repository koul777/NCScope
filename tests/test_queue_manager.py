"""Tests for app.services.queue_manager module."""

import pytest
from app.services.queue_manager import QueueManager, QueueTask


class TestQueueManager:
    """Test QueueManager functionality."""

    def test_enqueue_single_task(self):
        """Test enqueuing a single task."""
        qm = QueueManager()
        qm.enqueue("process_jd", {"file_id": "123"})

        stats = qm.stats()
        assert stats["queued"] == 1

    def test_enqueue_multiple_tasks(self):
        """Test enqueuing multiple tasks."""
        qm = QueueManager()
        for i in range(5):
            qm.enqueue("process_jd", {"file_id": str(i)})

        stats = qm.stats()
        assert stats["queued"] == 5

    def test_process_all_success(self):
        """Test processing all tasks successfully."""
        qm = QueueManager()
        processed = []

        def handler(payload):
            processed.append(payload)

        qm.enqueue("test", {"id": 1})
        qm.enqueue("test", {"id": 2})

        handlers = {"test": handler}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["processed"] == 2
        assert stats["queued"] == 0
        assert len(processed) == 2

    def test_process_all_with_missing_handler(self):
        """Test processing when handler is missing."""
        qm = QueueManager()
        qm.enqueue("unknown_type", {"id": 1})

        handlers = {"test": lambda x: None}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["failed"] >= 1
        assert stats["processed"] == 0

    def test_process_all_with_exception(self):
        """Test processing when handler raises exception."""
        qm = QueueManager()
        qm.enqueue("failing", {"id": 1})

        def failing_handler(payload):
            raise ValueError("Test error")

        handlers = {"failing": failing_handler}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["failed"] >= 1

    def test_retry_mechanism(self):
        """Test retry mechanism for failed tasks."""
        qm = QueueManager(max_retries=2)
        qm.enqueue("failing", {"id": 1})

        def failing_handler(payload):
            raise ValueError("Test error")

        handlers = {"failing": failing_handler}

        # process_all processes all retries in a single call until max_retries exceeded
        qm.process_all(handlers)
        stats = qm.stats()
        # After one process_all with max_retries=2, task is requeued twice then goes to dead_letter
        assert stats["dead_letter"] == 1
        assert stats["failed"] == 3  # Initial attempt + 2 retries

    def test_max_retries_exceeded(self):
        """Test that task goes to dead_letter after max retries."""
        qm = QueueManager(max_retries=1)
        qm.enqueue("failing", {"id": 1})

        def failing_handler(payload):
            raise ValueError("Test error")

        handlers = {"failing": failing_handler}

        # Process multiple times
        for _ in range(3):
            qm.process_all(handlers)

        stats = qm.stats()
        # After exceeding max_retries, task should be in dead_letter
        assert stats["dead_letter"] >= 1

    def test_dead_letter_queue(self):
        """Test that dead letter queue accumulates failed tasks."""
        qm = QueueManager(max_retries=0)
        qm.enqueue("type1", {"id": 1})
        qm.enqueue("type2", {"id": 2})

        handlers = {}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["dead_letter"] == 2

    def test_stats_initial_state(self):
        """Test initial stats."""
        qm = QueueManager()
        stats = qm.stats()
        assert stats["queued"] == 0
        assert stats["processed"] == 0
        assert stats["failed"] == 0
        assert stats["dead_letter"] == 0

    def test_mixed_success_and_failure(self):
        """Test processing mix of successful and failed tasks."""
        qm = QueueManager(max_retries=0)
        qm.enqueue("success", {"id": 1})
        qm.enqueue("success", {"id": 2})
        qm.enqueue("fail", {"id": 3})

        def success_handler(payload):
            pass

        def fail_handler(payload):
            raise ValueError("Error")

        handlers = {"success": success_handler, "fail": fail_handler}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["processed"] == 2
        assert stats["dead_letter"] == 1

    def test_task_payload_preservation(self):
        """Test that task payloads are preserved during processing."""
        qm = QueueManager()
        payload = {"file_id": "123", "data": {"key": "value"}}
        qm.enqueue("process", payload)

        received_payload = []

        def handler(p):
            received_payload.append(p)

        qm.process_all({"process": handler})

        assert received_payload[0] == payload

    def test_concurrent_safety(self):
        """Test thread safety of queue operations."""
        import threading

        qm = QueueManager()

        def enqueue_tasks():
            for i in range(10):
                qm.enqueue("test", {"id": i})

        # Simulate concurrent enqueueing
        threads = [threading.Thread(target=enqueue_tasks) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = qm.stats()
        assert stats["queued"] == 30

    def test_empty_queue_processing(self):
        """Test processing an empty queue."""
        qm = QueueManager()
        handlers = {}
        qm.process_all(handlers)

        stats = qm.stats()
        assert stats["queued"] == 0
        assert stats["processed"] == 0
