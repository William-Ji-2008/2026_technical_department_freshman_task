# -*- coding: utf-8 -*-
"""
全局配置
========

本项目所有"可能需要调整的参数"都集中在这里。
每一项都可以用环境变量覆盖，也可以直接在文件里改默认值。

在 Windows PowerShell 里临时覆盖的例子：

    $env:CLF_PORT = "8080"          # 换端口
    $env:CLF_ITEM_NEED_REVIEW = "0" # 发布即上线，不需要审核
    python run_server.py
"""

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# 一、路径
# ---------------------------------------------------------------------------

# 项目根目录（app/config.py 的上两级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录：数据库文件放在这里
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 数据库文件路径
DB_PATH = Path(os.getenv("CLF_DB_PATH", str(DATA_DIR / "campus_lost_found.db")))


# ---------------------------------------------------------------------------
# 二、服务
# ---------------------------------------------------------------------------

# 监听地址：127.0.0.1 表示只有本机能访问；改成 0.0.0.0 局域网内其他电脑才能连
HOST = os.getenv("CLF_HOST", "127.0.0.1")

# 监听端口
PORT = int(os.getenv("CLF_PORT", "8000"))


# ---------------------------------------------------------------------------
# 三、安全
# ---------------------------------------------------------------------------

# 令牌签名密钥。正式使用时请换成一段足够随机的字符串！
# 生成方法： python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.getenv("CLF_SECRET_KEY", "please-change-this-secret-before-production")

# 令牌有效期（分钟），默认 24 小时
TOKEN_EXPIRE_MINUTES = int(os.getenv("CLF_TOKEN_EXPIRE_MINUTES", "1440"))


# ---------------------------------------------------------------------------
# 四、业务
# ---------------------------------------------------------------------------

# 分页默认条数与单页上限（防止有人传个超大数字把服务拖垮）
DEFAULT_PAGE_SIZE = int(os.getenv("CLF_DEFAULT_PAGE_SIZE", "10"))
MAX_PAGE_SIZE = int(os.getenv("CLF_MAX_PAGE_SIZE", "100"))

# 发布的失物/招领信息是否需要管理员审核
#   True  -> 发布后是 pending，管理员通过后才对外可见
#   False -> 直接是 approved
ITEM_NEED_REVIEW = os.getenv("CLF_ITEM_NEED_REVIEW", "1") == "1"

# 首次启动时自动创建的管理员账号
DEFAULT_ADMIN_USERNAME = os.getenv("CLF_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("CLF_ADMIN_PASSWORD", "admin123")
