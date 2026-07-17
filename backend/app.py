import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import jwt, JWTError
from io import BytesIO
from openpyxl.utils import get_column_letter
import re

from database import (
    init_db, create_user, authenticate_user, get_user_by_id,
    update_user_profile, change_password, get_all_users, update_user_permissions,
    get_rules, update_rules,
    create_group, get_all_groups, delete_group,
    bulk_assign_employees, remove_employee_from_group,
    get_employee_group_map, get_employees_in_group,
    delete_user,
)
from attendance_parser import parse_attendance_file

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "attendance-system-default-secret-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="考勤数据分析系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ── 辅助函数：格式化日期 ──
def format_date(date_str: str) -> str:
    """将 '2026-07-01' 转换为 '7月1日'，若格式不符则返回原字符串"""
    if not date_str:
        return ""
    match = re.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', date_str)
    if match:
        month = str(int(match.group(2)))
        day = str(int(match.group(3)))
        return f"{month}月{day}日"
    return date_str

def sort_details_by_date(details: List[dict]) -> List[dict]:
    return sorted(details, key=lambda x: x.get('day', 0))


# ── Models ──
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class TimePeriod(BaseModel):
    start: str
    end: str

class RulesUpdate(BaseModel):
    time_periods: List[TimePeriod]
    min_punch_per_day: int

class GroupCreate(BaseModel):
    name: str

class EmployeeAssign(BaseModel):
    emp_ids: List[str]
    group_id: int

class EmployeeRemove(BaseModel):
    emp_id: str
    group_id: int

class ProfileUpdate(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None

class PasswordChange(BaseModel):
    old_password: str
    new_password: str

class PermissionsUpdate(BaseModel):
    permissions: dict


# ── Auth ──
def create_token(data: dict) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    data.update({"exp": expire})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="认证失败")

def extract_token(authorization: str) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    return authorization[7:]

def get_payload(authorization: str) -> dict:
    return verify_token(extract_token(authorization))

def is_admin_or_perm(authorization: str, perm: str) -> dict:
    payload = get_payload(authorization)
    if payload.get("role") == "admin":
        return payload
    user = get_user_by_id(payload["uid"])
    perms = user.get("permissions", {}) if user else {}
    if not perms.get(perm):
        raise HTTPException(status_code=403, detail="权限不足，需要管理员授权")
    return payload


# ── Auth API ──
@app.post("/api/login")
def login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token({"sub": user["username"], "role": user["role"], "uid": user["id"]})
    return {"token": token, "user": user}

@app.post("/api/register")
def register(req: RegisterRequest):
    if len(req.username) < 2 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="用户名至少2字符，密码至少4字符")
    user = create_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    token = create_token({"sub": user["username"], "role": user["role"], "uid": user["id"]})
    return {"token": token, "user": user}

@app.get("/api/me")
def get_me(authorization: str = Header(None, alias="Authorization")):
    payload = get_payload(authorization)
    user = get_user_by_id(payload["uid"])
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.put("/api/profile")
def api_update_profile(req: ProfileUpdate, authorization: str = Header(None, alias="Authorization")):
    uid = get_payload(authorization)["uid"]
    update_user_profile(uid, nickname=req.nickname, avatar=req.avatar)
    return get_user_by_id(uid)

@app.put("/api/password")
def api_change_password(req: PasswordChange, authorization: str = Header(None, alias="Authorization")):
    uid = get_payload(authorization)["uid"]
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="新密码至少4字符")
    if not change_password(uid, req.old_password, req.new_password):
        raise HTTPException(status_code=400, detail="旧密码错误")
    return {"message": "密码修改成功"}


@app.get("/api/rules")
def api_get_rules():
    return get_rules()

