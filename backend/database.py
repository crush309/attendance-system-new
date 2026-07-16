import psycopg2
import psycopg2.extras
import os
import hashlib
import secrets
import json
from typing import Optional, List, Dict, Tuple

DATABASE_URL = os.environ.get("DATABASE_URL")

ADMIN_USER = "admin"
ADMIN_DEFAULT_PASS = "admin123"


def hash_password(password: str, salt: str = None) -> Tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}:{h.hex()}", salt


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    computed, _ = hash_password(password, salt)
    return computed == stored


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def _column_exists(conn, table, column):
    c = conn.cursor()
    c.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return c.fetchone() is not None


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname VARCHAR(255) DEFAULT '',
            avatar TEXT DEFAULT '',
            role VARCHAR(50) NOT NULL DEFAULT 'user',
            permissions TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    if not _column_exists(conn, 'users', 'permissions'):
        c.execute("ALTER TABLE users ADD COLUMN permissions TEXT DEFAULT '{}'")
    c.execute("""
        CREATE TABLE IF NOT EXISTS attendance_rules (
            id SERIAL PRIMARY KEY,
            time_periods TEXT DEFAULT '[]',
            min_punch_per_day INTEGER DEFAULT 2,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS groups_table (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS employee_groups (
            id SERIAL PRIMARY KEY,
            emp_id VARCHAR(255) NOT NULL,
            group_id INTEGER NOT NULL,
            FOREIGN KEY (group_id) REFERENCES groups_table(id) ON DELETE CASCADE,
            UNIQUE(emp_id, group_id)
        )
    """)
    conn.commit()

    c.execute("SELECT COUNT(*) FROM attendance_rules")
    if c.fetchone()[0] == 0:
        default_periods = json.dumps([
            {"start": "08:00", "end": "12:00"},
            {"start": "13:00", "end": "18:00"}
        ])
        c.execute("INSERT INTO attendance_rules (time_periods, min_punch_per_day) VALUES (%s, %s)", (default_periods, 2))
    c.execute("SELECT COUNT(*) FROM users WHERE username = %s", (ADMIN_USER,))
    if c.fetchone()[0] == 0:
        hashed, _ = hash_password(ADMIN_DEFAULT_PASS)
        c.execute("INSERT INTO users (username, password_hash, nickname, role, permissions) VALUES (%s, %s, %s, %s, %s)",
                  (ADMIN_USER, hashed, "管理员", "admin", "{}"))
    conn.commit()
    conn.close()


# ── Users ──

def create_user(username: str, password: str) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor()
    try:
        hashed, _ = hash_password(password)
        c.execute("INSERT INTO users (username, password_hash, nickname, role, permissions) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                  (username, hashed, username, "user", "{}"))
        uid = c.fetchone()[0]
        conn.commit()
        conn.close()
        return {"id": uid, "username": username, "nickname": username, "role": "user", "permissions": {}}
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return {
            "id": row["id"], "username": row["username"],
            "nickname": row["nickname"] or row["username"],
            "role": row["role"], "avatar": row["avatar"] or "",
            "permissions": json.loads(row["permissions"]) if row["permissions"] else {},
        }
    return None


def get_user_by_id(uid: int) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM users WHERE id = %s", (uid,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "id": row["id"], "username": row["username"],
            "nickname": row["nickname"] or row["username"],
            "role": row["role"], "avatar": row["avatar"] or "",
            "permissions": json.loads(row["permissions"]) if row["permissions"] else {},
            "created_at": str(row["created_at"]) if row["created_at"] else "",
        }
    return None


def update_user_profile(uid: int, nickname: str = None, avatar: str = None) -> bool:
    conn = get_conn()
    c = conn.cursor()
    fields, vals = [], []
    if nickname is not None:
        fields.append("nickname = %s"); vals.append(nickname)
    if avatar is not None:
        fields.append("avatar = %s"); vals.append(avatar)
    if not fields:
        conn.close(); return False
    vals.append(uid)
    c.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = %s", vals)
    conn.commit(); ok = c.rowcount > 0; conn.close(); return ok


