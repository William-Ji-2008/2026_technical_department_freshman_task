# 校园失物招领系统 · 本地零依赖版

这是一个**只用 Python 标准库**写的后端服务：不需要 `pip install` 任何东西，
不需要联网，不需要打开浏览器，装好 Python 就能直接跑。

功能和一个正常的后端服务一样：提供 HTTP 接口、用 SQLite 存数据、JWT 登录认证。
另外附带一个**控制台演示脚本**，它会自动把服务跑起来，然后把
「注册 → 登录 → 发布 → 审核 → 搜索 → 认领 → 留言 → 统计」整条流程走一遍，
结果直接打印在命令行里，你不需要访问任何网站。

---

## 一、为什么是"零依赖"

常见做法是用 FastAPI + SQLAlchemy，但那样必须先 `pip install`，
在没网的环境里就卡住了。本项目改成用标准库实现：

| 常规方案 | 本项目替代方案 | 用到的标准库 |
| --- | --- | --- |
| FastAPI / Flask | 自己写一个迷你 Web 框架 | `http.server`、`json`、`re` |
| SQLAlchemy | 直接用 SQL 操作 SQLite | `sqlite3` |
| PyJWT | 自己实现 HS256 签名令牌 | `hmac`、`hashlib`、`base64` |
| passlib / bcrypt | PBKDF2-HMAC-SHA256 | `hashlib`、`secrets` |
| requests（演示脚本用） | urllib 调用自己的接口 | `urllib.request` |

自己实现这些不是为了"炫技"，而是让代码完全自包含：
拷到任何一台装了 Python 3.9+ 的电脑上都能跑。
每个模块的注释里都写了"为什么这样做"。

---

## 二、目录结构

```
campus_lost_found_local/
├── app/
│   ├── __init__.py
│   ├── config.py        全局配置（端口、数据库位置、密钥、分页、是否审核）
│   ├── db.py            SQLite 连接管理 + 建表 + 初始数据
│   ├── security.py      密码哈希 + JWT 令牌
│   ├── web.py           迷你 Web 框架（路由、请求、响应、异常）
│   ├── api.py           所有业务接口
│   └── server.py        组装路由并启动 HTTP 服务
├── tools/
│   └── find_python.bat  自动寻找可用的 Python 解释器
├── run_server.py        启动后端服务
├── run_demo.py          控制台端到端演示（不开浏览器）
├── 启动服务.bat         双击即可启动
├── 控制台演示.bat       双击即可跑完整演示
└── 部署到D盘.bat        把整个项目复制到 D:\campus_lost_found_local
```

运行后会自动生成 `data/campus_lost_found.db`，这就是你的数据库，删掉它就能重置全部数据。

---

## 三、怎么跑

### 方式一：双击（最省事）

- 双击 **控制台演示.bat**：自动跑完整流程，结果打印在窗口里，看完按任意键退出。
- 双击 **启动服务.bat**：启动后端服务，窗口保持打开就是服务运行中，按 `Ctrl + C` 停止。

### 方式二：命令行

```powershell
cd <本项目所在目录>

# 跑演示（推荐先看这个）
"C:\Users\William\AppData\Local\Programs\Python\Python314\python.exe" run_demo.py

# 启动服务
"C:\Users\William\AppData\Local\Programs\Python\Python314\python.exe" run_server.py
```

> 上例写的是完整路径，因为当前机器 PATH 里那个 `python` 指向一个残缺的虚拟环境
> （`D:\Scripts\python.exe`，会报 `failed to locate pyvenv.cfg`）。
> 如果你已经修好了 PATH，直接 `python run_demo.py` 即可。

### 方式三：把项目放到 D 盘

双击 **部署到D盘.bat**，脚本会把整个项目复制到 `D:\campus_lost_found_local`
（我自己没有写 D 盘根目录的权限，所以这一步交给你在本机双击执行）。

---

## 四、默认账号

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 演示学生 | `zhangsan` | `123456` |
| 演示学生 | `lisi` | `123456` |

