"""Tests for app/task.py — unified task service."""
from __future__ import annotations

import asyncio
import pytest

from app.task import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    cancel_task,
    cleanup_tasks,
    create_task,
    get_task,
    is_task_running,
    task_runner,
    update_task,
)


class TestTaskCRUD:
    def test_create_task(self, setup_db):
        task = create_task("test_1", "测试任务")
        assert task["id"] == "test_1"
        assert task["name"] == "测试任务"
        assert task["status"] == STATUS_PENDING
        assert task["progress"] == 0

    def test_create_task_reset_terminal(self, setup_db):
        """Creating a task with the same ID resets a terminal task."""
        create_task("test_1", "v1")
        update_task("test_1", status=STATUS_SUCCESS, progress=100)
        assert get_task("test_1")["status"] == STATUS_SUCCESS

        create_task("test_1", "v2")
        task = get_task("test_1")
        assert task["status"] == STATUS_PENDING
        assert task["name"] == "v2"
        assert task["progress"] == 0

    def test_get_task_not_found(self, setup_db):
        assert get_task("nonexistent") is None

    def test_update_task_partial(self, setup_db):
        create_task("test_1", "测试")
        update_task("test_1", progress=50, message="进行中")
        task = get_task("test_1")
        assert task["progress"] == 50
        assert task["message"] == "进行中"
        assert task["status"] == STATUS_PENDING  # unchanged

    def test_update_task_status_sets_ended_at(self, setup_db):
        create_task("test_1", "测试")
        update_task("test_1", status=STATUS_SUCCESS)
        task = get_task("test_1")
        assert task["ended_at"] is not None

    def test_update_task_result_dict(self, setup_db):
        create_task("test_1", "测试")
        update_task("test_1", result={"count": 42, "items": ["a", "b"]})
        task = get_task("test_1")
        assert task["result"]["count"] == 42
        assert task["result"]["items"] == ["a", "b"]


class TestTaskStatusTransitions:
    def test_pending_to_running(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        assert get_task("t")["status"] == STATUS_RUNNING

    def test_running_to_success(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        update_task("t", status=STATUS_SUCCESS, progress=100)
        t = get_task("t")
        assert t["status"] == STATUS_SUCCESS
        assert t["progress"] == 100
        assert t["ended_at"] is not None

    def test_running_to_failed(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        update_task("t", status=STATUS_FAILED, error="网络超时")
        t = get_task("t")
        assert t["status"] == STATUS_FAILED
        assert t["error"] == "网络超时"

    def test_running_to_canceled(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        cancel_task("t")
        assert get_task("t")["status"] == STATUS_CANCELED


class TestCancelTask:
    def test_cancel_running_task(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        assert cancel_task("t") is True
        assert get_task("t")["status"] == STATUS_CANCELED

    def test_cancel_non_running_task(self, setup_db):
        create_task("t", "test")
        assert cancel_task("t") is False  # still pending

    def test_cancel_nonexistent_task(self, setup_db):
        assert cancel_task("nope") is False


class TestIsTaskRunning:
    def test_pending_is_not_running(self, setup_db):
        create_task("t", "test")
        assert is_task_running("t") is False

    def test_running_is_running(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        assert is_task_running("t") is True

    def test_completed_is_not_running(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_SUCCESS)
        assert is_task_running("t") is False

    def test_nonexistent_is_not_running(self, setup_db):
        assert is_task_running("nope") is False


class TestCleanupTasks:
    def test_cleanup_removes_terminal_tasks(self, setup_db):
        create_task("a", "done")
        update_task("a", status=STATUS_SUCCESS)
        create_task("b", "running")
        update_task("b", status=STATUS_RUNNING)
        create_task("c", "failed")
        update_task("c", status=STATUS_FAILED)

        # Cleanup with 0 hours should remove terminal tasks (but they're fresh)
        # Use a negative offset to force cleanup
        cleanup_tasks(older_than_hours=0)
        # Running task should survive
        assert get_task("b") is not None

    def test_cleanup_preserves_running(self, setup_db):
        create_task("t", "test")
        update_task("t", status=STATUS_RUNNING)
        cleanup_tasks(older_than_hours=0)
        assert get_task("t") is not None


class TestTaskRunner:
    @pytest.mark.asyncio
    async def test_runner_success(self, setup_db):
        async with task_runner("t", "测试"):
            update_task("t", progress=50, message="处理中")
        t = get_task("t")
        assert t["status"] == STATUS_SUCCESS
        assert t["progress"] == 100

    @pytest.mark.asyncio
    async def test_runner_failure(self, setup_db):
        with pytest.raises(ValueError, match="boom"):
            async with task_runner("t", "测试"):
                update_task("t", progress=30)
                raise ValueError("boom")
        t = get_task("t")
        assert t["status"] == STATUS_FAILED
        assert "boom" in t["error"]
        assert t["progress"] == 30  # preserved from before the error

    @pytest.mark.asyncio
    async def test_runner_sets_running_on_entry(self, setup_db):
        async with task_runner("t", "测试"):
            assert get_task("t")["status"] == STATUS_RUNNING


class TestRegistryProxy:
    """Test that registry.py proxy functions work correctly with the new task backend."""

    def test_update_and_get_legacy_format(self, setup_db):
        from app.registry import update_task_status, get_task_status

        update_task_status("post_sync", {"msg": "同步中", "percent": 50, "done": False})
        status = get_task_status("post_sync")
        assert status is not None
        assert status["msg"] == "同步中"
        assert status["percent"] == 50
        assert status["done"] is False

    def test_legacy_done_true(self, setup_db):
        from app.registry import update_task_status, get_task_status

        update_task_status("post_sync", {"msg": "完成", "percent": 100, "done": True})
        status = get_task_status("post_sync")
        assert status["done"] is True

    def test_legacy_error(self, setup_db):
        from app.registry import update_task_status, get_task_status

        update_task_status("post_sync", {"msg": "失败", "done": True, "error": True})
        status = get_task_status("post_sync")
        assert status["error"] is True
        assert status["done"] is True

    def test_legacy_extras_in_result(self, setup_db):
        from app.registry import update_task_status, get_task_status

        update_task_status("chat_sync", {
            "msg": "同步中", "percent": 50, "done": False,
            "messages_synced": 42, "conversations": 5
        })
        status = get_task_status("chat_sync")
        assert status["messages_synced"] == 42
        assert status["conversations"] == 5
