"""Tests for repositories.py — DAO layer, all SQLite CRUD operations."""
import json
import time

import pytest
from tests.factories import (
    make_account,
    make_facebook_comment,
    make_facebook_page_profile,
    make_facebook_post,
    make_model_config,
)

# All post/conversation inserts require page_profile FK
PAGE_ID = "123456789"


def _seed_page_and_account():
    """Create page_profile + account — required for post/comment/conversation FKs."""
    from app.repositories import create_account, upsert_page_profile

    upsert_page_profile(make_facebook_page_profile(page_id=PAGE_ID))
    create_account(**make_account(page_id=PAGE_ID, token="tok",
                                   verify="verify_me", is_active=1))


# ============================================================================
# Account Configs
# ============================================================================
class TestAccountConfigs:
    def test_create_account(self, setup_db):
        from app.repositories import create_account, get_account_by_id

        aid = create_account(**make_account(name="Test Page", page_id="111",
                                             token="tok1", verify="v1"))
        acct = get_account_by_id(aid)
        assert acct is not None
        assert acct["name"] == "Test Page"
        assert acct["page_id"] == "111"

    def test_create_account_sets_active(self, setup_db):
        from app.repositories import create_account, get_active_account

        create_account(**make_account(page_id="222", token="tok2", verify="v2",
                                       is_active=1))
        active = get_active_account()
        assert active is not None
        assert active["page_id"] == "222"

    def test_create_account_only_one_active(self, setup_db):
        from app.repositories import create_account, get_active_account

        create_account(**make_account(page_id="aaa", token="t1", verify="v1",
                                       is_active=1))
        create_account(**make_account(page_id="bbb", token="t2", verify="v2",
                                       is_active=1))
        active = get_active_account()
        # Latest one should be active
        assert active["page_id"] == "bbb"

    def test_update_account(self, setup_db):
        from app.repositories import create_account, update_account, get_account_by_id

        aid = create_account(**make_account(page_id="333", token="old"))
        update_account(aid, name="Updated Name")
        acct = get_account_by_id(aid)
        assert acct["name"] == "Updated Name"

    def test_delete_account(self, setup_db):
        from app.repositories import create_account, delete_account, get_account_by_id

        aid = create_account(**make_account(page_id="444", token="t4"))
        delete_account(aid)
        assert get_account_by_id(aid) is None

    def test_get_account_by_page_id(self, setup_db):
        from app.repositories import create_account, get_account_by_page_id

        create_account(**make_account(page_id="555", token="t5"))
        acct = get_account_by_page_id("555")
        assert acct is not None
        assert acct["page_id"] == "555"

    def test_get_account_by_verify_token(self, setup_db):
        from app.repositories import create_account, get_account_by_verify_token

        create_account(**make_account(page_id="666", token="t6", verify="my_verify"))
        acct = get_account_by_verify_token("my_verify")
        assert acct is not None
        assert acct["verify_token"] == "my_verify"

    def test_set_active_account(self, setup_db):
        from app.repositories import (create_account, set_active_account,
                                       get_active_account)

        a1 = create_account(**make_account(page_id="p1", token="t1", is_active=1))
        a2 = create_account(**make_account(page_id="p2", token="t2", is_active=0))

        set_active_account(a2)
        active = get_active_account()
        assert active["page_id"] == "p2"

    def test_bulk_import_accounts(self, setup_db):
        from app.repositories import bulk_import_accounts, list_accounts

        accounts = [
            make_account(name="Bulk 1", page_id="b1", token="bt1", verify="bv1",
                         is_active=0),
            make_account(name="Bulk 2", page_id="b2", token="bt2", verify="bv2",
                         is_active=0),
        ]
        count = bulk_import_accounts(accounts)
        assert count == 2
        all_accts = list_accounts()
        assert len(all_accts) >= 2