def change_password(uid: int, old_password: str, new_password: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE id = %s", (uid,))
    row = c.fetchone()
    if not row or not verify_password(old_password, row[0]):
        conn.close(); return False
    hashed, _ = hash_password(new_password)
    c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed, uid))
    conn.commit(); conn.close(); return True


def get_all_users() -> List[dict]:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT id, username, nickname, role, avatar, permissions, created_at FROM users ORDER BY id")
    rows = []
    for r in c.fetchall():
        d = dict(r)
        d["permissions"] = json.loads(d["permissions"]) if d["permissions"] else {}
        d["created_at"] = str(d["created_at"]) if d["created_at"] else ""
        rows.append(d)
    conn.close()
    return rows


def update_user_permissions(uid: int, permissions: dict) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET permissions = %s WHERE id = %s", (json.dumps(permissions), uid))
    conn.commit(); ok = c.rowcount > 0; conn.close(); return ok


# ── Rules ──

def get_rules() -> dict:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("SELECT * FROM attendance_rules ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        return {"time_periods": json.loads(row["time_periods"]) if row["time_periods"] else [], "min_punch_per_day": row["min_punch_per_day"]}
    return {"time_periods": [{"start": "08:00", "end": "12:00"}, {"start": "13:00", "end": "18:00"}], "min_punch_per_day": 2}


def update_rules(rules: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM attendance_rules")
    c.execute("INSERT INTO attendance_rules (time_periods, min_punch_per_day) VALUES (%s, %s)",
              (json.dumps(rules["time_periods"]), rules["min_punch_per_day"]))
    conn.commit(); conn.close()


# ── Groups ──

def create_group(name: str) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO groups_table (name) VALUES (%s) RETURNING id", (name,))
        gid = c.fetchone()[0]
        conn.commit(); conn.close()
        return {"id": gid, "name": name}
    except psycopg2.IntegrityError:
        conn.rollback(); conn.close(); return None


def get_all_groups() -> List[dict]:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("""SELECT g.id, g.name, g.created_at, COUNT(eg.id) as member_count
        FROM groups_table g LEFT JOIN employee_groups eg ON g.id = eg.group_id
        GROUP BY g.id ORDER BY g.id""")
    rows = [dict(r) for r in c.fetchall()]
    conn.close(); return rows


def delete_group(group_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM employee_groups WHERE group_id = %s", (group_id,))
    c.execute("DELETE FROM groups_table WHERE id = %s", (group_id,))
    conn.commit(); ok = c.rowcount > 0; conn.close(); return ok


def bulk_assign_employees(emp_ids: List[str], group_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    count = 0
    for eid in emp_ids:
        try:
            c.execute("INSERT INTO employee_groups (emp_id, group_id) VALUES (%s, %s)", (eid, group_id)); count += 1
        except psycopg2.IntegrityError:
            conn.rollback()
    conn.commit(); conn.close(); return count


def remove_employee_from_group(emp_id: str, group_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM employee_groups WHERE emp_id = %s AND group_id = %s", (emp_id, group_id))
    conn.commit(); ok = c.rowcount > 0; conn.close(); return ok


def get_employee_group_map() -> Dict[str, List[dict]]:
    conn = get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute("""SELECT eg.emp_id, g.id as group_id, g.name as group_name
        FROM employee_groups eg JOIN groups_table g ON eg.group_id = g.id ORDER BY g.id""")
    result = {}
    for row in c.fetchall():
        eid = row["emp_id"]
        if eid not in result: result[eid] = []
        result[eid].append({"id": row["group_id"], "name": row["group_name"]})
    conn.close(); return result


def get_employees_in_group(group_id: int) -> List[str]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT emp_id FROM employee_groups WHERE group_id = %s", (group_id,))
    rows = [r[0] for r in c.fetchall()]
    conn.close(); return rows


def delete_user(uid: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("SELECT id FROM users WHERE id = %s", (uid,))
        if not c.fetchone():
            conn.close()
            return False
        c.execute("DELETE FROM employee_groups WHERE emp_id = %s", (str(uid),))
        c.execute("DELETE FROM users WHERE id = %s", (uid,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback()
        conn.close()
        raise
