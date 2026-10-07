# -*- coding: utf-8 -*-
"""
服务组装与启动
==============

把「路由表」「数据库初始化」「HTTP 服务器」三件事拼起来。
本质上只做两件事：

1. 用 web.make_handler 把路由表包装成一个 http.server 能用的处理类；
2. 用 ThreadingHTTPServer 把服务跑起来（每个请求分配一个线程）。
"""

import sys

from app import api, config, db, web


def build_handler():
    """生成请求处理类：路由表 + 当前用户解析函数。"""
    return web.make_handler(api.router, api.resolve_user)


def create_server(host: str = None, port: int = None):
    """
    创建（但不启动）HTTP 服务对象。

    单独抽出来是为了让 run_demo.py 能在子线程里启动它，
    这样跑演示时不用再额外开一个命令行窗口。
    """
    host = host or config.HOST
    port = config.PORT if port is None else port
    return web.serve(host, port, build_handler())


def main() -> None:
    """命令行入口：初始化数据库 -> 启动服务 -> 阻塞等待请求。"""
    print("=" * 68)
    print("校园失物招领系统 —— 本地零依赖后端")
    print("=" * 68)

    # 建表 + 写入默认管理员和默认分类（已存在则跳过）
    db.init_db()

    server = create_server()
    host, port = server.server_address[0], server.server_address[1]

    print(f"\n数据库文件： {config.DB_PATH}")
    print(f"服务地址：   http://{host}:{port}")
    print(f"健康检查：   http://{host}:{port}/api/health")
    print("\n提示：本服务没有网页界面，请用 run_demo.py 或自己的客户端调用接口。")
    print("按 Ctrl + C 停止服务。\n")

    # 主动刷新一次输出缓冲：这样即使用户把输出重定向到文件或管道，
    # 也能立刻看到上面这段启动信息，而不用等程序退出。
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收到停止信号，服务已关闭。")
    finally:
        server.server_close()