@app.put("/api/rules")
def api_update_rules(req: RulesUpdate, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_rules")
    update_rules({"time_periods": [p.model_dump() for p in req.time_periods], "min_punch_per_day": req.min_punch_per_day})
    return {"message": "规则已更新"}


@app.get("/api/users")
def api_get_users(authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)
    return get_all_users()

@app.put("/api/users/{uid}/permissions")
def api_update_permissions(uid: int, req: PermissionsUpdate, authorization: str = Header(None, alias="Authorization")):
    payload = get_payload(authorization)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改权限")
    valid_keys = {"edit_rules", "edit_groups", "manage_users"}
    perms = {k: bool(v) for k, v in req.permissions.items() if k in valid_keys}
    if not update_user_permissions(uid, perms):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"message": "权限已更新", "permissions": perms}

@app.delete("/api/users/{uid}")
def delete_user_api(uid: int, authorization: str = Header(None, alias="Authorization")):
    payload = get_payload(authorization)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可删除用户")
    if uid == payload["uid"]:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    if not delete_user(uid):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"message": "用户已删除"}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xls", "xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xls 或 .xlsx 文件")
    filepath = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}.{ext}")
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    rules = get_rules()
    try:
        result = parse_attendance_file(filepath, rules)
    except Exception as e:
        os.remove(filepath)
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")
    os.remove(filepath)
    group_map = get_employee_group_map()
    for rec in result["records"]:
        rec["groups"] = group_map.get(rec["emp_id"], [])
    return result


@app.get("/api/groups")
def api_get_groups(authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)
    return get_all_groups()