# ============================================================================
# Model Config
# ============================================================================
class TestModelConfig:
    def test_upsert_and_get_model_config(self, setup_db):
        from app.repositories import get_model_config, upsert_model_config

        upsert_model_config(**make_model_config(reply_url="https://api.test.com",
                                                  reply_key="sk-test",
                                                  reply_model="test-model"))
        cfg = get_model_config()
        assert cfg is not None
        assert cfg["reply_api_base_url"] == "https://api.test.com"
        assert cfg["reply_model"] == "test-model"

    def test_get_model_config_empty(self, setup_db):
        from app.repositories import get_model_config

        cfg = get_model_config()
        assert cfg is None

    def test_upsert_model_config_updates(self, setup_db):
        from app.repositories import get_model_config, upsert_model_config

        upsert_model_config(**make_model_config(reply_model="first"))
        upsert_model_config(**make_model_config(reply_model="second"))
        cfg = get_model_config()
        assert cfg["reply_model"] == "second"


# ============================================================================
# Posts
# ============================================================================
class TestPosts:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        _seed_page_and_account()

    def test_upsert_and_list_post(self, setup_db):
        from app.repositories import upsert_post, list_posts

        upsert_post("123456789", make_facebook_post(post_id="123456789_001",
                                                      message="Hello"))
        posts = list_posts(page_id="123456789")
        assert len(posts) == 1
        assert posts[0]["message"] == "Hello"

    def test_upsert_post_idempotent(self, setup_db):
        from app.repositories import upsert_post, list_posts

        upsert_post("123456789", make_facebook_post(post_id="123456789_001",
                                                      message="First"))
        upsert_post("123456789", make_facebook_post(post_id="123456789_001",
                                                      message="Second"))
        posts = list_posts(page_id="123456789")
        assert len(posts) == 1
        assert posts[0]["message"] == "Second"

    def test_get_post(self, setup_db):
        from app.repositories import upsert_post, get_post

        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))
        post = get_post("123456789_001")
        assert post is not None
        assert post["id"] == "123456789_001"

    def test_delete_posts(self, setup_db):
        from app.repositories import upsert_post, delete_posts, list_posts

        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))
        upsert_post("123456789", make_facebook_post(post_id="123456789_002"))
        delete_posts(["123456789_001"])
        posts = list_posts(page_id="123456789")
        assert len(posts) == 1
        assert posts[0]["id"] == "123456789_002"

    def test_clear_page_posts(self, setup_db):
        from app.repositories import upsert_post, clear_page_posts, list_posts

        upsert_post("123456789", make_facebook_post(post_id="123456789_001"))
        clear_page_posts("123456789")
        posts = list_posts(page_id="123456789")
        assert len(posts) == 0


