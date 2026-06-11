"""
从数据库中导出与AI相关的评论到CSV文件。
关键词覆盖中英文常见AI相关词汇，排除欧洲语言中"ai"的误匹配。
"""

import csv
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "facebookmsg.sqlite3"
OUTPUT = Path(__file__).parent / "ai_comments.csv"

# AI相关关键词（不区分大小写）
# 策略：
#   - "AI" 必须是独立大写（排除意大利语/罗马尼亚语 "ai"="你有"）
#   - 中文关键词直接匹配
#   - 英文技术术语用 \b 边界
AI_KEYWORDS = [
    # AI - 仅匹配大写独立出现
    r"\bAI\b(?![\s,]*(?:durm|pati|veni|kome|bene|note|sto|stai|gvar|cvelo|amore|bongi))",
    r"\bA\.I\.\b",
    # 中文关键词
    r"人工智能",
    r"机器人",
    r"大模型",
    r"语言模型",
    r"机器学习",
    r"深度学习",
    r"自动回复",
    r"自动应答",
    r"智能回复",
    r"智能助手",
    r"语音助手",
    r"人工智障",
    r"神经网络",
    r"智能客服",
    r"自动化回复",
    r"ai生成", r"AI生成",
    r"ai写的", r"AI写的",
    r"ai回覆", r"AI回覆",
    r"ai回复", r"AI回复",
    r"机器回覆", r"機器回覆",
    r"自動回覆", r"自动回覆",
    r"智能回覆",
    # 英文技术术语
    r"\bChatGPT\b",
    r"\bGPT-\d\b",        # GPT-3, GPT-4 等
    r"\bGPT\b",
    r"\bLLM\b",
    r"\bOpenAI\b",
    r"\bClaude\b(?!\s+(?:est|es|is|sont|sono))",  # 排除法语/西语人名
    r"\bGemini\b",
    r"\bCopilot\b",
    r"\bSiri\b",
    r"\bAlexa\b",
    r"\bbot\b",
    r"\bchatbot\b",
    r"\bchat\s+bot\b",
    r"\bneural\b",
    r"\balgorithm\b",
    r"\bmachine\s+learning\b",
    r"\bdeep\s+learning\b",
    r"\bartificial\s+intelligence\b",
]

PATTERN = re.compile("|".join(AI_KEYWORDS), re.IGNORECASE)

# 二次过滤：排除明显误匹配的模式
FALSE_POSITIVE_PATTERNS = [
    # 意大利语/罗马尼亚语 "ai" + 动词变位
    re.compile(r"\bai\s+(durm|pati|veni|kome|bene|note|sto|stai|gvar|cvelo|amore|bongi|durmit)", re.IGNORECASE),
    re.compile(r"\bai\s+un\b", re.IGNORECASE),  # "ai un" = "你有一个"
    # 法语 "ai" 作为 avoir 变位
    re.compile(r"\bj['']?ai\b", re.IGNORECASE),
    # "ai" 后面跟常见动词（排除真正讨论AI的上下文）
    re.compile(r"\bai\s+(fost|avea|face|lua|dat|mers|venit|zis|vazut)\b", re.IGNORECASE),
]


def is_false_positive(message: str) -> bool:
    """检查是否为误匹配"""
    for fp in FALSE_POSITIVE_PATTERNS:
        if fp.search(message):
            return True
    return False


def main():
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            c.id          AS comment_id,
            c.message     AS comment_message,
            c.author_id,
            c.created_time AS comment_time,
            c.post_id,
            p.type        AS post_type,
            p.created_time AS post_time
        FROM comments c
        INNER JOIN posts p ON p.id = c.post_id
        WHERE c.message IS NOT NULL AND c.message != ''
          AND p.page_id = '959200767287091'
          AND c.author_id != '959200767287091'
        ORDER BY c.created_time DESC
        """
    ).fetchall()

    print(f"数据库中共有 {len(rows)} 条评论")

    matched = []
    for row in rows:
        msg = row["comment_message"] or ""
        if PATTERN.search(msg) and not is_false_positive(msg):
            matched.append(row)

    print(f"匹配AI关键词的评论: {len(matched)} 条")

    if not matched:
        print("没有找到相关评论，未生成CSV。")
        return

    with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "comment_id",
            "author_id",
            "comment_time",
            "comment_message",
            "post_id",
            "post_type",
            "post_time",
        ])
        for row in matched:
            writer.writerow([
                row["comment_id"],
                row["author_id"],
                row["comment_time"],
                row["comment_message"],
                row["post_id"],
                row["post_type"],
                row["post_time"],
            ])

    print(f"已导出到: {OUTPUT}")

    # 打印前10条预览
    print("\n--- 前10条预览 ---")
    for i, row in enumerate(matched[:10], 1):
        msg = (row["comment_message"] or "")[:100].replace("\n", " ")
        print(f"{i}. [{row['author_id']}] {msg}")

    conn.close()


if __name__ == "__main__":
    main()
