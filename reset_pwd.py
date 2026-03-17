import os
import sqlite3
from pathlib import Path
from app.security import hash_password, generate_salt, PBKDF2_ITERATIONS, is_strong_password

# 数据库路径
DB_PATH = Path("data/facebookmsg.sqlite3")

def reset_password():
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    
    if not password:
        print("错误: 请先设置环境变量 ADMIN_PASSWORD")
        return

    if not is_strong_password(password):
        print("错误: 密码强度不足（需16位以上，含大小写字母、数字和特殊字符）")
        return

    if not DB_PATH.exists():
        print(f"错误: 找不到数据库文件 {DB_PATH}")
        return

    salt = generate_salt()
    pw_hash = hash_password(password, salt, PBKDF2_ITERATIONS)

    conn = sqlite3.connect(DB_PATH)
    try:
        # 更新管理员账号 (id=1)
        conn.execute("""
            UPDATE admin_auth 
            SET password_hash = ?, 
                password_salt = ?, 
                password_iterations = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (pw_hash, salt, PBKDF2_ITERATIONS))
        conn.commit()
        print("成功: 管理员密码已重置！")
    except Exception as e:
        print(f"失败: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    reset_password()
