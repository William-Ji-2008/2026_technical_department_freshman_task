# -*- coding: utf-8 -*-
"""
启动后端服务
============

用法（在本文件所在目录执行）：

    python run_server.py

启动后服务会一直运行，按 Ctrl + C 停止。
端口可以在 app/config.py 里改，也可以用环境变量覆盖：

    $env:CLF_PORT = "8080"
    python run_server.py
"""

from app.server import main

if __name__ == "__main__":
    main()
