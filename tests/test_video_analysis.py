"""
测试脚本：从 Facebook 获取视频直链，发送给火山引擎 doubao-seed-1.8 进行 3W1H 内容分析。

用法:
  uv run python tests/test_video_analysis.py
"""

import asyncio
import base64
import json
import sqlite3
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "facebookmsg.sqlite3"
GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

ANALYSIS_PROMPT = """你是一个专业的视频内容分析师。请仔细观看这段视频，重点关注画面中的背景景物、
环境特征、建筑风格、植被、天气、光线等视觉细节，经过深入思考后再给出分析结果。

请用中文回答以下问题：

1. **拍摄地点推测**：根据画面中出现的建筑风格、街道布局、招牌文字、植被类型、
车辆型号、餐食特征、天气光线等背景细节，推测视频拍摄的具体地点。
你必须给出一个明确的结论，格式为：国家 - 省/州 - 城市。
不要给出范围或多个候选，直接写出你认为最可能的具体地点，
然后用 1-2 句话说明你的判断依据（如植被类型、建筑风格、文字语言、饮食特征等）。

2. **人物行为分析**：视频中的人物在做什么？包括具体行为、穿着打扮、表情状态、
与周围环境或他人的互动方式。

3. **场景环境描述**：描述视频中的整体环境氛围，包括背景中出现的 notable 事物
（如地标建筑、自然景观、交通工具、人群密度等）。

请基于你观察到的具体画面细节进行分析，不要凭空猜测。每个问题 2-3 句话。"""


# ---------------------------------------------------------------------------
# DB 读取
# ---------------------------------------------------------------------------
def load_config_from_db():
    """从数据库读取 LLM 配置和 Facebook page_access_token。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # LLM 配置
    model_row = conn.execute("SELECT * FROM model_configs WHERE id = 1").fetchone()
    if not model_row:
        raise RuntimeError("model_configs 表中没有配置")
    ai_base = model_row["ai_api_base_url"]
    ai_key = model_row["ai_api_key"]
    ai_model = model_row["ai_model"]

    # Facebook 配置
    account_row = conn.execute("SELECT * FROM account_configs LIMIT 1").fetchone()
    if not account_row:
        raise RuntimeError("account_configs 表中没有账号")
    fb_token = account_row["page_access_token"]

    conn.close()
    return ai_base, ai_key, ai_model, fb_token


def get_latest_video_post():
    """从数据库获取最新的视频帖子。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, raw_json, message FROM posts WHERE type = 'video' ORDER BY created_time DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        raise RuntimeError("数据库中没有视频帖子")

    raw = json.loads(row["raw_json"])
    video_id = raw.get("video_id")
    if not video_id:
        raise RuntimeError(f"帖子 {row['id']} 的 raw_json 中没有 video_id")

    return {
        "post_id": row["id"],
        "video_id": video_id,
        "message": row["message"] or "",
    }


