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


# ── Profile ──

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


# ── Rules ──

@app.get("/api/rules")
def api_get_rules():
    return get_rules()

@app.put("/api/rules")
def api_update_rules(req: RulesUpdate, authorization: str = Header(None, alias="Authorization")):
    is_admin_or_perm(authorization, "edit_rules")
    update_rules({"time_periods": [p.model_dump() for p in req.time_periods], "min_punch_per_day": req.min_punch_per_day})
    return {"message": "规则已更新"}


# ── Users ──

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


# ── Upload ──

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


# ── Groups ──

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

    # 设置列宽
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
        total_punch_col = 4 + num_periods + 2  # 有效打卡次数列号
        for rec in records:
            rec_total = 0
            for detail in rec.get("daily_details", []):
                vals = [rec["emp_id"], rec["name"], rec["dept"], f"第{detail['day']}天"]
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
            # 该员工汇总行
            sum_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
            sum_font = Font(name="微软雅黑", bold=True, color="E5E7EB", size=11)
            sum_vals = [rec["emp_id"], rec["name"], rec["dept"], "汇总"] + [""] * num_periods + ["", rec_total]
            for col, v in enumerate(sum_vals, 1):
                cell = ws2.cell(row=row_idx, column=col, value=v)
                cell.fill = sum_fill; cell.font = sum_font
                cell.alignment = Alignment(horizontal="center"); cell.border = thin_border
            row_idx += 1

        # 设置列宽
        ws2.column_dimensions[get_column_letter(1)].width = 10
        ws2.column_dimensions[get_column_letter(2)].width = 10
        ws2.column_dimensions[get_column_letter(3)].width = 10
        ws2.column_dimensions[get_column_letter(4)].width = 10
        for ci in range(len(period_headers)):
            ws2.column_dimensions[get_column_letter(5 + ci)].width = 18

        # ── Sheet 3: 透视表 ──
        ws3 = wb.create_sheet(f"考勤透视{sheet_suffix}")
        num_days = records[0].get("total_days", 0) if records else 0
        num_periods = len(periods)

        # 表头
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

        for d in range(num_days):
            start_col = 4 + d * num_periods
            end_col = start_col + num_periods - 1
            ws3.cell(row=1, column=start_col, value=f"第{d+1}天")
            ws3.cell(row=1, column=start_col).fill = header_fill
            ws3.cell(row=1, column=start_col).font = header_font
            ws3.cell(row=1, column=start_col).alignment = Alignment(horizontal="center")
            ws3.cell(row=1, column=start_col).border = thin_border
            if num_periods > 1:
                ws3.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
            for pi, p in enumerate(periods):
                col = start_col + pi
                ws3.cell(row=2, column=col, value=f"时段{pi+1}")
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
            for d in range(num_days):
                detail = day_map.get(d + 1)
                start_col = 4 + d * num_periods
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

        # 设置列宽
        ws3.column_dimensions[get_column_letter(1)].width = 10
        ws3.column_dimensions[get_column_letter(2)].width = 10
        ws3.column_dimensions[get_column_letter(3)].width = 10
        for ci in range(num_days * num_periods):
            ws3.column_dimensions[get_column_letter(4 + ci)].width = 8

    # ===== 终极防御：清空所有工作表的列宽设置（彻底删除非法键） =====
    for sheet in wb.worksheets:
        sheet.column_dimensions.clear()
    # ================================================================

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

    all_records = []
    all_details = []
    detail_header = None
    total_days = 0

    for f in files:
        content = await f.read()
        tmp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}.xlsx")
        with open(tmp_path, "wb") as tmp:
            tmp.write(content)
        try:
            wb = xl_load(tmp_path, data_only=True)
            # 读取考勤统计 Sheet
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
                    })
            # 读取考勤明细 Sheet
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
                    # 跳过汇总行
                    if len(row_data) > 3 and row_data[3] and str(row_data[3]) == "汇总":
                        continue
                    if len(row_data) > 3 and row_data[3] and str(row_data[3]).startswith("第"):
                        d = int(str(row_data[3]).replace("第", "").replace("天", ""))
                        if d > total_days:
                            total_days = d
                    all_details.append(row_data)
        except Exception:
            pass
        finally:
            os.remove(tmp_path)

    # 去重统计
    seen = {}
    for r in all_records:
        seen[r["emp_id"]] = r
    merged = list(seen.values())
    merged.sort(key=lambda x: float(x["emp_id"]) if x["emp_id"].replace('.', '', 1).isdigit() else x["emp_id"])

    if not total_days:
        total_days = 1
    for r in merged:
        r["total_days"] = total_days

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

    for i, rec in enumerate(merged, 2):
        rate = f"{round(rec['total_punches'] / (total_days * 3) * 100)}%" if total_days else "0%"
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
        def detail_sort_key(r):
            eid = str(r[0]) if r[0] else ""
            day = str(r[3]) if len(r) > 3 and r[3] else ""
            try:
                return (float(eid), day)
            except:
                return (eid, day)
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
        ws2.column_dimensions[get_column_letter(4)].width = 10
        for ci in range(max(0, num_cols - 7)):
            ws2.column_dimensions[get_column_letter(5 + ci)].width = 18

    # ===== 终极防御：清空所有工作表的列宽设置 =====
    for sheet in wb_out.worksheets:
        sheet.column_dimensions.clear()
    # ============================================

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