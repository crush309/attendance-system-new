# 📊 考勤数据分析系统

在线考勤数据导入、解析、统计与导出系统。

## 功能特性

- **用户系统**：管理员 / 普通用户双角色，JWT 认证
- **考勤规则**：管理员可配置有效打卡时间段和每日最低打卡次数
- **数据导入**：支持 .xls / .xlsx 考勤表格上传与自动解析
- **统计分析**：出勤天数、缺勤天数、异常天数、总打卡次数
- **数据导出**：统计结果可导出为 .xlsx 文件
- **深色主题**：现代化单页应用，左侧导航栏布局

## 快速启动

```bash
cd attendance-system
bash start.sh
```

启动后访问 http://localhost:8080

## 默认账号

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | admin | admin123 |

## 技术栈

- **后端**：Python FastAPI + SQLite
- **前端**：Vue.js 3 (CDN) + 原生 CSS
- **文件解析**：xlrd (.xls) + openpyxl (.xlsx)
- **认证**：JWT (python-jose)

## 项目结构

```
attendance-system/
├── backend/
│   ├── app.py          # FastAPI 主应用
│   ├── database.py     # 数据库模型与操作
│   └── parser.py       # Excel 文件解析器
├── frontend/
│   └── index.html      # 单页应用 (Vue.js 3)
├── uploads/            # 临时上传目录
├── requirements.txt    # Python 依赖
├── start.sh            # 启动脚本
└── README.md
```

## API 接口

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | /api/login | 用户登录 | 公开 |
| POST | /api/register | 用户注册 | 公开 |
| GET | /api/me | 获取当前用户信息 | 登录 |
| GET | /api/rules | 获取考勤规则 | 公开 |
| PUT | /api/rules | 更新考勤规则 | 管理员 |
| GET | /api/users | 获取用户列表 | 管理员 |
| POST | /api/upload | 上传考勤文件 | 登录 |
| POST | /api/export | 导出统计 Excel | 登录 |
