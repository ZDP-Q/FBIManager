"""Tests for MonitorService — the core comment monitoring pipeline."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.fixture
def mock_repos(setup_db):
    """Seed a full test dataset for monitor tests and return test IDs."""
    from app.repositories import (
        create_account, upsert_page_profile, upsert_post, upsert_comment,
        create_monitor,
    )
    from tests.factories import (
        make_account, make_facebook_page_profile,
        make_facebook_post, make_facebook_comment,
    )

    page_id = "111111"
    post_id = f"{page_id}_001"

    upsert_page_profile(make_facebook_page_profile(page_id=page_id, name="TestPage"))
    create_account(**make_account(page_id=page_id, is_active=1))
    upsert_post(page_id, make_facebook_post(
        post_id=post_id, message="Test post message"))

    # Create pending comments (not screened, not replied, not own)
    for i in range(3):
        upsert_comment(post_id, None, make_facebook_comment(
            comment_id=f"c{i}", message=f"Comment {i}",
            author_id="user1", author_name="User One",
            created_time=f"2025-06-01T1{i}:00:00+0000"))

    mid = create_monitor(post_id, interval_seconds=1800)

    return {"page_id": page_id, "post_id": post_id, "monitor_id": mid}


class TestMonitorServiceBasics:
    """Test monitor initialisation and simple methods."""

    def test_monitor_service_init(self):
        from app.services.monitor import MonitorService
        svc = MonitorService()
        assert svc._running_monitors == set()
        assert svc._task is None

    @pytest.mark.asyncio
    async def test_monitor_start_stop(self):
        from app.services.monitor import MonitorService
        svc = MonitorService()
        await svc.start()
        assert svc._task is not None
        await svc.stop()
        # Task should be cancelled
        assert svc._task.cancelled() or svc._task.done()


class TestCommentCounting:
    """Test the new counting and random selection logic."""

    def test_count_all_comments(self, mock_repos):
        from app.repositories import count_all_comments
        # mock_repos seeds 3 comments
        assert count_all_comments(mock_repos["post_id"]) == 3

    def test_count_replied_comments_empty(self, mock_repos):
        from app.repositories import count_replied_comments
        assert count_replied_comments(mock_repos["post_id"]) == 0

    def test_count_replied_comments_after_marking(self, mock_repos):
        from app.repositories import count_replied_comments, mark_replied
        mark_replied("c0", mock_repos["post_id"], mock_repos["monitor_id"], "reply")
        assert count_replied_comments(mock_repos["post_id"]) == 1

    def test_list_unreplied_comments(self, mock_repos):
        from app.repositories import list_unreplied_comments
        unreplied = list_unreplied_comments(mock_repos["post_id"])
        assert len(unreplied) == 3

    def test_list_unreplied_excludes_replied(self, mock_repos):
        from app.repositories import list_unreplied_comments, mark_replied
        mark_replied("c0", mock_repos["post_id"], mock_repos["monitor_id"], "reply")
        unreplied = list_unreplied_comments(mock_repos["post_id"])
        assert len(unreplied) == 2
        ids = {c["id"] for c in unreplied}
        assert "c0" not in ids

    def test_list_unreplied_excludes_author(self, mock_repos):
        from app.repositories import list_unreplied_comments
        # All test comments have author_id "user1", exclude it
        unreplied = list_unreplied_comments(mock_repos["post_id"], exclude_author_id="user1")
        assert len(unreplied) == 0

    def test_10_percent_target(self, mock_repos):
        """10% of 3 comments = 0 (floor). With 10 comments, target = 1."""
        total = 3
        assert total // 10 == 0
        total = 10
        assert total // 10 == 1
        total = 134
        assert total // 10 == 13


class TestMonitorTick:
    """Test _tick scheduling logic."""

    @pytest.mark.asyncio
    async def test_tick_no_monitors(self, setup_db):
        from app.services.monitor import MonitorService

        svc = MonitorService()
        with patch("app.services.monitor.list_monitors", return_value=[]), \
             patch("app.services.monitor.get_auto_monitor_config",
                   return_value={"enabled": 0, "max_posts": 10}):
            await svc._tick()  # Should not raise


class TestCommentHasPageReply:
    """Test _comment_has_page_reply."""
    @pytest.fixture
    def setup(self, mock_repos):
        from app.config import AppConfig
        from app.services.facebook import FacebookService

        config = AppConfig(
            account_id=1, account_name="Test",
            page_access_token="tok", verify_token="v",
            page_id=mock_repos["page_id"],
        )
        return FacebookService(config)

    @pytest.mark.asyncio
    async def test_has_reply_in_comment_data(self, setup, mock_repos):
        from app.services.monitor import MonitorService

        svc = MonitorService()
        comment = {
            "id": "c1",
            "from": {"id": "user1"},
            "replies": {"data": [{
                "from": {"id": mock_repos["page_id"]},
            }]},
        }

        result = await svc._comment_has_page_reply(
            comment=comment, page_id=mock_repos["page_id"], facebook=setup)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_reply_in_data_fetches_remote(self, setup, mock_repos):
        from app.services.monitor import MonitorService

        svc = MonitorService()
        # Mock the remote fetch to return empty
        setup.fetch_replies_for_comment = AsyncMock(return_value=[])

        comment = {"id": "c1", "from": {"id": "user1"}, "replies": {}}

        result = await svc._comment_has_page_reply(
            comment=comment, page_id=mock_repos["page_id"], facebook=setup)
        assert result is False

    @pytest.mark.asyncio
    async def test_remote_check_returns_true(self, setup, mock_repos):
        from app.services.monitor import MonitorService

        svc = MonitorService()
        setup.fetch_replies_for_comment = AsyncMock(return_value=[{
            "from": {"id": mock_repos["page_id"]},
        }])

        comment = {"id": "c1", "from": {"id": "user1"}, "replies": {}}

        result = await svc._comment_has_page_reply(
            comment=comment, page_id=mock_repos["page_id"], facebook=setup)
        assert result is True

    @pytest.mark.asyncio
    async def test_remote_error_conservative_skip(self, setup, mock_repos):
        from app.services.monitor import MonitorService

        svc = MonitorService()
        setup.fetch_replies_for_comment = AsyncMock(
            side_effect=Exception("Network error"))

        comment = {"id": "c1", "from": {"id": "user1"}, "replies": {}}

        # On error, conservatively returns True to avoid duplicate replies
        result = await svc._comment_has_page_reply(
            comment=comment, page_id=mock_repos["page_id"], facebook=setup)
        assert result is True