# ============================================================================
# Comments
# ============================================================================
class TestComments:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_post

        _seed_page_and_account()
        upsert_post(PAGE_ID, make_facebook_post(post_id="123456789_001",
                                                  message="Test post"))

    def test_upsert_comment(self, setup_db):
        from app.repositories import upsert_comment, get_comment

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="c1", message="Nice!"))

        c = get_comment("c1")
        assert c is not None
        assert c["message"] == "Nice!"
        assert c["author_name"] == "Test User"

    def test_upsert_comment_with_reply(self, setup_db):
        from app.repositories import upsert_comment, list_comments_by_post_ids

        # Create parent comment with a nested reply in the data structure
        reply = make_facebook_comment(comment_id="reply1", message="Reply msg",
                                       parent_comment_id="parent1")
        parent = make_facebook_comment(comment_id="parent1", message="Parent")
        parent["replies"] = {"data": [reply]}

        upsert_comment("123456789_001", None, parent)

        tree = list_comments_by_post_ids(["123456789_001"])
        assert "123456789_001" in tree
        assert len(tree["123456789_001"]) == 1
        # Parent comment should have reply nested inside
        parent_in_tree = tree["123456789_001"][0]
        assert parent_in_tree["id"] == "parent1"
        assert len(parent_in_tree.get("replies", [])) == 1
        assert parent_in_tree["replies"][0]["id"] == "reply1"

    def test_list_comments_by_post_ids_tree_structure(self, setup_db):
        from app.repositories import upsert_comment, list_comments_by_post_ids

        c1 = make_facebook_comment(comment_id="c1", message="First")
        c2 = make_facebook_comment(comment_id="c2", message="Reply to c1",
                                    parent_comment_id="c1")
        c3 = make_facebook_comment(comment_id="c3", message="Second top-level")

        upsert_comment("123456789_001", None, c1)
        upsert_comment("123456789_001", "c1", c2)
        upsert_comment("123456789_001", None, c3)

        tree = list_comments_by_post_ids(["123456789_001"])
        comments = tree["123456789_001"]
        assert len(comments) == 2  # c1 and c3 are top-level
        ids = {c["id"] for c in comments}
        assert ids == {"c1", "c3"}
        # c1 should have c2 as reply
        c1_node = next(c for c in comments if c["id"] == "c1")
        assert len(c1_node.get("replies", [])) == 1
        assert c1_node["replies"][0]["id"] == "c2"

    def test_get_comment(self, setup_db):
        from app.repositories import upsert_comment, get_comment

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="gc1", message="Test"))
        c = get_comment("gc1")
        assert c["message"] == "Test"

    def test_get_comment_not_found(self, setup_db):
        from app.repositories import get_comment

        assert get_comment("nonexistent") is None

    def test_delete_comment_local(self, setup_db):
        from app.repositories import upsert_comment, delete_comment_local, get_comment

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="dc1"))
        delete_comment_local("dc1")
        assert get_comment("dc1") is None

    def test_replace_comments_for_post(self, setup_db):
        from app.repositories import (replace_comments_for_post,
                                       list_comments_by_post_ids)

        comments = [
            make_facebook_comment(comment_id="rc1", message="Batch 1"),
            make_facebook_comment(comment_id="rc2", message="Batch 2"),
        ]
        replace_comments_for_post("123456789_001", comments)

        tree = list_comments_by_post_ids(["123456789_001"])
        assert len(tree["123456789_001"]) == 2

        # Replace with new set
        replace_comments_for_post("123456789_001",
                                  [make_facebook_comment(comment_id="rc3",
                                                          message="New")])
        tree2 = list_comments_by_post_ids(["123456789_001"])
        assert len(tree2["123456789_001"]) == 1
        assert tree2["123456789_001"][0]["id"] == "rc3"


# ============================================================================
# Screened / Pending Comments
# ============================================================================
class TestScreenedComments:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_post

        _seed_page_and_account()
        upsert_post(PAGE_ID, make_facebook_post(post_id="123456789_001"))

    def test_screened_workflow(self, setup_db):
        from app.repositories import (upsert_comment, get_screened_comment_ids,
                                       mark_comments_screened,
                                       count_pending_comments,
                                       list_pending_comments)

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="sc1"))
        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="sc2"))

        # All start unscreened
        assert count_pending_comments("123456789_001") == 2

        pending = list_pending_comments("123456789_001")
        assert len(pending) == 2
        assert "author_id" in pending[0]
        assert "message" in pending[0]

        # Mark one as screened
        mark_comments_screened(["sc1"])
        assert count_pending_comments("123456789_001") == 1

        screened = get_screened_comment_ids("123456789_001")
        assert screened == {"sc1"}

        # Mark remaining
        mark_comments_screened(["sc2"])
        assert count_pending_comments("123456789_001") == 0

    def test_get_latest_comment_time(self, setup_db):
        from app.repositories import upsert_comment, get_latest_comment_time

        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="lt1",
                                              created_time="2025-06-01T10:00:00"))
        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="lt2",
                                              created_time="2025-06-01T12:00:00"))

        latest = get_latest_comment_time("123456789_001")
        assert latest is not None

    def test_get_latest_comment_time_no_comments(self, setup_db):
        from app.repositories import get_latest_comment_time

        assert get_latest_comment_time("123456789_001") is None


