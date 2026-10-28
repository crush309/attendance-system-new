# 免费部署候选（核查日期：2026-10-28）

建议：Render Free Web Service + Neon Free PostgreSQL。当前项目保留 PostgreSQL 支持，因此迁移时不必把本地 SQLite 数据库放到云端临时磁盘上。

## Render + Neon

- Render 官方免费说明：https://render.com/docs/free
- Neon 官方定价：https://neon.com/pricing
- Render 免费 Python Web 服务空闲 15 分钟后休眠，下次访问约需一分钟启动；每个工作区每月共享 750 个免费实例小时。免费本地文件系统不持久化，不能用于保存 SQLite 数据库。
- Render 自带免费 PostgreSQL 30 天后到期，故推荐另接 Neon。
- Neon Free 当前为每项目 1 GB 数据库存储、每月 100 CU-hours 计算额度，无时间期限且不要求信用卡；仍需遵守额度限制。

部署配置（尚未实际部署）：

1. 创建 Neon Free 项目，在私密环境变量中配置其连接串 `DATABASE_URL`。不要把连接串放入 Git。
2. Render 创建 Free Web Service，连接项目 GitHub 仓库。
3. Build Command：`python -m pip install -r requirements.txt`。
4. Start Command：`python -m uvicorn backend.app:app --host 0.0.0.0 --port $PORT`。
5. 使用与项目依赖兼容的 Python 3.12；在 Render 环境变量中明确设置 `PYTHON_VERSION`。配置随机 `JWT_SECRET_KEY`、自己的强密码 `ADMIN_INITIAL_PASSWORD` 以及 Neon 的 `DATABASE_URL`。初始密码只在创建管理员时生效，不覆盖已存在管理员。
6. 首次上线后更改管理员默认密码，核查登录、上传与导出。

本地 SQLite 的账号、规则和分组不会自动迁移到 Neon；原 Railway 数据需要另外导出和导入。免费计划适合低流量试用，Render 官方不建议将免费实例用于正式生产。

## Vercel Hobby + Neon（个人非商业演示备选）

- FastAPI 支持：https://vercel.com/docs/frameworks/backend/fastapi
- Hobby 限制：https://vercel.com/docs/plans/hobby
- 请求/响应限制：https://vercel.com/docs/functions/limitations
- Hobby 免费但限个人非商业用途；函数请求及响应最大 4.5 MB，可能限制考勤 Excel 上传和导出。
- 项目需要适配临时上传目录及 FastAPI 入口配置，数据库继续使用 Neon。当前未做这些适配。

## 未列入推荐

- Koyeb 虽有免费实例，但当前官方 FAQ 说明注册流程默认选择 Pro 并收取按比例套餐费，不符合优先避免初始付费的目标：https://www.koyeb.com/docs/faqs/pricing
- Hugging Face 当前说明运行 Docker/Gradio Space 需要付费方案，即使 CPU Basic 无小时费，也不适合作为新用户零成本方案：https://huggingface.co/docs/hub/spaces-overview
