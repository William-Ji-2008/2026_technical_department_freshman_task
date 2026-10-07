# -*- coding: utf-8 -*-
"""
业务接口
========

这个文件把所有 HTTP 接口和它们的业务逻辑集中在一起，分五组：

    认证     注册 / 登录 / 个人信息
    分类     分类的增删改查
    信息     失物招领的发布、搜索、详情、修改、删除、留言
    认领     提交认领申请、发布者审核
    后台     管理员审核信息、管理用户、看统计

每个接口上面都有 @router.route(...) 装饰器，标明了方法、路径和权限要求；
函数内部只关心业务规则，鉴权和参数解析交给 web.py 里的框架处理。
"""

import json
from typing import Optional

from app import db, security
from app.config import ITEM_NEED_REVIEW
from app.web import ApiError, Response, Router, make_page, read_pagination


# ---------------------------------------------------------------------------
# 一、状态常量
# ---------------------------------------------------------------------------

class ItemType:
    """信息类型：在找东西，还是招领东西。"""

    LOST = "lost"
    FOUND = "found"
    ALL = (LOST, FOUND)
    LABELS = {LOST: "寻物启事", FOUND: "失物招领"}


class ItemStatus:
    """信息状态。"""

    PENDING = "pending"     # 待审核
    APPROVED = "approved"   # 已发布
    REJECTED = "rejected"   # 已驳回
    CLOSED = "closed"       # 已完成
    ALL = (PENDING, APPROVED, REJECTED, CLOSED)
    LABELS = {
        PENDING: "待审核",
        APPROVED: "已发布",
        REJECTED: "已驳回",
        CLOSED: "已完成",
    }


class ClaimStatus:
    """认领申请状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALL = (PENDING, APPROVED, REJECTED)
    LABELS = {PENDING: "待处理", APPROVED: "已同意", REJECTED: "已拒绝"}


class UserRole:
    """用户角色。"""

    USER = "user"
    ADMIN = "admin"
    ALL = (USER, ADMIN)
    LABELS = {USER: "普通用户", ADMIN: "管理员"}


class UserStatus:
    """账号状态。"""

    ACTIVE = "active"
    BANNED = "banned"
    ALL = (ACTIVE, BANNED)
    LABELS = {ACTIVE: "正常", BANNED: "已封禁"}


# ---------------------------------------------------------------------------
# 二、参数校验小工具
# ---------------------------------------------------------------------------

def need_str(data: dict, field: str, label: str, min_len: int = 1, max_len: int = 200) -> str:
    """取一个必填的字符串字段，同时校验长度。"""
    value = data.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, f"{label}不能为空")
    if not isinstance(value, str):
        raise ApiError(400, f"{label}必须是字符串")
    value = value.strip()
    if len(value) < min_len:
        raise ApiError(400, f"{label}至少需要 {min_len} 个字符")
    if len(value) > max_len:
        raise ApiError(400, f"{label}最多 {max_len} 个字符")
    return value


def opt_str(data: dict, field: str, label: str, max_len: int = 200) -> Optional[str]:
    """取一个选填的字符串字段；传了空字符串就当作没填。"""
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, f"{label}必须是字符串")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len:
        raise ApiError(400, f"{label}最多 {max_len} 个字符")
    return value


def need_choice(data: dict, field: str, label: str, choices) -> str:
    """取一个必须在给定范围内的枚举值。"""
    value = data.get(field)
    if value not in choices:
        raise ApiError(400, f"{label}只能是 {', '.join(choices)} 中的一个")
    return value


def opt_int(data: dict, field: str, label: str) -> Optional[int]:
    """取一个选填的整数字段。"""
    value = data.get(field)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{label}必须是整数")


def opt_bool(data: dict, field: str, default: bool = False) -> bool:
    """取一个选填的布尔字段，兼容 true / "true" / 1 / "1" 这几种写法。"""
    if field not in data or data[field] is None:
        return default
    value = data[field]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    raise ApiError(400, f"{field} 必须是布尔值")


# ---------------------------------------------------------------------------
# 三、数据库行 -> 接口返回结构
# ---------------------------------------------------------------------------

def parse_images(raw) -> list:
    """把数据库里的 JSON 字符串还原成列表；数据坏了也不让接口报错。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def serialize_user(row) -> dict:
    """用户完整信息（不含密码哈希）。"""
    return {
        "id": row["id"],
        "username": row["username"],
        "real_name": row["real_name"],
        "student_no": row["student_no"],
        "phone": row["phone"],
        "email": row["email"],
        "role": row["role"],
        "role_label": UserRole.LABELS.get(row["role"], row["role"]),
        "status": row["status"],
        "status_label": UserStatus.LABELS.get(row["status"], row["status"]),
        "created_at": row["created_at"],
    }


