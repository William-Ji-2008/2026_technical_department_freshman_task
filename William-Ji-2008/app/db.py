# -*- coding: utf-8 -*-
"""
数据库模块（SQLite）
====================

没有用 SQLAlchemy，而是直接用标准库的 sqlite3 写 SQL。
好处是零依赖、看得见每一句 SQL；代价是要自己管理连接和事务。

本项目一共 5 张表：
    users       用户（学生 / 管理员）
    categories  物品分类
    items       失物 / 招领信息（核心表）
    claims      认领申请
    comments    留言

连接策略：每个请求打开一个新连接，用完就关（见 get_conn）。
这么做比"全局共享一个连接 + 加锁"更简单，也不会踩到
sqlite3 的线程限制问题；对校园级别的访问量来说性能完全够用。
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from app import config, security


# ---------------------------------------------------------------------------
# 建表语句
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,                 -- 登录名，唯一
    password_hash TEXT    NOT NULL,                        -- 密码哈希，绝不存明文
    real_name     TEXT,                                    -- 真实姓名
    student_no    TEXT    UNIQUE,                          -- 学号 / 工号
    phone         TEXT,                                    -- 手机号
    email         TEXT,                                    -- 邮箱
    role          TEXT    NOT NULL DEFAULT 'user',         -- user / admin
    status        TEXT    NOT NULL DEFAULT 'active',       -- active / banned
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type     TEXT    NOT NULL,                        -- lost / found
    title         TEXT    NOT NULL,
    description   TEXT,
    category_id   INTEGER REFERENCES categories(id),
    location      TEXT,                                    -- 丢失 / 拾获地点
    event_time    TEXT,                                    -- 丢失 / 拾获时间
    contact       TEXT,                                    -- 公开联系方式
    reward        TEXT,                                    -- 酬谢说明
    images        TEXT,                                    -- 图片地址，JSON 数组字符串
    status        TEXT    NOT NULL DEFAULT 'pending',      -- pending/approved/rejected/closed
    reject_reason TEXT,                                    -- 驳回原因
    view_count    INTEGER NOT NULL DEFAULT 0,
    publisher_id  INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_status    ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_type      ON items(item_type);
CREATE INDEX IF NOT EXISTS idx_items_publisher ON items(publisher_id);
CREATE INDEX IF NOT EXISTS idx_items_category  ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_items_created   ON items(created_at);

CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    applicant_id  INTEGER NOT NULL REFERENCES users(id),
    description   TEXT    NOT NULL,                        -- 认领说明
    contact       TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',      -- pending/approved/rejected
    review_remark TEXT,                                    -- 审核备注
    reviewer_id   INTEGER REFERENCES users(id),
    created_at    TEXT    NOT NULL,
    reviewed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_claims_item      ON claims(item_id);
CREATE INDEX IF NOT EXISTS idx_claims_applicant ON claims(applicant_id);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_item ON comments(item_id);
"""


# 首次启动时写入的默认分类：(名称, 说明)
DEFAULT_CATEGORIES = [
    ("证件卡类", "校园卡、学生证、身份证、银行卡等"),
    ("电子产品", "手机、耳机、充电宝、U 盘、数据线等"),
    ("书籍资料", "课本、笔记本、复习资料、实验报告等"),
    ("钥匙", "宿舍钥匙、自行车钥匙、钥匙串等"),
    ("雨伞水杯", "雨伞、水杯、保温杯等"),
    ("衣物配饰", "外套、帽子、围巾、眼镜等"),
    ("运动器材", "球拍、护具、运动手环等"),
    ("其他", "不属于以上分类的物品"),
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def now_str() -> str:
    """
    统一的时间格式：'2026-10-07 15:30:00'。

    SQLite 没有专门的日期类型，把时间存成这种定长字符串有个好处：
    直接按字符串排序/比较，就等于按时间排序/比较。
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    """打开一个数据库连接。"""
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10)
    # row_factory 让查询结果支持 row["列名"] 这种方式取值，比按序号取清晰得多
    conn.row_factory = sqlite3.Row
    # SQLite 默认不启用外键约束，必须每次连接后手动打开，
    # 否则 ON DELETE CASCADE 不会生效
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    """
    数据库连接的上下文管理器，用法：

        with get_conn() as conn:
            conn.execute(...)

    正常结束时自动 commit；中途抛异常则 rollback，保证不会写半截数据。
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表并写入初始数据（管理员账号 + 默认分类）。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)

        # ---- 默认管理员 ----
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
        ).fetchone()
        if row["c"] == 0:
            timestamp = now_str()
            conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, real_name, role, status, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 'active', ?, ?)
                """,
                (
                    config.DEFAULT_ADMIN_USERNAME,
                    security.hash_password(config.DEFAULT_ADMIN_PASSWORD),
                    "系统管理员",
                    timestamp,
                    timestamp,
                ),
            )
            print(
                f"[初始化] 已创建默认管理员："
                f"{config.DEFAULT_ADMIN_USERNAME} / {config.DEFAULT_ADMIN_PASSWORD}"
                f"（正式使用前请改密码）"
            )

        # ---- 默认分类 ----
        row = conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()
        if row["c"] == 0:
            timestamp = now_str()
            for index, (name, description) in enumerate(DEFAULT_CATEGORIES):
                conn.execute(
                    "INSERT INTO categories (name, description, sort_order, created_at) VALUES (?, ?, ?, ?)",
                    (name, description, index, timestamp),
                )
            print(f"[初始化] 已写入 {len(DEFAULT_CATEGORIES)} 个默认分类")
