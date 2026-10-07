# -*- coding: utf-8 -*-
"""
控制台端到端演示
================

用法：

    python run_demo.py

它会做这些事：

1. 在本机随机端口上把后端服务跑起来（放在子线程里，不占用你的命令行）；
2. 用标准库 urllib 当客户端，把整条业务流程走一遍；
3. 把每一步的请求和结果直接打印在控制台里，跑完自动关掉服务。

所以你不需要打开浏览器，也不需要装 Postman，跑一遍就知道后端到底能不能用。
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from app import config, db
from app.server import create_server

# Windows 控制台默认可能是 GBK 编码。万一某个字符打不出来，
# 这里让它替换成 "?"，而不是让脚本直接崩掉。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------------------
# 打印小工具
# ---------------------------------------------------------------------------

def title(text: str) -> None:
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def step(text: str) -> None:
    print(f"\n[步骤] {text}")


def show(label: str, value) -> None:
    print(f"        {label}: {value}")


# ---------------------------------------------------------------------------
# 极简 HTTP 客户端
# ---------------------------------------------------------------------------

class Client:
    """
    用标准库 urllib 调用自己的接口。

    token 为 None 时就相当于游客身份，
    这样可以顺便演示"未登录访问受保护接口会怎样"。
    """

    def __init__(self, base_url: str, token: str = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, body: dict = None):
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

        # URL 里如果带中文（例如 ?keyword=耳机），必须先做 URL 编码，
        # 否则 HTTP 请求行要求纯 ASCII，会直接抛 UnicodeEncodeError。
        # safe 参数保留了 / ? & = 这些结构字符，只把中文等转成 %XX。
        url = self.base_url + quote(path, safe="/?&=:%#")

        request = urllib.request.Request(url, data=payload, method=method)
        request.add_header("Content-Type", "application/json; charset=utf-8")
        if self.token:
            request.add_header("Authorization", "Bearer " + self.token)

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx / 5xx 会走这里；接口返回的也是 JSON，照样解析出来看提示
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"message": raw, "data": None}


def login_or_register(client: Client, username: str, password: str,
                      real_name: str, student_no: str) -> None:
    """注册一个账号；如果已经注册过就直接登录。"""
    status, payload = client.request(
        "POST",
        "/api/auth/register",
        {
            "username": username,
            "password": password,
            "real_name": real_name,
            "student_no": student_no,
        },
    )
    if status == 201:
        show(f"注册 {username}", "成功（HTTP 201）")
    else:
        show(f"注册 {username}", f"跳过：{payload.get('message')}")

    status, payload = client.request(
        "POST", "/api/auth/login", {"username": username, "password": password}
    )
    if status != 200:
        raise SystemExit(f"登录失败：{payload.get('message')}")
    client.token = payload["data"]["access_token"]
    show(f"登录 {username}", f"成功，令牌前 30 位 {client.token[:30]}...")


def main() -> None:
    # ---- 0. 启动服务：子线程 + 随机端口，避免和别的程序抢 8000 ----
    db.init_db()
    server = create_server("127.0.0.1", 0)
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)

    title("校园失物招领系统 —— 端到端演示")
    show("服务地址", base_url)
    show("数据库文件", config.DB_PATH)
    show("发布是否需审核", "是" if config.ITEM_NEED_REVIEW else "否")

    try:
        # ===============================================================
        # 1. 健康检查
        # ===============================================================
        step("1. 健康检查（公开接口，不需要登录）")
        guest = Client(base_url)
        status, payload = guest.request("GET", "/api/health")
        show("HTTP 状态", status)
        show("返回内容", payload["data"])

        # ===============================================================
        # 2. 登录管理员
        # ===============================================================
        step("2. 管理员登录")
        admin = Client(base_url)
        status, payload = admin.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        if status != 200:
            show("登录失败", payload.get("message"))
            show("提示", "请确认数据库里的管理员密码没有被改过")
            return
        admin.token = payload["data"]["access_token"]
        show("当前用户", payload["data"]["user"]["username"])
        show("角色", payload["data"]["user"]["role_label"])

        # ===============================================================
        # 3. 两个学生账号
        # ===============================================================
        step("3. 准备两个学生账号（张三 / 李四）")
        zhangsan = Client(base_url)
        login_or_register(zhangsan, "zhangsan", "123456", "张三", "2023010101")
        lisi = Client(base_url)
        login_or_register(lisi, "lisi", "123456", "李四", "2023010102")

        # ===============================================================
        # 4. 分类列表
        # ===============================================================
        step("4. 查看系统分类")
        status, payload = guest.request("GET", "/api/categories")
        categories = payload["data"]
        for category in categories[:4]:
            show(f"分类 {category['id']}", category["name"])
        show("分类总数", len(categories))

        category_map = {item["name"]: item["id"] for item in categories}
        electronic_id = category_map.get("电子产品")
        card_id = category_map.get("证件卡类")

        # ===============================================================
        # 5. 张三发布寻物启事
        # ===============================================================
        step("5. 张三发布一条寻物启事")
        status, payload = zhangsan.request(
            "POST",
            "/api/items",
            {
                "item_type": "lost",
                "title": "丢失一副白色蓝牙耳机",
                "description": "昨晚在图书馆四楼自习区丢失，充电盒上有蓝色贴纸。",
                "category_id": electronic_id,
                "location": "图书馆四楼",
                "event_time": "2026-10-06 21:30:00",
                "contact": "手机 13800000001",
                "reward": "当面酬谢 100 元",
            },
        )
        lost_item = payload["data"]
        show("HTTP 状态", status)
        show("信息 ID", lost_item["id"])
        show("类型", lost_item["type_label"])
        show("状态", f"{lost_item['status_label']}（发布后需要管理员审核）")

        # ===============================================================
        # 6. 李四发布失物招领
        # ===============================================================
        step("6. 李四发布一条失物招领")
        status, payload = lisi.request(
            "POST",
            "/api/items",
            {
                "item_type": "found",
                "title": "在二食堂门口捡到一张校园卡",
                "description": "卡面姓名是张同学，学号尾号 01，请描述完整学号核对后归还。",
                "category_id": card_id,
                "location": "第二食堂门口",
                "contact": "微信 lisi_2023",
            },
        )
        found_item = payload["data"]
        show("信息 ID", found_item["id"])
        show("类型", found_item["type_label"])
        show("状态", found_item["status_label"])

        # ===============================================================
        # 7. 未审核的信息游客看不到
        # ===============================================================
        step("7. 游客查询列表（两条信息都还没审核，应该查不到）")
        status, payload = guest.request("GET", "/api/items?keyword=耳机")
        show("查询关键字", "耳机")
        show("命中条数", payload["data"]["total"])
        show("说明", "待审核的信息不会出现在公开列表里")

        # ===============================================================
        # 8. 管理员审核
        # ===============================================================
        step("8. 管理员查看待审核列表并全部通过")
        status, payload = admin.request("GET", "/api/admin/items?status=pending")
        pending = payload["data"]["items"]
        show("待审核条数", payload["data"]["total"])

        for item in pending:
            status, reviewed = admin.request(
                "POST", f"/api/admin/items/{item['id']}/review", {"approve": True}
            )
            show(
                f"审核信息 {item['id']}",
                f"{item['title']} -> {reviewed['data']['status_label']}",
            )

        # ===============================================================
        # 9. 审核后游客就能搜到了
        # ===============================================================
        step("9. 游客再次搜索（这次能查到了）")
        status, payload = guest.request("GET", "/api/items?keyword=耳机&sort=latest")
        result = payload["data"]
        show("命中条数", result["total"])
        for item in result["items"]:
            show(
                f"信息 {item['id']}",
                f"{item['type_label']} | {item['title']} | 发布者 {item['publisher']['username']}",
            )

        # ===============================================================
        # 10. 查看详情
        # ===============================================================
        step("10. 查看详情，浏览次数会 +1")
        status, payload = guest.request("GET", f"/api/items/{found_item['id']}")
        detail = payload["data"]
        show("标题", detail["title"])
        show("分类", detail["category"]["name"] if detail["category"] else "未分类")
        show("浏览次数", detail["view_count"])

        # ===============================================================
        # 11. 张三认领李四捡到的校园卡
        # ===============================================================
        step("11. 张三提交认领申请")
        status, payload = zhangsan.request(
            "POST",
            f"/api/items/{found_item['id']}/claims",
            {
                "description": "卡是我的：姓名张三，完整学号 2023010101，卡背面签了名字。",
                "contact": "13800000001",
            },
        )
        claim = payload["data"]
        show("HTTP 状态", status)
        show("申请 ID", claim["id"])
        show("当前状态", claim["status_label"])

        # ===============================================================
        # 12. 李四审核认领申请
        # ===============================================================
        step("12. 李四查看收到的申请并同意")
        status, payload = lisi.request("GET", f"/api/items/{found_item['id']}/claims")
        show("收到的申请数", len(payload["data"]))
        for item in payload["data"]:
            show("申请人", item["applicant"]["username"])
            show("说明", item["description"][:30] + "...")

        status, payload = lisi.request(
            "POST",
            f"/api/claims/{claim['id']}/review",
            {"approve": True, "remark": "信息核对无误，已当面归还"},
        )
        show("审核结果", payload["data"]["status_label"])
        show("备注", payload["data"]["review_remark"])

        # ===============================================================
        # 13. 信息自动变成"已完成"
        # ===============================================================
        step("13. 认领成功后，那条信息会自动变成已完成")
        status, payload = guest.request("GET", f"/api/items/{found_item['id']}")
        show("信息状态", payload["data"]["status_label"])

        # ===============================================================
        # 14. 留言
        # ===============================================================
        step("14. 在信息下面留言")
        status, payload = lisi.request(
            "POST",
            f"/api/items/{lost_item['id']}/comments",
            {"content": "我昨晚路过四楼，好像看到有人把耳机交到借阅台了，你去问问。"},
        )
        show("留言人", payload["data"]["user"]["username"])
        show("留言内容", payload["data"]["content"][:30] + "...")

        status, payload = guest.request("GET", f"/api/items/{lost_item['id']}/comments")
        show("该信息留言总数", len(payload["data"]))

        # ===============================================================
        # 15. 个人中心
        # ===============================================================
        step("15. 张三查看自己发布的信息和提交的认领")
        status, payload = zhangsan.request("GET", "/api/items/mine")
        show("我发布的信息数", payload["data"]["total"])
        for item in payload["data"]["items"]:
            show(f"信息 {item['id']}", f"{item['title']} | {item['status_label']}")

        status, payload = zhangsan.request("GET", "/api/claims/mine")
        show("我提交的认领数", payload["data"]["total"])
        for item in payload["data"]["items"]:
            title_text = item["item"]["title"] if item["item"] else "(信息已删除)"
            show(f"申请 {item['id']}", f"{title_text} | {item['status_label']}")

        # ===============================================================
        # 16. 权限与参数校验
        # ===============================================================
        step("16. 演示权限控制和参数校验")
        status, payload = guest.request(
            "POST", "/api/items", {"item_type": "lost", "title": "游客想发布"}
        )
        show("游客发布信息", f"HTTP {status} -> {payload.get('message')}")

        status, payload = zhangsan.request(
            "POST", "/api/items", {"item_type": "wrong", "title": "类型写错了"}
        )
        show("类型写错", f"HTTP {status} -> {payload.get('message')}")

        status, payload = zhangsan.request("GET", "/api/admin/stats")
        show("学生访问后台", f"HTTP {status} -> {payload.get('message')}")

        # ===============================================================
        # 17. 管理员统计
        # ===============================================================
        step("17. 管理员查看统计数据")
        status, payload = admin.request("GET", "/api/admin/stats")
        stats = payload["data"]
        show("用户总数", stats["user_count"])
        show("信息总数", stats["item_count"])
        show("待审核", stats["pending_item_count"])
        show("已发布", stats["approved_item_count"])
        show("已完成", stats["closed_item_count"])
        show("认领申请总数", stats["claim_count"])
        show("待处理认领", stats["pending_claim_count"])

        title("演示结束：所有接口调用成功")
        print("说明：本次演示把数据写进了上面那个数据库文件，下次启动还在。")
        print("      想从零开始，删掉 data/campus_lost_found.db 即可。")

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