def serialize_category(conn, category_id) -> Optional[dict]:
    """按 ID 取分类；不存在就返回 None。"""
    if not category_id:
        return None
    row = conn.execute(
        "SELECT * FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "sort_order": row["sort_order"],
    }


def serialize_item(conn, row) -> dict:
    """把 items 表的一行组装成完整结构（带上分类和发布者）。"""
    publisher = conn.execute(
        "SELECT id, username, real_name FROM users WHERE id = ?",
        (row["publisher_id"],),
    ).fetchone()

    return {
        "id": row["id"],
        "item_type": row["item_type"],
        "type_label": ItemType.LABELS.get(row["item_type"], row["item_type"]),
        "title": row["title"],
        "description": row["description"],
        "category_id": row["category_id"],
        "category": serialize_category(conn, row["category_id"]),
        "location": row["location"],
        "event_time": row["event_time"],
        "contact": row["contact"],
        "reward": row["reward"],
        "images": parse_images(row["images"]),
        "status": row["status"],
        "status_label": ItemStatus.LABELS.get(row["status"], row["status"]),
        "reject_reason": row["reject_reason"],
        "view_count": row["view_count"],
        "publisher_id": row["publisher_id"],
        "publisher": (
            {
                "id": publisher["id"],
                "username": publisher["username"],
                "real_name": publisher["real_name"],
            }
            if publisher
            else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def serialize_claim(conn, row, with_item: bool = False) -> dict:
    """把 claims 表的一行组装成返回结构。"""
    applicant = conn.execute(
        "SELECT id, username, real_name FROM users WHERE id = ?",
        (row["applicant_id"],),
    ).fetchone()

    data = {
        "id": row["id"],
        "item_id": row["item_id"],
        "applicant_id": row["applicant_id"],
        "applicant": (
            {
                "id": applicant["id"],
                "username": applicant["username"],
                "real_name": applicant["real_name"],
            }
            if applicant
            else None
        ),
        "description": row["description"],
        "contact": row["contact"],
        "status": row["status"],
        "status_label": ClaimStatus.LABELS.get(row["status"], row["status"]),
        "review_remark": row["review_remark"],
        "reviewer_id": row["reviewer_id"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
    }

    # 查"我提交的申请"时顺带带上信息标题，省得前端再查一次
    if with_item:
        item = conn.execute(
            "SELECT id, title, item_type, status FROM items WHERE id = ?",
            (row["item_id"],),
        ).fetchone()
        data["item"] = (
            {
                "id": item["id"],
                "title": item["title"],
                "item_type": item["item_type"],
                "status": item["status"],
            }
            if item
            else None
        )

    return data


def serialize_comment(conn, row) -> dict:
    """把 comments 表的一行组装成返回结构。"""
    user = conn.execute(
        "SELECT id, username, real_name FROM users WHERE id = ?",
        (row["user_id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "user_id": row["user_id"],
        "user": (
            {
                "id": user["id"],
                "username": user["username"],
                "real_name": user["real_name"],
            }
            if user
            else None
        ),
        "content": row["content"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# 四、权限辅助
# ---------------------------------------------------------------------------

def get_item_or_404(conn, item_id):
    """取信息，取不到抛 404。"""
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        raise ApiError(400, "信息 ID 必须是整数")
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ApiError(404, "该信息不存在或已被删除")
    return row


def ensure_can_view(item, user: Optional[dict]) -> None:
    """
    已发布 / 已完成的信息谁都能看；
    待审核、已驳回的只有发布者本人和管理员能看。
    """
    if item["status"] in (ItemStatus.APPROVED, ItemStatus.CLOSED):
        return
    if user and (user["id"] == item["publisher_id"] or user["role"] == UserRole.ADMIN):
        return
    raise ApiError(403, "该信息尚未通过审核，暂时无法查看")


def ensure_can_manage(item, user: dict) -> None:
    """只有发布者本人和管理员能改 / 删 / 关闭一条信息。"""
    if user["id"] == item["publisher_id"] or user["role"] == UserRole.ADMIN:
        return
    raise ApiError(403, "只能操作自己发布的信息")


# ---------------------------------------------------------------------------
# 五、路由表与当前用户解析
# ---------------------------------------------------------------------------

router = Router()


def resolve_user(request) -> Optional[dict]:
    """
    从请求头解析当前用户。

    返回 None 表示"未登录"：没带令牌、令牌无效或过期、用户被删或被封禁。
    框架会根据这个结果决定要不要拦下请求。
    """
    token = request.bearer_token()
    if not token:
        return None

    payload = security.decode_access_token(token)
    if not payload:
        return None

    subject = payload.get("sub")
    if subject is None:
        return None

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        return None

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    if row is None or row["status"] == UserStatus.BANNED:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# 六、基础接口
# ---------------------------------------------------------------------------

@router.route("GET", "/")
def service_info(request):
    """访问根路径时返回服务信息，用来确认服务确实起来了。"""
    return {
        "name": "校园失物招领系统 API（本地零依赖版）",
        "version": "1.0.0",
        "docs": "本项目没有网页界面，请用 run_demo.py 或自己的客户端调用接口",
        "hint": "所有接口都以 /api 开头，例如 GET /api/items",
    }


@router.route("GET", "/api/health")
def health(request):
    """健康检查，演示脚本启动服务后第一件事就是调它。"""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 七、认证：注册 / 登录 / 个人信息
# ---------------------------------------------------------------------------

@router.route("POST", "/api/auth/register")
def register(request):
    """注册普通用户。管理员账号由系统初始化时自动创建，不能通过接口注册。"""
    data = request.json
    username = need_str(data, "username", "用户名", min_len=3, max_len=50)
    password = need_str(data, "password", "密码", min_len=6, max_len=64)
    real_name = opt_str(data, "real_name", "真实姓名", max_len=50)
    student_no = opt_str(data, "student_no", "学号", max_len=30)
    phone = opt_str(data, "phone", "手机号", max_len=20)
    email = opt_str(data, "email", "邮箱", max_len=100)

    if " " in username:
        raise ApiError(400, "用户名不能包含空格")

    with db.get_conn() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if exists:
            raise ApiError(400, "该用户名已被注册")

        if student_no:
            exists = conn.execute(
                "SELECT id FROM users WHERE student_no = ?", (student_no,)
            ).fetchone()
            if exists:
                raise ApiError(400, "该学号已被注册")

        timestamp = db.now_str()
        cursor = conn.execute(
            """
            INSERT INTO users
                (username, password_hash, real_name, student_no, phone, email,
                 role, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'user', 'active', ?, ?)
            """,
            (
                username,
                security.hash_password(password),
                real_name,
                student_no,
                phone,
                email,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    return Response(serialize_user(row), 201)


@router.route("POST", "/api/auth/login")
def login(request):
    """登录，成功后返回令牌。之后请求把这个令牌放进 Authorization 头。"""
    data = request.json
    username = need_str(data, "username", "用户名", min_len=1, max_len=50)
    password = need_str(data, "password", "密码", min_len=1, max_len=64)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

    # 用户不存在和密码错误返回同一句提示，避免被用来枚举系统里有哪些用户名
    if row is None or not security.verify_password(password, row["password_hash"]):
        raise ApiError(401, "用户名或密码错误")

    if row["status"] == UserStatus.BANNED:
        raise ApiError(403, "账号已被封禁，请联系管理员")

    token = security.create_access_token(
        row["id"],
        extra={"username": row["username"], "role": row["role"]},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24,
        "user": serialize_user(row),
    }


@router.route("GET", "/api/auth/me", auth=True)
def read_me(request):
    """获取当前登录用户的信息。"""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (request.user["id"],)
        ).fetchone()
    return serialize_user(row)


@router.route("PUT", "/api/auth/me", auth=True)
def update_me(request):
    """修改自己的资料。用户名、角色、密码都不在这里改。"""
    data = request.json
    real_name = opt_str(data, "real_name", "真实姓名", max_len=50)
    phone = opt_str(data, "phone", "手机号", max_len=20)
    email = opt_str(data, "email", "邮箱", max_len=100)
    student_no = opt_str(data, "student_no", "学号", max_len=30)

    with db.get_conn() as conn:
        if student_no:
            duplicated = conn.execute(
                "SELECT id FROM users WHERE student_no = ? AND id != ?",
                (student_no, request.user["id"]),
            ).fetchone()
            if duplicated:
                raise ApiError(400, "该学号已被其他账号使用")

        # 用 COALESCE 的好处：没传的字段保持原值，不会把已有资料清空
        conn.execute(
            """
            UPDATE users
               SET real_name  = COALESCE(?, real_name),
                   phone      = COALESCE(?, phone),
                   email      = COALESCE(?, email),
                   student_no = COALESCE(?, student_no),
                   updated_at = ?
             WHERE id = ?
            """,
            (
                real_name,
                phone,
                email,
                student_no,
                db.now_str(),
                request.user["id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (request.user["id"],)
        ).fetchone()

    return serialize_user(row)


@router.route("PUT", "/api/auth/me/password", auth=True)
def change_password(request):
    """修改密码：必须先验证原密码，防止令牌被盗后直接改密码。"""
    data = request.json
    old_password = need_str(data, "old_password", "原密码", min_len=1, max_len=64)
    new_password = need_str(data, "new_password", "新密码", min_len=6, max_len=64)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (request.user["id"],)
        ).fetchone()

        if not security.verify_password(old_password, row["password_hash"]):
            raise ApiError(400, "原密码不正确")
        if old_password == new_password:
            raise ApiError(400, "新密码不能与原密码相同")

        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (
                security.hash_password(new_password),
                db.now_str(),
                request.user["id"],
            ),
        )

    return {"message": "密码修改成功，请使用新密码重新登录"}


# ---------------------------------------------------------------------------
# 八、分类
# ---------------------------------------------------------------------------

@router.route("GET", "/api/categories")
def list_categories(request):
    """分类列表，公开接口，前端用来渲染筛选下拉框。"""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM categories ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "sort_order": row["sort_order"],
        }
        for row in rows
    ]


@router.route("POST", "/api/categories", admin=True)
def create_category(request):
    """新增分类（管理员）。"""
    data = request.json
    name = need_str(data, "name", "分类名称", min_len=1, max_len=50)
    description = opt_str(data, "description", "分类说明", max_len=200)
    sort_order = opt_int(data, "sort_order", "排序值")

    with db.get_conn() as conn:
        if conn.execute(
            "SELECT id FROM categories WHERE name = ?", (name,)
        ).fetchone():
            raise ApiError(400, "该分类名称已存在")

        cursor = conn.execute(
            "INSERT INTO categories (name, description, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (name, description, sort_order if sort_order is not None else 0, db.now_str()),
        )
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    return Response(
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "sort_order": row["sort_order"],
        },
        201,
    )


@router.route("PUT", "/api/categories/{category_id}", admin=True)
def update_category(request, category_id):
    """修改分类（管理员），只传要改的字段。"""
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ApiError(400, "分类 ID 必须是整数")

    data = request.json
    name = opt_str(data, "name", "分类名称", max_len=50)
    description = opt_str(data, "description", "分类说明", max_len=200)
    sort_order = opt_int(data, "sort_order", "排序值")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "分类不存在")

        if name and name != row["name"]:
            if conn.execute(
                "SELECT id FROM categories WHERE name = ?", (name,)
            ).fetchone():
                raise ApiError(400, "该分类名称已存在")

        conn.execute(
            """
            UPDATE categories
               SET name        = COALESCE(?, name),
                   description = COALESCE(?, description),
                   sort_order  = COALESCE(?, sort_order)
             WHERE id = ?
            """,
            (name, description, sort_order, category_id),
        )
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "sort_order": row["sort_order"],
    }


@router.route("DELETE", "/api/categories/{category_id}", admin=True)
def delete_category(request, category_id):
    """
    删除分类（管理员）。

    如果还有信息挂在这个分类下就拒绝删除，否则那些信息的
    category_id 会指向一个不存在的分类，前端显示会出问题。
    """
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ApiError(400, "分类 ID 必须是整数")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "分类不存在")

        used = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE category_id = ?", (category_id,)
        ).fetchone()["c"]
        if used > 0:
            raise ApiError(400, f"该分类下还有 {used} 条信息，请先修改这些信息的分类")

        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    return {"message": "分类已删除"}


# ---------------------------------------------------------------------------
# 九、失物 / 招领信息
# ---------------------------------------------------------------------------
# 注意下面的注册顺序：/api/items/mine 必须写在 /api/items/{item_id} 前面，
# 否则路由匹配时会把 "mine" 当成 item_id，导致接口用不了。

@router.route("GET", "/api/items")
def list_items(request):
    """
    公开的信息列表，支持关键字、类型、分类、地点筛选和分页。

    默认只返回审核通过的信息；想按别的状态查，用 ?status=pending，
    但那样查出来的结果可能是别人的待审核内容——所以这里做了限制：
    只有管理员才允许查非 approved 的状态。
    """
    page, size, offset = read_pagination(request)

    status = request.q("status", ItemStatus.APPROVED)
    if status not in ItemStatus.ALL:
        raise ApiError(400, f"status 只能是 {', '.join(ItemStatus.ALL)} 中的一个")

    # 只有管理员能查"非公开状态"的信息（用于后台审核页）
    if status != ItemStatus.APPROVED:
        user = resolve_user(request)
        if not user or user["role"] != UserRole.ADMIN:
            raise ApiError(403, "只有管理员可以按该状态查询信息")

    item_type = request.q("item_type")
    if item_type and item_type not in ItemType.ALL:
        raise ApiError(400, f"item_type 只能是 {', '.join(ItemType.ALL)} 中的一个")

    category_id = request.q_int("category_id")
    location = request.q("location")
    keyword = request.q("keyword")
    sort = request.q("sort", "latest")

    where = ["i.status = ?"]
    params: list = [status]

    if item_type:
        where.append("i.item_type = ?")
        params.append(item_type)

    if category_id:
        where.append("i.category_id = ?")
        params.append(category_id)

    if location:
        where.append("i.location LIKE ?")
        params.append(f"%{location}%")

    if keyword:
        # 标题、描述、地点任意命中即可，所以要用 OR，外面加括号避免破坏其他条件
        where.append(
            "(i.title LIKE ? OR i.description LIKE ? OR i.location LIKE ?)"
        )
        like = f"%{keyword}%"
        params.extend([like, like, like])

    where_sql = " AND ".join(where)

    # 排序：hot 按浏览量，latest 按发布时间
    order_sql = (
        "i.view_count DESC, i.created_at DESC"
        if sort == "hot"
        else "i.created_at DESC"
    )

    with db.get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM items i WHERE {where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT i.* FROM items i
             WHERE {where_sql}
             ORDER BY {order_sql}
             LIMIT ? OFFSET ?
            """,
            params + [size, offset],
        ).fetchall()

        items = [serialize_item(conn, row) for row in rows]

    return make_page(total, page, size, items)


@router.route("POST", "/api/items", auth=True)
def create_item(request):
    """发布一条寻物启事或失物招领。"""
    data = request.json
    item_type = need_choice(data, "item_type", "信息类型", ItemType.ALL)
    title = need_str(data, "title", "标题", min_len=2, max_len=100)
    description = opt_str(data, "description", "详细描述", max_len=2000)
    category_id = opt_int(data, "category_id", "分类 ID")
    location = opt_str(data, "location", "地点", max_len=100)
    event_time = opt_str(data, "event_time", "发生时间", max_len=30)
    contact = opt_str(data, "contact", "联系方式", max_len=100)
    reward = opt_str(data, "reward", "酬谢说明", max_len=100)

    images = data.get("images") or []
    if not isinstance(images, list):
        raise ApiError(400, "images 必须是字符串数组")
    images = [str(item) for item in images]

    # 是否需要审核由配置决定；关闭审核时发布即可见，方便演示和本地调试
    status = ItemStatus.PENDING if ITEM_NEED_REVIEW else ItemStatus.APPROVED

    with db.get_conn() as conn:
        if category_id and not serialize_category(conn, category_id):
            raise ApiError(400, "指定的分类不存在")

        timestamp = db.now_str()
        cursor = conn.execute(
            """
            INSERT INTO items
                (item_type, title, description, category_id, location, event_time,
                 contact, reward, images, status, view_count, publisher_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                item_type,
                title,
                description,
                category_id,
                location,
                event_time,
                contact,
                reward,
                json.dumps(images, ensure_ascii=False) if images else None,
                status,
                request.user["id"],
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        result = serialize_item(conn, row)

    return Response(result, 201)


@router.route("GET", "/api/items/mine", auth=True)
def list_my_items(request):
    """
    我发布的信息（含待审核、已驳回的）。

    用户需要这个页面来查看自己每条信息的审核状态和驳回原因。
    """
    page, size, offset = read_pagination(request)
    status = request.q("status")

    where = ["publisher_id = ?"]
    params: list = [request.user["id"]]

    if status:
        if status not in ItemStatus.ALL:
            raise ApiError(400, f"status 只能是 {', '.join(ItemStatus.ALL)} 中的一个")
        where.append("status = ?")
        params.append(status)

    where_sql = " AND ".join(where)

    with db.get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM items WHERE {where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT * FROM items
             WHERE {where_sql}
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            params + [size, offset],
        ).fetchall()

        items = [serialize_item(conn, row) for row in rows]

    return make_page(total, page, size, items)


@router.route("GET", "/api/items/{item_id}")
def get_item(request, item_id):
    """
    查看详情，每次访问把浏览次数 +1。

    这个接口是公开的（游客能看已发布的信息），但待审核 / 已驳回的信息
    只有发布者本人和管理员能看，所以这里手动解析一次当前用户身份。
    """
    user = resolve_user(request)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_view(item, user)

        conn.execute(
            "UPDATE items SET view_count = view_count + 1 WHERE id = ?",
            (item["id"],),
        )
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item["id"],)
        ).fetchone()
        return serialize_item(conn, row)


@router.route("PUT", "/api/items/{item_id}", auth=True)
def update_item(request, item_id):
    """
    修改自己发布的信息。

    如果系统开启了审核，普通用户修改已发布的内容后会重新回到待审核：
    否则可以先提交合规内容过审，再偷偷改成违规内容，审核就形同虚设。
    """
    data = request.json
    title = opt_str(data, "title", "标题", max_len=100)
    description = opt_str(data, "description", "详细描述", max_len=2000)
    category_id = opt_int(data, "category_id", "分类 ID")
    location = opt_str(data, "location", "地点", max_len=100)
    event_time = opt_str(data, "event_time", "发生时间", max_len=30)
    contact = opt_str(data, "contact", "联系方式", max_len=100)
    reward = opt_str(data, "reward", "酬谢说明", max_len=100)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_manage(item, request.user)

        if category_id and not serialize_category(conn, category_id):
            raise ApiError(400, "指定的分类不存在")

        images_sql = None
        if "images" in data:
            images = data.get("images") or []
            if not isinstance(images, list):
                raise ApiError(400, "images 必须是字符串数组")
            images_sql = json.dumps([str(x) for x in images], ensure_ascii=False)

        # 普通用户改完要重新审核；管理员改动则直接保持原状态
        reset_review = ITEM_NEED_REVIEW and request.user["role"] != UserRole.ADMIN

        conn.execute(
            """
            UPDATE items
               SET title         = COALESCE(?, title),
                   description   = COALESCE(?, description),
                   category_id   = COALESCE(?, category_id),
                   location      = COALESCE(?, location),
                   event_time    = COALESCE(?, event_time),
                   contact       = COALESCE(?, contact),
                   reward        = COALESCE(?, reward),
                   images        = COALESCE(?, images),
                   status        = CASE WHEN ? = 1 THEN 'pending' ELSE status END,
                   reject_reason = CASE WHEN ? = 1 THEN NULL ELSE reject_reason END,
                   updated_at    = ?
             WHERE id = ?
            """,
            (
                title,
                description,
                category_id,
                location,
                event_time,
                contact,
                reward,
                images_sql,
                1 if reset_review else 0,
                1 if reset_review else 0,
                db.now_str(),
                item["id"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item["id"],)
        ).fetchone()
        return serialize_item(conn, row)


@router.route("DELETE", "/api/items/{item_id}", auth=True)
def delete_item(request, item_id):
    """
    删除信息。

    物理删除，所以只允许发布者本人和管理员操作。
    该信息下的认领申请和留言会通过外键的 ON DELETE CASCADE 一并删除。
    """
    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_manage(item, request.user)
        conn.execute("DELETE FROM items WHERE id = ?", (item["id"],))

    return {"message": "信息已删除"}


@router.route("POST", "/api/items/{item_id}/close", auth=True)
def close_item(request, item_id):
    """
    标记为"已完成"（东西找回了，或已被领走）。

    顺手把还在等待处理的认领申请也拒掉，
    免得申请人一直等一个已经结束的流程。
    """
    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_manage(item, request.user)

        conn.execute(
            "UPDATE items SET status = 'closed', updated_at = ? WHERE id = ?",
            (db.now_str(), item["id"]),
        )
        conn.execute(
            """
            UPDATE claims
               SET status = 'rejected',
                   review_remark = '信息已标记为完成，认领流程自动结束',
                   reviewer_id = ?,
                   reviewed_at = ?
             WHERE item_id = ? AND status = 'pending'
            """,
            (request.user["id"], db.now_str(), item["id"]),
        )
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item["id"],)
        ).fetchone()
        return serialize_item(conn, row)