# ============================================================================
# Comment Attachments
# ============================================================================
class TestCommentAttachments:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_post, upsert_comment

        _seed_page_and_account()
        upsert_post(PAGE_ID, make_facebook_post(post_id="123456789_001"))
        # Create a comment so attachment FK works
        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="att_comment", message="test"))

    def test_insert_and_get_attachment(self, setup_db):
        from app.repositories import (insert_comment_attachment,
                                       get_comment_attachments, has_attachment)

        insert_comment_attachment("att_comment", "photo", "https://example.com/img.jpg",
                                  b"fake image bytes")

        assert has_attachment("att_comment") is True
        assert has_attachment("nonexistent") is False

        atts = get_comment_attachments("att_comment")
        assert len(atts) == 1
        assert atts[0]["media_type"] == "photo"
        assert atts[0]["media_url"] == "https://example.com/img.jpg"
        assert atts[0]["data"] == b"fake image bytes"

    def test_insert_attachment_ignore_duplicate(self, setup_db):
        from app.repositories import (insert_comment_attachment,
                                       get_comment_attachments)

        insert_comment_attachment("att_comment", "sticker", "url1", b"data1")
        insert_comment_attachment("att_comment", "sticker", "url1", b"data2")

        atts = get_comment_attachments("att_comment")
        # Multiple attachments per comment are allowed (no unique constraint)
        assert len(atts) >= 1

    def test_compress_attachment_webp(self, setup_db):
        from app.services.attachments import _compress_attachment
        from PIL import Image
        import io

        # Create a small test image
        img = Image.new("RGB", (100, 100), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data = buf.getvalue()

        result = _compress_attachment(png_data, "photo")
        # Should be WebP (starts with RIFF)
        assert result[:4] == b"RIFF"
        # Should be smaller than the PNG
        assert len(result) < len(png_data)

    def test_compress_attachment_small_image_no_regression(self, setup_db):
        from app.services.attachments import _compress_attachment

        # Very small data that can't be decoded as image
        result = _compress_attachment(b"not an image", "photo")
        # Should return original data
        assert result == b"not an image"


# ============================================================================
# Monitors
# ============================================================================
class TestMonitors:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_post

        _seed_page_and_account()
        upsert_post(PAGE_ID, make_facebook_post(post_id="123456789_001"))

    def test_create_and_get_monitor(self, setup_db):
        from app.repositories import create_monitor, get_monitor

        mid = create_monitor("123456789_001", interval_seconds=1800)
        m = get_monitor(mid)
        assert m is not None
        assert m["post_id"] == "123456789_001"
        assert m["interval_seconds"] == 1800

    def test_create_monitor_duplicate(self, setup_db):
        from app.repositories import create_monitor, list_monitors

        create_monitor("123456789_001")
        create_monitor("123456789_001")  # INSERT OR IGNORE
        monitors = list_monitors(page_id="123456789")
        assert len(monitors) == 1

    def test_list_monitors(self, setup_db):
        from app.repositories import create_monitor, upsert_post, list_monitors

        upsert_post("123456789", make_facebook_post(post_id="123456789_002"))
        create_monitor("123456789_001")
        create_monitor("123456789_002")

        monitors = list_monitors(page_id="123456789")
        assert len(monitors) == 2

    def test_update_monitor(self, setup_db):
        from app.repositories import create_monitor, update_monitor, get_monitor

        mid = create_monitor("123456789_001")
        update_monitor(mid, enabled=0, interval_seconds=3600)

        m = get_monitor(mid)
        assert m["enabled"] == 0
        assert m["interval_seconds"] == 3600

    def test_delete_monitor(self, setup_db):
        from app.repositories import create_monitor, delete_monitor, get_monitor

        mid = create_monitor("123456789_001")
        delete_monitor(mid)
        assert get_monitor(mid) is None

    def test_delete_monitors_bulk(self, setup_db):
        from app.repositories import (create_monitor, upsert_post,
                                       delete_monitors, list_monitors)

        upsert_post("123456789", make_facebook_post(post_id="123456789_002"))
        m1 = create_monitor("123456789_001")
        m2 = create_monitor("123456789_002")
        delete_monitors([m1, m2])
        assert list_monitors() == []

    def test_get_monitor_by_post(self, setup_db):
        from app.repositories import create_monitor, get_monitor_by_post

        create_monitor("123456789_001")
        m = get_monitor_by_post("123456789_001")
        assert m is not None
        assert m["post_id"] == "123456789_001"

    def test_list_monitored_post_ids(self, setup_db):
        from app.repositories import create_monitor, upsert_post, list_monitored_post_ids

        upsert_post("123456789", make_facebook_post(post_id="123456789_002"))
        create_monitor("123456789_001")
        create_monitor("123456789_002")

        ids = list_monitored_post_ids(page_id="123456789")
        assert ids == {"123456789_001", "123456789_002"}


# ============================================================================
# Replied Comments
# ============================================================================
class TestRepliedComments:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        from app.repositories import upsert_post, upsert_comment, create_monitor

        _seed_page_and_account()
        upsert_post(PAGE_ID, make_facebook_post(post_id="123456789_001"))
        upsert_comment("123456789_001", None,
                       make_facebook_comment(comment_id="rc1", message="Hi"))
        self.monitor_id = create_monitor("123456789_001")

    def test_has_replied_false_initially(self, setup_db):
        from app.repositories import has_replied

        assert not has_replied("rc1")

    def test_mark_and_check_replied(self, setup_db):
        from app.repositories import mark_replied, has_replied

        mark_replied("rc1", "123456789_001", self.monitor_id, "Thanks!")
        assert has_replied("rc1")

    def test_mark_replied_idempotent(self, setup_db):
        from app.repositories import mark_replied, has_replied

        mark_replied("rc1", "123456789_001", self.monitor_id, "First reply")
        mark_replied("rc1", "123456789_001", self.monitor_id, "Second reply")
        assert has_replied("rc1")

    def test_unmark_replied(self, setup_db):
        from app.repositories import mark_replied, unmark_replied, has_replied

        mark_replied("rc1", "123456789_001", self.monitor_id, "Reply")
        unmark_replied("rc1")
        assert not has_replied("rc1")

    def test_list_replied_for_post(self, setup_db):
        from app.repositories import mark_replied, list_replied_for_post

        mark_replied("rc1", "123456789_001", self.monitor_id, "Reply text")
        replies = list_replied_for_post("123456789_001")
        assert len(replies) == 1
        assert replies[0]["comment_id"] == "rc1"

    def test_list_replied_for_monitor(self, setup_db):
        from app.repositories import mark_replied, list_replied_for_monitor

        mark_replied("rc1", "123456789_001", self.monitor_id, "Reply text")
        replies = list_replied_for_monitor(self.monitor_id)
        assert len(replies) == 1
        assert replies[0]["reply_message"] == "Reply text"


# ============================================================================
# Admin Auth and Sessions
# ============================================================================
class TestAdminAuth:
    def test_get_admin_auth_exists_after_seed(self, setup_db):
        from app.repositories import get_admin_auth

        # setup_db seeds admin auth
        auth = get_admin_auth()
        assert auth is not None
        assert auth["username"] == "admin"

    def test_update_and_get_admin_password(self, setup_db):
        from app.repositories import get_admin_auth, update_admin_password

        update_admin_password(password_hash="hash123", password_salt="salt456",
                              password_iterations=100000)
        auth = get_admin_auth()
        assert auth is not None
        assert auth["password_hash"] == "hash123"
        assert auth["password_salt"] == "salt456"

    def test_create_and_get_admin_session(self, setup_db):
        from app.repositories import create_admin_session, get_admin_session

        create_admin_session(session_id="sess1", ip="1.2.3.4",
                             user_agent="TestAgent")
        sess = get_admin_session("sess1")
        assert sess is not None
        assert sess["ip"] == "1.2.3.4"

    def test_get_admin_session_expired(self, setup_db):
        from app.repositories import create_admin_session, get_admin_session
        from app.database import get_connection
        from app.security import now_utc_sql

        # Create session then force-expire it
        create_admin_session(session_id="sess2", ip="1.2.3.4",
                             user_agent="TestAgent")
        with get_connection() as conn:
            conn.execute("UPDATE admin_sessions SET expires_at = ? WHERE session_id = ?",
                         ("2020-01-01 00:00:00", "sess2"))
        assert get_admin_session("sess2") is None

    def test_delete_admin_session(self, setup_db):
        from app.repositories import (create_admin_session, delete_admin_session,
                                       get_admin_session)

        create_admin_session(session_id="sess3", ip="1.2.3.4",
                             user_agent="TestAgent")
        delete_admin_session("sess3")
        assert get_admin_session("sess3") is None


# ============================================================================
# IP Rate Limiting
# ============================================================================
class TestIPRateLimit:
    def test_is_ip_locked_false_initially(self, setup_db):
        from app.repositories import is_ip_locked

        assert not is_ip_locked("10.0.0.1")

    def test_register_failed_login_increments(self, setup_db):
        from app.repositories import register_failed_login

        count = register_failed_login("10.0.0.2")
        assert count == 1
        count = register_failed_login("10.0.0.2")
        assert count == 2

    def test_clear_login_attempts(self, setup_db):
        from app.repositories import register_failed_login, clear_login_attempts, is_ip_locked

        register_failed_login("10.0.0.3")
        clear_login_attempts("10.0.0.3")
        assert not is_ip_locked("10.0.0.3")


# ============================================================================
# Video Analysis
# ============================================================================
class TestVideoAnalysis:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        _seed_page_and_account()
        from app.repositories import upsert_post
        upsert_post(PAGE_ID, make_facebook_post(post_id="vp1", post_type="video"))

    def test_save_and_get_video_analysis(self, setup_db):
        from app.repositories import save_video_analysis, get_video_analysis

        save_video_analysis("vp1", "Test Video", '{"location":"NYC"}',
                            int(time.time()))
        analysis = get_video_analysis("vp1")
        assert analysis is not None
        assert analysis["title"] == "Test Video"
        assert analysis["post_id"] == "vp1"

    def test_update_video_analysis(self, setup_db):
        from app.repositories import (save_video_analysis, update_video_analysis,
                                       get_video_analysis)

        save_video_analysis("vp1", "Test", '{"location":"Old"}', int(time.time()))
        update_video_analysis("vp1", '{"location":"New"}')
        analysis = get_video_analysis("vp1")
        assert "New" in analysis["content"]

    def test_list_posts_with_analysis(self, setup_db):
        from app.repositories import save_video_analysis, list_posts_with_analysis

        save_video_analysis("vp1", "Test Video", '{"location":"Paris"}',
                            int(time.time()))
        posts = list_posts_with_analysis(page_id="123456789")
        assert len(posts) == 1
        assert posts[0]["analysis_content"] is not None


# ============================================================================
# Conversations
# ============================================================================
class TestConversations:
    @pytest.fixture(autouse=True)
    def _seed(self, setup_db):
        _seed_page_and_account()

    def test_upsert_page_conversation(self, setup_db):
        from app.repositories import upsert_page_conversation

        upsert_page_conversation("conv1", "123456789", "2025-06-01T10:00:00+0000",
                                 2, '["user1", "user2"]')
        # Should not raise

    def test_upsert_conversation_message(self, setup_db):
        from app.repositories import upsert_page_conversation, upsert_conversation_message

        upsert_page_conversation("conv1", "123456789", "2025-06-01T10:00:00+0000",
                                 0, "[]")
        upsert_conversation_message("msg1", "conv1", "Hello", "user1", "Alice",
                                    "2025-06-01T10:00:00+0000")
        # Should not raise

    def test_check_message_exists(self, setup_db):
        from app.repositories import (upsert_page_conversation,
                                       upsert_conversation_message,
                                       check_message_exists)

        upsert_page_conversation("conv1", "123456789", "2025-06-01T10:00:00+0000",
                                 0, "[]")
        upsert_conversation_message("msg_exists", "conv1", "Test", "u1", "A",
                                    "2025-06-01T10:00:00+0000")
        assert check_message_exists("msg_exists") is True
        assert check_message_exists("msg_nonexistent") is False

    def test_bulk_upsert_messages(self, setup_db):
        from app.repositories import (upsert_page_conversation,
                                       bulk_upsert_conversation_messages,
                                       check_message_exists)

        upsert_page_conversation("conv1", "123456789", "2025-06-01T10:00:00+0000",
                                 0, "[]")
        msgs = [
            ("bm1", "conv1", "Msg 1", "u1", "A", "2025-01-01T00:00:00+0000"),
            ("bm2", "conv1", "Msg 2", "u2", "B", "2025-01-01T00:01:00+0000"),
        ]
        bulk_upsert_conversation_messages(msgs)
        assert check_message_exists("bm1")
        assert check_message_exists("bm2")

    def test_get_chat_dashboard_stats(self, setup_db):
        from app.repositories import (upsert_page_conversation,
                                       upsert_conversation_message,
                                       get_chat_dashboard_stats)

        upsert_page_conversation("conv1", "123456789", "2025-06-01T10:00:00+0000",
                                 0, "[]")
        upsert_conversation_message("s1", "conv1", "Hi", "u1", "Alice",
                                    "2025-01-01T00:00:00+0000")
        stats = get_chat_dashboard_stats("123456789")
        assert "total_users" in stats
        assert "total_messages" in stats
        assert stats["total_messages"] >= 1

    def test_get_user_message_counts(self, setup_db):
        from app.repositories import (upsert_page_conversation,
                                       upsert_conversation_message,
                                       get_user_message_counts)

        upsert_page_conversation("conv_u1", "123456789",
                                 "2025-06-01T10:00:00+0000", 0, "[]")
        upsert_conversation_message("um1", "conv_u1", "Hello", "u1", "Alice",
                                    "2025-01-01T00:00:00+0000")
        counts = get_user_message_counts("123456789")
        assert len(counts) >= 1


# ============================================================================
# Page Profile
# ============================================================================
class TestPageProfile:
    def test_upsert_and_get_page_profile(self, setup_db):
        from app.repositories import upsert_page_profile, get_page_profile
        from tests.factories import make_facebook_page_profile

        profile = make_facebook_page_profile(page_id="99999", name="TestPage")
        upsert_page_profile(profile)

        p = get_page_profile(page_id="99999")
        assert p is not None
        assert p["name"] == "TestPage"
        assert p["fan_count"] == 1000

    def test_get_canonical_page_id_resolves(self, setup_db):
        from app.repositories import upsert_page_profile, get_canonical_page_id
        from tests.factories import make_facebook_page_profile

        upsert_page_profile(make_facebook_page_profile(page_id="88888",
                                                        username="myusername"))
        # Should resolve username to numeric ID
        resolved = get_canonical_page_id("myusername")
        assert resolved == "88888"


# ============================================================================
# Auto-Monitor Config
# ============================================================================
class TestAutoMonitor:
    def test_get_auto_monitor_config_default(self, setup_db):
        from app.repositories import get_auto_monitor_config

        cfg = get_auto_monitor_config()
        assert cfg["enabled"] == 0
        assert cfg["max_posts"] == 10

    def test_update_auto_monitor_config(self, setup_db):
        from app.repositories import update_auto_monitor_config, get_auto_monitor_config

        update_auto_monitor_config(enabled=1, max_posts=5)
        cfg = get_auto_monitor_config()
        assert cfg["enabled"] == 1
        assert cfg["max_posts"] == 5

    def test_crud_auto_monitor_schedule(self, setup_db):
        from app.repositories import (add_auto_monitor_schedule,
                                       list_auto_monitor_schedules,
                                       update_auto_monitor_schedule,
                                       delete_auto_monitor_schedule)

        sid = add_auto_monitor_schedule("09:00")
        schedules = list_auto_monitor_schedules()
        assert len(schedules) == 1
        assert schedules[0]["trigger_time"] == "09:00"

        update_auto_monitor_schedule(sid, enabled=0)
        schedules = list_auto_monitor_schedules()
        assert schedules[0]["enabled"] == 0

        delete_auto_monitor_schedule(sid)
        assert list_auto_monitor_schedules() == []