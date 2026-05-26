"""Test data factories for FBIManager."""


def make_account(*, name="Test Page", token="EAA_test_token", verify="verify_me",
                  page_id="123456789", api_version="v25.0", is_active=1):
    """Create an account_config dict for seeding."""
    return {
        "name": name,
        "page_access_token": token,
        "verify_token": verify,
        "page_id": page_id,
        "api_version": api_version,
        "is_active": is_active,
    }


def make_model_config(*, reply_url="https://api.openai.com/v1", reply_key="sk-test",
                      reply_model="gpt-4", video_url="", video_key="", video_model="",
                      prompt_template="reply_prompt.j2"):
    """Create model config values."""
    return {
        "reply_api_base_url": reply_url,
        "reply_api_key": reply_key,
        "reply_model": reply_model,
        "video_api_base_url": video_url,
        "video_api_key": video_key,
        "video_model": video_model,
        "prompt_template": prompt_template,
    }


def make_facebook_post(post_id="123456789_001", message="Hello world",
                       created_time="2025-01-15T10:00:00+0000",
                       permalink_url="https://facebook.com/123456789/posts/001",
                       post_type="photo", full_picture=""):
    """Create a Facebook post dict (Graph API format)."""
    return {
        "id": post_id,
        "message": message,
        "created_time": created_time,
        "permalink_url": permalink_url,
        "type": post_type,
        "full_picture": full_picture,
    }


def make_facebook_comment(comment_id="001_comment_1", message="Nice post!",
                          author_id="987654321", author_name="Test User",
                          created_time="2025-01-15T11:00:00+0000",
                          parent_comment_id=None, attachment=None, story=""):
    """Create a Facebook comment dict (Graph API format)."""
    comment = {
        "id": comment_id,
        "message": message,
        "from": {"id": author_id, "name": author_name},
        "created_time": created_time,
        "attachment": attachment,
        "story": story,
    }
    if parent_comment_id:
        comment["parent"] = {"id": parent_comment_id}
    return comment


def make_facebook_page_profile(page_id="123456789", name="Test Page",
                               username="testpage", category="Community",
                               fan_count=1000):
    """Create a Facebook page profile dict."""
    return {
        "id": page_id,
        "name": name,
        "username": username,
        "link": f"https://facebook.com/{username or page_id}",
        "category": category,
        "fan_count": fan_count,
        "picture": {"data": {"url": f"https://graph.facebook.com/{page_id}/picture"}},
    }