@router.route("GET", "/api/items/{item_id}/comments")
def list_comments(request, item_id):
    """查看某条信息下面的留言（公开）。"""
    user = resolve_user(request)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_view(item, user)

        rows = conn.execute(
            "SELECT * FROM comments WHERE item_id = ? ORDER BY created_at ASC",
            (item["id"],),
        ).fetchall()
        return [serialize_comment(conn, row) for row in rows]


@router.route("POST", "/api/items/{item_id}/comments", auth=True)
def create_comment(request, item_id):
    """发表留言，例如补充线索。"""
    data = request.json
    content = need_str(data, "content", "留言内容", min_len=1, max_len=500)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)
        ensure_can_view(item, request.user)

        cursor = conn.execute(
            "INSERT INTO comments (item_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (item["id"], request.user["id"], content, db.now_str()),
        )
        row = conn.execute(
            "SELECT * FROM comments WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return Response(serialize_comment(conn, row), 201)


# ---------------------------------------------------------------------------
# 十、认领申请
# ---------------------------------------------------------------------------

@router.route("POST", "/api/items/{item_id}/claims", auth=True)
def create_claim(request, item_id):
    """
    提交认领申请。

    业务约束：
    1. 不能认领自己发布的信息；
    2. 只有"已发布"的信息才能申请认领；
    3. 同一个人对同一条信息只能有一条待处理的申请，避免重复刷屏。
    """
    data = request.json
    description = need_str(data, "description", "认领说明", min_len=5, max_len=1000)
    contact = opt_str(data, "contact", "联系方式", max_len=100)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)

        if item["publisher_id"] == request.user["id"]:
            raise ApiError(400, "不能认领自己发布的信息")

        if item["status"] != ItemStatus.APPROVED:
            raise ApiError(400, "该信息当前状态不支持申请认领")

        duplicated = conn.execute(
            """
            SELECT id FROM claims
             WHERE item_id = ? AND applicant_id = ? AND status = 'pending'
            """,
            (item["id"], request.user["id"]),
        ).fetchone()
        if duplicated:
            raise ApiError(400, "你已经提交过认领申请，请等待处理")

        cursor = conn.execute(
            """
            INSERT INTO claims
                (item_id, applicant_id, description, contact, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                item["id"],
                request.user["id"],
                description,
                contact or request.user.get("phone"),
                db.now_str(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM claims WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return Response(serialize_claim(conn, row), 201)


@router.route("GET", "/api/items/{item_id}/claims", auth=True)
def list_claims_of_item(request, item_id):
    """
    查看自己发布的信息收到了哪些认领申请。

    只有发布者本人和管理员能看：认领说明里通常包含能证明物品归属的
    敏感信息，不能让所有人随便看。
    """
    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)

        if item["publisher_id"] != request.user["id"] and request.user["role"] != UserRole.ADMIN:
            raise ApiError(403, "只能查看自己发布的信息收到的认领申请")

        rows = conn.execute(
            "SELECT * FROM claims WHERE item_id = ? ORDER BY created_at DESC",
            (item["id"],),
        ).fetchall()
        return [serialize_claim(conn, row) for row in rows]


@router.route("GET", "/api/claims/mine", auth=True)
def list_my_claims(request):
    """分页返回我提交过的认领申请，并附上对应信息的标题。"""
    page, size, offset = read_pagination(request)

    with db.get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM claims WHERE applicant_id = ?",
            (request.user["id"],),
        ).fetchone()["c"]

        rows = conn.execute(
            """
            SELECT * FROM claims
             WHERE applicant_id = ?
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            (request.user["id"], size, offset),
        ).fetchall()

        items = [serialize_claim(conn, row, with_item=True) for row in rows]

    return make_page(total, page, size, items)