管理员账号在首次启动时自动创建；两个学生账号由演示脚本自动创建。

---

## 五、接口一览

服务默认监听 `http://127.0.0.1:8000`。接口风格是 REST，
请求和响应都是 JSON；需要登录的接口要在请求头里带上
`Authorization: Bearer <token>`。

### 基础

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 公开 | 服务信息 |
| GET | `/api/health` | 公开 | 健康检查 |

### 认证

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/register` | 公开 | 注册 |
| POST | `/api/auth/login` | 公开 | 登录，返回令牌 |
| GET | `/api/auth/me` | 登录 | 当前用户信息 |
| PUT | `/api/auth/me` | 登录 | 修改资料 |
| PUT | `/api/auth/me/password` | 登录 | 修改密码 |

### 失物 / 招领信息

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/items` | 公开 | 分页 + 关键字 / 类型 / 分类 / 地点筛选 |
| POST | `/api/items` | 登录 | 发布信息 |
| GET | `/api/items/mine` | 登录 | 我发布的信息 |
| GET | `/api/items/{id}` | 公开 | 详情（浏览次数 +1） |
| PUT | `/api/items/{id}` | 本人 / 管理员 | 修改 |
| DELETE | `/api/items/{id}` | 本人 / 管理员 | 删除 |
| POST | `/api/items/{id}/close` | 本人 / 管理员 | 标记已完成 |
| GET | `/api/items/{id}/comments` | 公开 | 留言列表 |
| POST | `/api/items/{id}/comments` | 登录 | 发表留言 |

### 认领 / 分类 / 后台

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/items/{id}/claims` | 登录 | 提交认领申请 |
| GET | `/api/items/{id}/claims` | 发布者 / 管理员 | 查看收到的申请 |
| GET | `/api/claims/mine` | 登录 | 我提交的申请 |
| POST | `/api/claims/{id}/review` | 发布者 / 管理员 | 同意 / 拒绝 |
| GET | `/api/categories` | 公开 | 分类列表 |
| POST / PUT / DELETE | `/api/categories[/{id}]` | 管理员 | 分类维护 |
| GET | `/api/admin/items` | 管理员 | 全部信息（可筛状态） |
| POST | `/api/admin/items/{id}/review` | 管理员 | 审核通过 / 驳回 |
| GET | `/api/admin/users` | 管理员 | 用户列表 |
| POST | `/api/admin/users/{id}/status` | 管理员 | 封禁 / 解封 |
| GET | `/api/admin/stats` | 管理员 | 统计数据 |

---

## 六、想用浏览器 / Postman 测试

服务本身就是 HTTP 接口，所以你想用别的工具测也完全可以，例如：

```powershell
# 登录拿令牌
$body = '{"username":"admin","password":"admin123"}'
$token = (Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/auth/login" `
    -Method Post -ContentType "application/json" -Body $body).data.access_token

# 查待审核信息
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/admin/items?status=pending" `
    -Headers @{ Authorization = "Bearer $token" }
```

本项目没有内置网页界面（因为要求是"不用登网站"），
如果后面想配一个前端页面，直接调这些接口就行。

---

## 七、常见问题

**Q：双击 bat 提示找不到 Python？**
先确认 Python 装好了，然后双击 `控制台演示.bat` 看提示。
脚本会自动去这几个位置找：PATH 里的 `python`、`py` 启动器、
`%LOCALAPPDATA%\Programs\Python\Python3xx\python.exe`、`D:\hclaw\python\python.exe`。

**Q：端口 8000 被占用？**
改 `app/config.py` 里的 `PORT`，或者设置环境变量后再启动：
`$env:CLF_PORT = "8080"`。

**Q：数据库想清空重来？**
删掉 `data/campus_lost_found.db`，下次启动会自动重建。

**Q：这个能直接当毕业设计用吗？**
可以作为一个"后端服务"的部分。真实项目里建议补上：
图片上传、操作日志、Redis 缓存、单元测试，以及一个前端页面。
