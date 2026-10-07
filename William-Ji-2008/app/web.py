# -*- coding: utf-8 -*-
"""
迷你 Web 框架
=============

只用标准库 http.server 实现，提供 4 样东西：

1. Router  —— 路由表，支持 /api/items/{item_id} 这种路径参数
2. Request —— 请求对象：方法、路径、查询参数、请求头、JSON 请求体、当前用户
3. Response—— 响应对象，用来指定非 200 的状态码
4. ApiError—— 业务异常，业务代码里 raise 一下就能返回对应的 HTTP 错误

响应统一是这个形状，前端处理起来最省事：

    { "code": 0, "message": "ok", "data": ... }

出错时 code 是 HTTP 状态码，message 是中文提示，data 为 null。
"""

import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from app import config


class ApiError(Exception):
    """业务异常。抛出后由框架统一转成 JSON 错误响应。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Response:
    """需要自定义状态码时返回它，例如创建成功返回 201。"""

    def __init__(self, data, status: int = 200):
        self.data = data
        self.status = status


class Request:
    """一次 HTTP 请求的封装。"""

    def __init__(self, method, path, query, headers, body: bytes):
        self.method = method
        self.path = path
        self.query = query          # dict[str, list[str]]
        self.headers = headers
        self.body = body
        self.user: Optional[dict] = None   # 由框架在鉴权后填入
        self._json = None

    @property
    def json(self) -> dict:
        """
        把请求体解析成字典。

        之所以做成属性而不是方法，是为了让业务代码写起来更顺：
            data = req.json
        解析失败会直接抛 400，而不是让后面报出一堆 KeyError。
        """
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    parsed = json.loads(self.body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise ApiError(400, "请求体不是合法的 JSON")
                if not isinstance(parsed, dict):
                    raise ApiError(400, "请求体必须是一个 JSON 对象")
                self._json = parsed
        return self._json

    def q(self, name: str, default=None):
        """取查询参数（?a=1&b=2 里的 b 这种）。"""
        values = self.query.get(name)
        if not values:
            return default
        value = values[0]
        return value if value != "" else default

    def q_int(self, name: str, default=None):
        """取整数查询参数，格式不对就抛 400。"""
        raw = self.q(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, f"查询参数 {name} 必须是整数")

    def bearer_token(self) -> Optional[str]:
        """从请求头里取出 Bearer 令牌。"""
        raw = self.headers.get("Authorization") or ""
        if raw.lower().startswith("bearer "):
            return raw[7:].strip()
        return None


class Router:
    """
    极简路由表。

    注册时会先把 /api/items/{item_id} 这种写法编译成正则，
    匹配成功后把 {item_id} 捕获到的字符串作为关键字参数传给处理函数。
    """

    def __init__(self):
        self._routes = []

    def route(self, method: str, pattern: str, auth: bool = False, admin: bool = False):
        """
        装饰器用法：

            @router.route("POST", "/api/items", auth=True)
            def create_item(req):
                ...

        参数：
            auth  : 需要登录
            admin : 需要管理员（自动包含登录）
        """
        def decorator(func: Callable):
            regex = re.compile(
                "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
            )
            self._routes.append((method.upper(), regex, func, auth or admin, admin))
            return func

        return decorator

    def match(self, method: str, path: str):
        """
        找出匹配的路由。

        注意：是按注册顺序返回第一个匹配项，所以像 /api/items/mine
        这种"固定路径"必须注册在 /api/items/{item_id} 之前，
        否则 mine 会被当成 item_id。
        """
        allowed_methods = set()
        for route_method, regex, func, need_auth, need_admin in self._routes:
            matched = regex.match(path)
            if not matched:
                continue
            if route_method == method:
                return func, matched.groupdict(), need_auth, need_admin
            allowed_methods.add(route_method)

        if allowed_methods:
            raise ApiError(
                405,
                f"该地址不支持 {method}，可用方法：{', '.join(sorted(allowed_methods))}",
            )
        return None, None, False, False


def read_pagination(req: Request):
    """
    解析分页参数，返回 (page, size, offset)。

    不传就用配置里的默认每页条数；超过上限会被压到上限，
    避免有人传 size=999999 把整张表读进内存。
    """
    page = req.q_int("page", 1) or 1
    size = req.q_int("size", config.DEFAULT_PAGE_SIZE) or config.DEFAULT_PAGE_SIZE

    if page < 1:
        page = 1
    if size < 1:
        size = config.DEFAULT_PAGE_SIZE
    if size > config.MAX_PAGE_SIZE:
        size = config.MAX_PAGE_SIZE

    return page, size, (page - 1) * size


def make_page(total: int, page: int, size: int, items: list) -> dict:
    """组装统一的分页响应结构。"""
    pages = (total + size - 1) // size if size else 0
    return {"total": total, "page": page, "size": size, "pages": pages, "items": items}


def make_handler(router: Router, resolve_user: Callable[[Request], Optional[dict]]):
    """
    根据路由表生成一个 http.server 能用的请求处理类。

    resolve_user 由调用方提供：给它一个 Request，它要么返回用户字典，
    要么返回 None（表示未登录或令牌无效）。
    """

    class Handler(BaseHTTPRequestHandler):
        # 用 HTTP/1.1 才能复用连接；前提是每次响应都要带 Content-Length
        protocol_version = "HTTP/1.1"
        server_version = "CampusLostFound/1.0"

        def log_message(self, fmt, *args):
            """默认日志太啰嗦，这里每行只打印关键信息。"""
            print(f"    {self.command} {self.path}")

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # 允许跨域，方便以后接一个前端页面来调试
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, data, status: int = 200) -> None:
            self._send(status, {"code": 0, "message": "ok", "data": data})

        def _fail(self, status: int, message: str) -> None:
            self._send(status, {"code": status, "message": message, "data": None})

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            # 去掉末尾多余的斜杠，这样 /api/items 和 /api/items/ 都能匹配
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""

            request = Request(method, path, query, self.headers, body)

            try:
                func, params, need_auth, need_admin = router.match(method, path)

                if func is None:
                    raise ApiError(404, f"接口不存在：{method} {path}")

                if need_auth or need_admin:
                    user = resolve_user(request)
                    if user is None:
                        raise ApiError(401, "请先登录（请求头里的令牌缺失或已失效）")
                    request.user = user

                if need_admin and request.user["role"] != "admin":
                    raise ApiError(403, "该操作需要管理员权限")

                result = func(request, **params)

                if isinstance(result, Response):
                    self._ok(result.data, result.status)
                else:
                    self._ok(result)

            except ApiError as exc:
                self._fail(exc.status, exc.message)
            except Exception as exc:  # noqa: BLE001  兜底，避免服务因为一个请求崩掉
                print("[错误] 未预期的异常：")
                traceback.print_exc()
                self._fail(500, f"服务器内部错误：{exc}")

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        def do_OPTIONS(self):
            """浏览器发跨域预检请求时直接放行。"""
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def serve(host: str, port: int, handler_class):
    """启动 HTTP 服务，返回服务器对象（方便调用方在子线程里启动）。"""
    return ThreadingHTTPServer((host, port), handler_class)
