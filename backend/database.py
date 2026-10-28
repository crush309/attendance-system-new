"""Choose local SQLite unless an explicit PostgreSQL DATABASE_URL is configured."""
import os
from importlib import import_module

_backend_name = "postgres_database" if os.environ.get("DATABASE_URL") else "sqlite_database"
_backend = import_module(f".{_backend_name}", __package__) if __package__ else import_module(_backend_name)

__all__ = ['init_db', 'create_user', 'authenticate_user', 'get_user_by_id', 'update_user_profile', 'change_password', 'get_all_users', 'update_user_permissions', 'get_rules', 'update_rules', 'create_group', 'get_all_groups', 'delete_group', 'bulk_assign_employees', 'remove_employee_from_group', 'get_employee_group_map', 'get_employees_in_group', 'delete_user']
for _name in __all__:
    globals()[_name] = getattr(_backend, _name)