# ---------------------------------------------------------------------------
# Facebook Graph API — 获取视频下载直链
# ---------------------------------------------------------------------------
async def get_video_source_url(video_id: str, fb_token: str) -> dict:
    """通过 Facebook Graph API 获取视频的 source 直链。"""
    url = f"{GRAPH_BASE}/{video_id}"
    params = {
        "fields": "source,format,permalink_url,description,length",
        "access_token": fb_token,
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# 火山引擎 LLM — 视频 URL 直传
# ---------------------------------------------------------------------------
async def analyze_video_with_url(ai_base: str, ai_key: str, ai_model: str, video_url: str) -> str:
    """直接用视频 URL 调用 LLM（Chat API）。"""
    endpoint = ai_base.rstrip("/") + "/chat/completions"

    payload = {
        "model": ai_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": video_url,
                            "fps": 2,
                        },
                    },
                    {
                        "type": "text",
                        "text": ANALYSIS_PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 1600,
        "temperature": 0.2,
        "enable_thinking": True,
    }

    headers = {
        "Authorization": f"Bearer {ai_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 降级方案 — 下载视频后 base64 编码发送
# ---------------------------------------------------------------------------
async def download_video(video_url: str) -> bytes:
    """下载视频文件。"""
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        return resp.content


async def analyze_video_with_base64(ai_base: str, ai_key: str, ai_model: str, video_bytes: bytes) -> str:
    """用 base64 编码的视频调用 LLM（Chat API）。"""
    b64 = base64.b64encode(video_bytes).decode()
    data_url = f"data:video/mp4;base64,{b64}"

    endpoint = ai_base.rstrip("/") + "/chat/completions"

    payload = {
        "model": ai_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": data_url,
                            "fps": 2,
                        },
                    },
                    {
                        "type": "text",
                        "text": ANALYSIS_PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 1600,
        "temperature": 0.2,
        "enable_thinking": True,
    }

    headers = {
        "Authorization": f"Bearer {ai_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def main():
    print("=" * 60)
    print("视频内容分析测试")
    print("=" * 60)

    # 1. 读取配置
    print("\n[1/4] 从数据库读取配置...")
    ai_base, ai_key, ai_model, fb_token = load_config_from_db()
    print(f"  LLM 模型: {ai_model}")
    print(f"  LLM 地址: {ai_base}")

    # 2. 获取最新视频帖子
    print("\n[2/4] 获取最新视频帖子...")
    post = get_latest_video_post()
    print(f"  帖子 ID: {post['post_id']}")
    print(f"  视频 ID: {post['video_id']}")
    print(f"  帖子内容: {post['message'][:80]}...")

    # 3. 获取视频下载直链
    print("\n[3/4] 通过 Facebook Graph API 获取视频直链...")
    try:
        video_info = await get_video_source_url(post["video_id"], fb_token)
        video_source = video_info.get("source")
        permalink = video_info.get("permalink_url", "")
        description = video_info.get("description", "")
        length = video_info.get("length", 0)

        print(f"  直链: {video_source[:100] if video_source else '未获取到'}...")
        print(f"  永久链接: {permalink}")
        print(f"  视频时长: {length}s")

        if not video_source:
            print("  ⚠ Facebook API 未返回 source 字段，可能需要 page_access_token 有额外权限")
            print("  将尝试使用 permalink 作为降级方案...")
    except Exception as e:
        print(f"  ✗ 获取视频直链失败: {e}")
        video_source = None

    # 4. 发送给 LLM 分析
    print("\n[4/4] 发送视频给 LLM 进行 3W1H 分析...")

    result = None

    # 方案 A: 直接用 URL
    if video_source:
        print("  尝试方案 A: 视频 URL 直传...")
        try:
            result = await analyze_video_with_url(ai_base, ai_key, ai_model, video_source)
            print("  ✓ URL 直传成功")
        except httpx.HTTPStatusError as e:
            print(f"  ✗ URL 直传失败 (HTTP {e.response.status_code}): {e.response.text[:200]}")
        except Exception as e:
            print(f"  ✗ URL 直传失败: {e}")

    # 方案 B: 下载后 base64
    if not result and video_source:
        print("  尝试方案 B: 下载视频 → base64 编码...")
        try:
            video_bytes = await download_video(video_source)
            size_mb = len(video_bytes) / (1024 * 1024)
            print(f"  下载完成: {size_mb:.1f} MB")

            if size_mb > 50:
                print(f"  ✗ 视频超过 50MB 限制，跳过 base64 方案")
            else:
                result = await analyze_video_with_base64(ai_base, ai_key, ai_model, video_bytes)
                print("  ✓ base64 方式成功")
        except Exception as e:
            print(f"  ✗ base64 方式失败: {e}")

    # 输出结果
    print("\n" + "=" * 60)
    if result:
        print("LLM 分析结果:")
        print("=" * 60)
        print(result)
    else:
        print("✗ 所有方案均失败，未能获取分析结果")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