@app.post("/api/groups")
def api_create_group(req: GroupCreate, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_groups")
    g = create_group(req.name)
    if not g:
        raise HTTPException(status_code=400, detail="分组名已存在")
    return g

@app.delete("/api/groups/{group_id}")
def api_delete_group(group_id: int, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_groups")
    if not delete_group(group_id):
        raise HTTPException(status_code=404, detail="分组不存在")
    return {"message": "已删除"}

@app.post("/api/groups/assign")
def api_assign(req: EmployeeAssign, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_groups")
    count = bulk_assign_employees(req.emp_ids, req.group_id)
    return {"message": f"已分配 {count} 人", "count": count}

@app.post("/api/groups/remove")
def api_remove(req: EmployeeRemove, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_groups")
    if not remove_employee_from_group(req.emp_id, req.group_id):
        raise HTTPException(status_code=404, detail="未找到该分配关系")
    return {"message": "已移除"}

@app.get("/api/groups/{group_id}/members")
def api_get_members(group_id: int, authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)
    return get_employees_in_group(group_id)


# ── Export ──
@app.post("/api/export")
async def export_excel(data: dict, authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)
    records = data.get("records", [])
    group_filter = data.get("group_id")

    include_detail = data.get("include_detail", True)

    if group_filter is not None:
        emp_ids = set(get_employees_in_group(group_filter))
        records = [r for r in records if r["emp_id"] in emp_ids]
        group_name = ""
        for g in get_all_groups():
            if g["id"] == group_filter:
                group_name = g["name"]
                break
        file_label = f"考勤统计_{group_name}"
    else:
        file_label = "考勤统计_全部"

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    sheet_suffix = f"_{file_label.replace('考勤统计_', '')}" if group_filter is not None else ""

    # ── Sheet 1: Summary ──
    ws = wb.active
    ws.title = f"考勤统计{sheet_suffix}"

    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(name="微软雅黑", bold=True, color="E5E7EB", size=11)
    cell_font = Font(name="微软雅黑", size=10)
    thin_border = Border(
        left=Side(style="thin", color="374151"), right=Side(style="thin", color="374151"),
        top=Side(style="thin", color="374151"), bottom=Side(style="thin", color="374151"),
    )

    headers = ["工号", "姓名", "部门", "分组", "出勤天数", "缺勤天数", "异常天数", "总打卡次数"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill; cell.font = header_font
        cell.alignment = Alignment(horizontal="center"); cell.border = thin_border

    group_map = get_employee_group_map()
    for i, rec in enumerate(records, 2):
        groups = group_map.get(rec["emp_id"], [])
        group_str = ", ".join(g["name"] for g in groups) if groups else "未分组"
        for col, v in enumerate([rec["emp_id"], rec["name"], rec["dept"], group_str,
                                  rec["attendance_days"], rec["absent_days"], rec["abnormal_days"], rec["total_punches"]], 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.font = cell_font; cell.alignment = Alignment(horizontal="center"); cell.border = thin_border

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16

    # ── Sheet 2: Daily Detail ──
    if include_detail and records and records[0].get("daily_details"):
        ws2 = wb.create_sheet(f"考勤明细{sheet_suffix}")
        rules = get_rules()
        periods = rules.get("time_periods", [])
        period_headers = [f"时段{i+1}({p['start']}-{p['end']})" for i, p in enumerate(periods)]
        headers2 = ["工号", "姓名", "部门", "日期"] + period_headers + ["状态", "有效打卡次数"]
        for col, h in enumerate(headers2, 1):
            cell = ws2.cell(row=1, column=col, value=h)
            cell.fill = header_fill; cell.font = header_font
            cell.alignment = Alignment(horizontal="center"); cell.border = thin_border
        status_map = {"normal": "正常", "abnormal": "异常", "absent": "缺勤"}
        num_periods = len(periods)
        row_idx = 2

        for rec in records:
            sorted_details = sort_details_by_date(rec.get("daily_details", []))
            rec_total = 0
            for detail in sorted_details:
                date_raw = detail.get('date', f"第{detail['day']}天")
                date_display = format_date(date_raw) if date_raw != f"第{detail['day']}天" else date_raw
                vals = [rec["emp_id"], rec["name"], rec["dept"], date_display]
                detail_periods = detail.get("periods", [])
                for i in range(num_periods):
                    if i < len(detail_periods):
                        p = detail_periods[i]
                        vals.append(p["earliest"] if p["earliest"] else "-")
                    else:
                        vals.append("-")
                vc = detail.get("valid_count", 0)
                rec_total += vc
                vals.append(status_map.get(detail["status"], detail["status"]))
                vals.append(vc)
                for col, v in enumerate(vals, 1):
                    cell = ws2.cell(row=row_idx, column=col, value=v)
                    cell.font = cell_font; cell.alignment = Alignment(horizontal="center"); cell.border = thin_border
                row_idx += 1
            # 汇总行
            sum_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
            sum_font = Font(name="微软雅黑", bold=True, color="E5E7EB", size=11)
            sum_vals = [rec["emp_id"], rec["name"], rec["dept"], "汇总"] + [""] * num_periods + ["", rec_total]
            for col, v in enumerate(sum_vals, 1):
                cell = ws2.cell(row=row_idx, column=col, value=v)
                cell.fill = sum_fill; cell.font = sum_font
                cell.alignment = Alignment(horizontal="center"); cell.border = thin_border
            row_idx += 1

        ws2.column_dimensions[get_column_letter(1)].width = 10
        ws2.column_dimensions[get_column_letter(2)].width = 10
        ws2.column_dimensions[get_column_letter(3)].width = 10
        ws2.column_dimensions[get_column_letter(4)].width = 12
        for ci in range(len(period_headers)):
            ws2.column_dimensions[get_column_letter(5 + ci)].width = 18

        # ── Sheet 3: 透视表 ──
        ws3 = wb.create_sheet(f"考勤透视{sheet_suffix}")

        first_details = sort_details_by_date(records[0].get("daily_details", []))
        date_list = []
        for d in first_details:
            raw = d.get('date', f"第{d['day']}天")
            display = format_date(raw) if raw != f"第{d['day']}天" else raw
            date_list.append(display)

        num_days = len(date_list)
        num_periods = len(periods)

        ws3.cell(row=1, column=1, value="工号").fill = header_fill
        ws3.cell(row=1, column=1).font = header_font
        ws3.cell(row=1, column=1).alignment = Alignment(horizontal="center")
        ws3.cell(row=1, column=1).border = thin_border
        ws3.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
        ws3.cell(row=1, column=2, value="姓名").fill = header_fill
        ws3.cell(row=1, column=2).font = header_font
        ws3.cell(row=1, column=2).alignment = Alignment(horizontal="center")
        ws3.cell(row=1, column=2).border = thin_border
        ws3.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
        ws3.cell(row=1, column=3, value="部门").fill = header_fill
        ws3.cell(row=1, column=3).font = header_font
        ws3.cell(row=1, column=3).alignment = Alignment(horizontal="center")
        ws3.cell(row=1, column=3).border = thin_border
        ws3.merge_cells(start_row=1, start_column=3, end_row=2, end_column=3)

        for d_idx, date_display in enumerate(date_list):
            start_col = 4 + d_idx * num_periods
            end_col = start_col + num_periods - 1
            ws3.cell(row=1, column=start_col, value=date_display)
            ws3.cell(row=1, column=start_col).fill = header_fill
            ws3.cell(row=1, column=start_col).font = header_font
            ws3.cell(row=1, column=start_col).alignment = Alignment(horizontal="center")
            ws3.cell(row=1, column=start_col).border = thin_border
            if num_periods > 1:
                ws3.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
            for pi, p in enumerate(periods):
                col = start_col + pi
                ws3.cell(row=2, column=col, value=f"{p['start']}-{p['end']}")
                ws3.cell(row=2, column=col).fill = header_fill
                ws3.cell(row=2, column=col).font = header_font
                ws3.cell(row=2, column=col).alignment = Alignment(horizontal="center")
                ws3.cell(row=2, column=col).border = thin_border

        for ri, rec in enumerate(records, 3):
            ws3.cell(row=ri, column=1, value=rec["emp_id"]).font = cell_font
            ws3.cell(row=ri, column=1).alignment = Alignment(horizontal="center")
            ws3.cell(row=ri, column=1).border = thin_border
            ws3.cell(row=ri, column=2, value=rec["name"]).font = cell_font
            ws3.cell(row=ri, column=2).alignment = Alignment(horizontal="center")
            ws3.cell(row=ri, column=2).border = thin_border
            ws3.cell(row=ri, column=3, value=rec["dept"]).font = cell_font
            ws3.cell(row=ri, column=3).alignment = Alignment(horizontal="center")
            ws3.cell(row=ri, column=3).border = thin_border

            day_map = {d["day"]: d for d in rec.get("daily_details", [])}
            for d_idx, date_display in enumerate(date_list):
                day_num = first_details[d_idx]["day"]
                detail = day_map.get(day_num)
                start_col = 4 + d_idx * num_periods
                for pi in range(num_periods):
                    col = start_col + pi
                    val = ""
                    if detail:
                        periods_info = detail.get("periods", [])
                        if pi < len(periods_info) and periods_info[pi].get("earliest"):
                            val = 1
                    cell = ws3.cell(row=ri, column=col, value=val)
                    cell.font = cell_font
                    cell.alignment = Alignment(horizontal="center")
                    cell.border = thin_border

        ws3.column_dimensions[get_column_letter(1)].width = 10
        ws3.column_dimensions[get_column_letter(2)].width = 10
        ws3.column_dimensions[get_column_letter(3)].width = 10
        for ci in range(num_days * num_periods):
            ws3.column_dimensions[get_column_letter(4 + ci)].width = 8

    # ===== 防御：清空所有工作表的列宽设置 =====
    for sheet in wb.worksheets:
        sheet.column_dimensions.clear()

    buf = BytesIO()
    wb.save(buf); buf.seek(0)
    filename = f"{file_label}_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ── Merge Export ──
@app.post("/api/merge-export")
async def merge_export(files: List[UploadFile] = File(...), authorization: str = Header(None, alias="Authorization")):
    get_payload(authorization)

    from openpyxl import Workbook, load_workbook as xl_load

    # 获取考勤规则（用于计算应打卡次数）
    rules = get_rules()
    min_punch_per_day = rules.get("min_punch_per_day", 2)  # 默认2

    all_records = []       # 用于统计汇总
    all_details = []       # 用于明细汇总
    detail_header = None

    # 用于合并透视表（直接从每个文件的“考勤透视” Sheet 读取）
    merged_pivot = {}          # key: emp_id, value: { 'name': str, 'dept': str, 'periods': [0/1] }
    pivot_header_row1 = None   # 第一个文件的第1行（日期合并行）
    pivot_header_row2 = None   # 第一个文件的第2行（时段行）
    pivot_num_time_cols = 0    # 时段总列数

    for f in files:
        content = await f.read()
        tmp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}.xlsx")
        with open(tmp_path, "wb") as tmp:
            tmp.write(content)
        try:
            wb = xl_load(tmp_path, data_only=True)

            # 1. 读取考勤统计 Sheet
            summary_sheet = None
            for name in wb.sheetnames:
                if name.startswith("考勤统计"):
                    summary_sheet = wb[name]
                    break
            if summary_sheet:
                for row in summary_sheet.iter_rows(min_row=2, values_only=True):
                    if not row[0]:
                        continue
                    all_records.append({
                        "emp_id": str(row[0]).strip(),
                        "name": str(row[1]).strip(),
                        "dept": str(row[2]).strip(),
                        "group": str(row[3]).strip() if row[3] else "未分组",
                        "attendance_days": row[4] or 0,
                        "absent_days": row[5] or 0,
                        "abnormal_days": row[6] or 0,
                        "total_punches": row[7] or 0,
                        # 提取 total_days（如果有）
                        "total_days": int(row[8]) if len(row) > 8 and row[8] else 0,
                    })

            # 2. 读取考勤明细 Sheet（用于明细汇总）
            detail_sheet = None
            for name in wb.sheetnames:
                if name.startswith("考勤明细"):
                    detail_sheet = wb[name]
                    break
            if detail_sheet:
                for row_idx, row in enumerate(detail_sheet.iter_rows(values_only=True)):
                    if row_idx == 0:
                        if detail_header is None:
                            detail_header = list(row)
                        continue
                    if not row[0]:
                        continue
                    row_data = list(row)
                    if len(row_data) > 3 and row_data[3] and str(row_data[3]) == "汇总":
                        continue
                    all_details.append(row_data)

            # 3. 读取考勤透视 Sheet（用于合并时段数据）
            pivot_sheet = None
            for name in wb.sheetnames:
                if name.startswith("考勤透视"):
                    pivot_sheet = wb[name]
                    break
            if pivot_sheet:
                # 获取表头（第1行和第2行）
                row1 = [cell.value for cell in pivot_sheet[1]]
                row2 = [cell.value for cell in pivot_sheet[2]]
                if pivot_header_row1 is None:
                    pivot_header_row1 = row1
                    pivot_header_row2 = row2
                    # 计算时段列数：从第4列开始到最后一列（因为透视表没有总计列，只有时段）
                    pivot_num_time_cols = len(row1) - 3  # 减去前3列（工号、姓名、部门）
                else:
                    # 如果列数不一致，以第一个为准，但可以取最大值，这里简单处理
                    if len(row1) - 3 > pivot_num_time_cols:
                        pivot_num_time_cols = len(row1) - 3
                        pivot_header_row1 = row1
                        pivot_header_row2 = row2

                # 读取数据行（从第3行开始）
                for row in pivot_sheet.iter_rows(min_row=3, values_only=True):
                    if not row[0]:
                        continue
                    emp_id = str(row[0]).strip()
                    name = str(row[1]).strip() if row[1] else ""
                    dept = str(row[2]).strip() if row[2] else ""

                    if emp_id not in merged_pivot:
                        # 初始化时段数据为全0
                        merged_pivot[emp_id] = {
                            'name': name,
                            'dept': dept,
                            'periods': [0] * pivot_num_time_cols
                        }
                    # 合并时段数据（或逻辑）
                    for idx in range(pivot_num_time_cols):
                        val = row[3 + idx] if (3 + idx) < len(row) else 0
                        if val == 1 or val == 1.0:
                            merged_pivot[emp_id]['periods'][idx] = 1

        except Exception as e:
            # 可以记录错误日志，但这里忽略
            pass
        finally:
            os.remove(tmp_path)

    # 去重统计（同之前）
    seen = {}
    total_days = 1  # 默认值
    for r in all_records:
        emp_id = r["emp_id"]
        if emp_id not in seen:
            seen[emp_id] = r
            # 取第一个记录的 total_days（如果存在）
            if r.get("total_days", 0) > 0:
                total_days = r["total_days"]
        else:
            # 合并 total_days（取最大值）
            if r.get("total_days", 0) > seen[emp_id].get("total_days", 0):
                seen[emp_id]["total_days"] = r["total_days"]

    merged = list(seen.values())
    merged.sort(key=lambda x: float(x["emp_id"]) if x["emp_id"].replace('.', '', 1).isdigit() else x["emp_id"])

    # 如果未从统计表中获取 total_days，尝试从透视表推断（但可能不准确）
    if total_days == 1 and pivot_num_time_cols > 0:
        # 从透视表的列数推断天数：时段列数 / 时段数，但时段数未知，取最小可能性
        # 但这不是可靠方法，我们保留默认1
        pass

    # 生成汇总 Excel
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    wb_out = Workbook()

    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    header_font = Font(name="微软雅黑", bold=True, color="E5E7EB", size=11)
    cell_font = Font(name="微软雅黑", size=10)
    thin_border = Border(
        left=Side(style="thin", color="374151"), right=Side(style="thin", color="374151"),
        top=Side(style="thin", color="374151"), bottom=Side(style="thin", color="374151"),
    )

    # ── Sheet 1: 考勤汇总 ──
    ws = wb_out.active
    ws.title = "考勤汇总"
    headers = ["工号", "姓名", "部门", "分组", "出勤天数", "缺勤天数", "异常天数", "总打卡次数", "出勤率"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill; cell.font = header_font
        cell.alignment = Alignment(horizontal="center"); cell.border = thin_border

    # 计算该月应打卡总次数 = 总天数 × 每日最低打卡次数
    required_total = total_days * min_punch_per_day

    for i, rec in enumerate(merged, 2):
        # 出勤率 = 有效打卡总次数 / 应打卡总次数 * 100%
        if required_total > 0:
            rate = f"{round(rec['total_punches'] / required_total * 100)}%"
        else:
            rate = "0%"
        vals = [rec["emp_id"], rec["name"], rec["dept"], rec["group"],
                rec["attendance_days"], rec["absent_days"], rec["abnormal_days"], rec["total_punches"], rate]
        for col, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=col, value=v)
            cell.font = cell_font; cell.alignment = Alignment(horizontal="center"); cell.border = thin_border

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16

    # ── Sheet 2: 考勤明细汇总 ──
    if all_details and detail_header:
        ws2 = wb_out.create_sheet("考勤明细汇总")
        num_cols = len(detail_header)
        for col, v in enumerate(detail_header, 1):
            cell = ws2.cell(row=1, column=col, value=v)
            cell.fill = header_fill; cell.font = header_font
            cell.alignment = Alignment(horizontal="center"); cell.border = thin_border

        # 按工号排序
        def detail_sort_key(r):
            eid = str(r[0]) if r[0] else ""
            day_str = str(r[3]) if len(r) > 3 and r[3] else ""
            try:
                day_num = 0
                if day_str.startswith("第") and day_str.endswith("天"):
                    day_num = int(day_str[1:-1])
                else:
                    match = re.search(r'(\d+)日', day_str)
                    if match:
                        day_num = int(match.group(1))
                return (eid, day_num)
            except:
                return (eid, day_str)
        all_details.sort(key=detail_sort_key)

        for i, row_data in enumerate(all_details, 2):
            for col in range(num_cols):
                v = row_data[col] if col < len(row_data) else None
                cell = ws2.cell(row=i, column=col + 1, value=v)
                cell.font = cell_font; cell.alignment = Alignment(horizontal="center")
                cell.border = thin_border

        ws2.column_dimensions[get_column_letter(1)].width = 10
        ws2.column_dimensions[get_column_letter(2)].width = 10
        ws2.column_dimensions[get_column_letter(3)].width = 10
        ws2.column_dimensions[get_column_letter(4)].width = 12
        for ci in range(max(0, num_cols - 7)):
            ws2.column_dimensions[get_column_letter(5 + ci)].width = 18

    # ── Sheet 3: 考勤透视汇总（直接从合并后的透视表生成） ──
    if merged_pivot and pivot_header_row1 is not None:
        ws3 = wb_out.create_sheet("考勤透视汇总")

        # 原表头列数 = 3 + pivot_num_time_cols
        orig_cols = 3 + pivot_num_time_cols
        # 新增总计列
        total_col = orig_cols + 1

        # 写入表头第1行（第1行和第2行合并单元格已处理）
        for col_idx, val in enumerate(pivot_header_row1, 1):
            if val:
                cell = ws3.cell(row=1, column=col_idx, value=val)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")
                cell.border = thin_border
        # 添加总计列头（合并第1、2行）
        cell_total = ws3.cell(row=1, column=total_col, value="总计")
        cell_total.fill = header_fill
        cell_total.font = header_font
        cell_total.alignment = Alignment(horizontal="center")
        cell_total.border = thin_border
        ws3.merge_cells(start_row=1, start_column=total_col, end_row=2, end_column=total_col)

        # 写入表头第2行（时段标题）
        for col_idx, val in enumerate(pivot_header_row2, 1):
            if val and col_idx <= orig_cols:
                cell = ws3.cell(row=2, column=col_idx, value=val)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center")
                cell.border = thin_border

        # 写入数据行
        sorted_emp_ids = sorted(merged_pivot.keys(), key=lambda x: float(x) if x.replace('.', '', 1).isdigit() else x)
        row_idx = 3
        for emp_id in sorted_emp_ids:
            data = merged_pivot[emp_id]
            name = data['name']
            dept = data['dept']
            periods = data['periods']
            # 确保periods长度与pivot_num_time_cols一致
            while len(periods) < pivot_num_time_cols:
                periods.append(0)
            # 计算总计
            total_sum = sum(1 for p in periods if p == 1)

            row_vals = [emp_id, name, dept] + [1 if p == 1 else None for p in periods] + [total_sum]

            for col_idx, val in enumerate(row_vals, 1):
                cell = ws3.cell(row=row_idx, column=col_idx, value=val)
                cell.font = cell_font
                cell.alignment = Alignment(horizontal="center")
                cell.border = thin_border
            row_idx += 1

        # 设置列宽
        ws3.column_dimensions[get_column_letter(1)].width = 10
        ws3.column_dimensions[get_column_letter(2)].width = 10
        ws3.column_dimensions[get_column_letter(3)].width = 10
        for ci in range(4, orig_cols + 1):
            ws3.column_dimensions[get_column_letter(ci)].width = 8
        ws3.column_dimensions[get_column_letter(total_col)].width = 10

    # ===== 清空所有列宽设置（防御） =====
    for sheet in wb_out.worksheets:
        sheet.column_dimensions.clear()

    buf = BytesIO()
    wb_out.save(buf); buf.seek(0)
    filename = f"考勤汇总_全体_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ── Frontend ──
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/")
def serve_index():
    resp = FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.on_event("startup")
def startup():
    init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)