@router.route("POST", "/api/claims/{claim_id}/review", auth=True)
def review_claim(request, claim_id):
    """
    审核认领申请（发布者或管理员）。

    同意之后会把信息置为"已完成"，同时自动拒绝该信息上其他还在等待的申请——
    东西只有一个，给了一个人，其他人的申请就该关掉。
    """
    data = request.json
    approve = opt_bool(data, "approve", True)
    remark = opt_str(data, "remark", "审核备注", max_len=200)

    try:
        claim_id = int(claim_id)
    except (TypeError, ValueError):
        raise ApiError(400, "申请 ID 必须是整数")

    with db.get_conn() as conn:
        claim = conn.execute(
            "SELECT * FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise ApiError(404, "认领申请不存在")

        item = get_item_or_404(conn, claim["item_id"])

        if item["publisher_id"] != request.user["id"] and request.user["role"] != UserRole.ADMIN:
            raise ApiError(403, "只有信息发布者或管理员可以审核认领申请")

        if claim["status"] != ClaimStatus.PENDING:
            raise ApiError(400, "该申请已经处理过了")

        conn.execute(
            """
            UPDATE claims
               SET status = ?, review_remark = ?, reviewer_id = ?, reviewed_at = ?
             WHERE id = ?
            """,
            (
                ClaimStatus.APPROVED if approve else ClaimStatus.REJECTED,
                remark,
                request.user["id"],
                db.now_str(),
                claim_id,
            ),
        )

        if approve:
            conn.execute(
                "UPDATE items SET status = 'closed', updated_at = ? WHERE id = ?",
                (db.now_str(), item["id"]),
            )
            conn.execute(
                """
                UPDATE claims
                   SET status = 'rejected',
                       review_remark = '该物品已被其他申请人认领',
                       reviewer_id = ?,
                       reviewed_at = ?
                 WHERE item_id = ? AND id != ? AND status = 'pending'
                """,
                (request.user["id"], db.now_str(), item["id"], claim_id),
            )

        row = conn.execute(
            "SELECT * FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        return serialize_claim(conn, row)


# ---------------------------------------------------------------------------
# 十一、管理员后台
# ---------------------------------------------------------------------------

@router.route("GET", "/api/admin/items", admin=True)
def admin_list_items(request):
    """
    管理员用的信息列表，能查到待审核、已驳回的信息。

    例：/api/admin/items?status=pending 就是审核页要的数据。
    """
    page, size, offset = read_pagination(request)
    status = request.q("status")
    keyword = request.q("keyword")

    where = ["1 = 1"]
    params: list = []

    if status:
        if status not in ItemStatus.ALL:
            raise ApiError(400, f"status 只能是 {', '.join(ItemStatus.ALL)} 中的一个")
        where.append("i.status = ?")
        params.append(status)

    if keyword:
        where.append("(i.title LIKE ? OR i.description LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like])

    where_sql = " AND ".join(where)

    with db.get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM items i WHERE {where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT i.* FROM items i
             WHERE {where_sql}
             ORDER BY i.created_at DESC
             LIMIT ? OFFSET ?
            """,
            params + [size, offset],
        ).fetchall()

        items = [serialize_item(conn, row) for row in rows]

    return make_page(total, page, size, items)


@router.route("POST", "/api/admin/items/{item_id}/review", admin=True)
def admin_review_item(request, item_id):
    """
    审核信息。

    通过 -> 状态变 approved，所有人可见；
    驳回 -> 状态变 rejected，把原因写进 reject_reason，
            发布者在"我的发布"里能看到原因并修改后重新提交。
    """
    data = request.json
    approve = opt_bool(data, "approve", True)
    reason = opt_str(data, "reason", "驳回原因", max_len=200)

    with db.get_conn() as conn:
        item = get_item_or_404(conn, item_id)

        if approve:
            conn.execute(
                "UPDATE items SET status = 'approved', reject_reason = NULL, updated_at = ? WHERE id = ?",
                (db.now_str(), item["id"]),
            )
        else:
            conn.execute(
                "UPDATE items SET status = 'rejected', reject_reason = ?, updated_at = ? WHERE id = ?",
                (reason or "内容不符合平台规范", db.now_str(), item["id"]),
            )

        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item["id"],)
        ).fetchone()
        return serialize_item(conn, row)


@router.route("GET", "/api/admin/users", admin=True)
def admin_list_users(request):
    """用户列表，支持按用户名 / 姓名 / 学号搜索。"""
    page, size, offset = read_pagination(request)
    keyword = request.q("keyword")
    role = request.q("role")

    where = ["1 = 1"]
    params: list = []

    if keyword:
        where.append("(username LIKE ? OR real_name LIKE ? OR student_no LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])

    if role:
        if role not in UserRole.ALL:
            raise ApiError(400, f"role 只能是 {', '.join(UserRole.ALL)} 中的一个")
        where.append("role = ?")
        params.append(role)

    where_sql = " AND ".join(where)

    with db.get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM users WHERE {where_sql}", params
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT * FROM users
             WHERE {where_sql}
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            params + [size, offset],
        ).fetchall()

        users = [serialize_user(row) for row in rows]

    return make_page(total, page, size, users)


@router.route("POST", "/api/admin/users/{user_id}/status", admin=True)
def admin_change_user_status(request, user_id):
    """
    封禁 / 解封账号。

    两条保护措施：不能改自己的状态（否则管理员会把自己锁在门外），
    也不能封禁其他管理员。
    """
    data = request.json
    target_status = need_choice(data, "status", "目标状态", UserStatus.ALL)

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise ApiError(400, "用户 ID 必须是整数")

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ApiError(404, "用户不存在")

        if row["id"] == request.user["id"]:
            raise ApiError(400, "不能修改自己的账号状态")

        if row["role"] == UserRole.ADMIN:
            raise ApiError(400, "不能封禁管理员账号")

        conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
            (target_status, db.now_str(), user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return serialize_user(row)


@router.route("GET", "/api/admin/stats", admin=True)
def admin_stats(request):
    """后台首页的统计数字。"""

    def count(conn, sql, params=()):
        return conn.execute(sql, params).fetchone()["c"]

    with db.get_conn() as conn:
        return {
            "user_count": count(conn, "SELECT COUNT(*) AS c FROM users"),
            "item_count": count(conn, "SELECT COUNT(*) AS c FROM items"),
            "pending_item_count": count(
                conn, "SELECT COUNT(*) AS c FROM items WHERE status = 'pending'"
            ),
            "approved_item_count": count(
                conn, "SELECT COUNT(*) AS c FROM items WHERE status = 'approved'"
            ),
            "closed_item_count": count(
                conn, "SELECT COUNT(*) AS c FROM items WHERE status = 'closed'"
            ),
            "claim_count": count(conn, "SELECT COUNT(*) AS c FROM claims"),
            "pending_claim_count": count(
                conn, "SELECT COUNT(*) AS c FROM claims WHERE status = 'pending'"
            ),
        }
