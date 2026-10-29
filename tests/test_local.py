import importlib
from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


class LocalDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_url = os.environ.pop('DATABASE_URL', None)
        self.old_path = os.environ.get('SQLITE_DB_PATH')
        os.environ['SQLITE_DB_PATH'] = str(Path(self.temp.name) / 'attendance.db')
        import database
        self.db = importlib.reload(database)
        self.db.init_db()

    def tearDown(self):
        for key, value in [('DATABASE_URL', self.old_url), ('SQLITE_DB_PATH', self.old_path)]:
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_initialize_twice_and_persist_rules(self):
        rules = {'time_periods': [{'start': '09:00', 'end': '10:00'}], 'min_punch_per_day': 1}
        self.db.update_rules(rules)
        self.db.init_db()
        self.assertEqual(self.db.get_rules(), rules)
        self.assertEqual(len(self.db.get_all_users()), 1)
        self.assertTrue(Path(os.environ['SQLITE_DB_PATH']).is_file())

    def test_users_authentication_permissions_and_password(self):
        self.assertIsNotNone(self.db.authenticate_user('admin', 'admin123'))
        self.assertIsNone(self.db.authenticate_user('admin', 'wrong'))
        user = self.db.create_user('local-user', 'test-password')
        self.assertIsNone(self.db.create_user('local-user', 'duplicate'))
        uid = user['id']
        self.db.update_user_profile(uid, nickname='本地用户', avatar='avatar')
        self.db.update_user_permissions(uid, {'edit_groups': True})
        profile = self.db.get_user_by_id(uid)
        self.assertEqual(profile['nickname'], '本地用户')
        self.assertEqual(profile['permissions'], {'edit_groups': True})
        self.assertFalse(self.db.change_password(uid, 'wrong', 'new-password'))
        self.assertTrue(self.db.change_password(uid, 'test-password', 'new-password'))
        self.assertIsNotNone(self.db.authenticate_user('local-user', 'new-password'))
        self.assertTrue(self.db.delete_user(uid))
        self.assertIsNone(self.db.get_user_by_id(uid))

    def test_group_assignment_survives_duplicates_and_removal(self):
        group = self.db.create_group('测试组')
        gid = group['id']
        self.assertIsNone(self.db.create_group('测试组'))
        self.assertEqual(self.db.bulk_assign_employees(['001', '001', '002'], gid), 2)
        self.assertEqual(self.db.get_employees_in_group(gid), ['001', '002'])
        self.assertEqual(self.db.get_all_groups()[0]['member_count'], 2)
        self.assertEqual(self.db.get_employee_group_map()['001'], [{'id': gid, 'name': '测试组'}])
        self.assertTrue(self.db.remove_employee_from_group('001', gid))
        self.assertTrue(self.db.delete_group(gid))
        self.assertEqual(self.db.get_employee_group_map(), {})

    def test_cloud_bootstrap_password_does_not_replace_existing_admin(self):
        import sqlite_database
        previous = os.environ.get('ADMIN_INITIAL_PASSWORD')
        try:
            os.environ['ADMIN_INITIAL_PASSWORD'] = 'test-cloud-bootstrap-password'
            importlib.reload(sqlite_database)

            os.environ['SQLITE_DB_PATH'] = str(Path(self.temp.name) / 'cloud.db')
            sqlite_database.init_db()
            self.assertIsNone(sqlite_database.authenticate_user('admin', 'admin123'))
            self.assertIsNotNone(sqlite_database.authenticate_user('admin', 'test-cloud-bootstrap-password'))
            os.environ['ADMIN_INITIAL_PASSWORD'] = 'different-bootstrap-password'
            importlib.reload(sqlite_database)
            sqlite_database.init_db()
            self.assertIsNotNone(sqlite_database.authenticate_user('admin', 'test-cloud-bootstrap-password'))
        finally:
            if previous is None:
                os.environ.pop('ADMIN_INITIAL_PASSWORD', None)
            else:
                os.environ['ADMIN_INITIAL_PASSWORD'] = previous
            importlib.reload(sqlite_database)


    def test_legacy_ids_are_preserved_without_becoming_name_assignments(self):
        import sqlite_database
        group = self.db.create_group('旧版分组')
        with closing(sqlite_database.get_conn()) as conn, conn:
            conn.execute('INSERT INTO employee_groups (emp_id, group_id) VALUES (?, ?)', ('001', group['id']))
        self.db.init_db()
        self.assertEqual(self.db.get_employee_group_map(), {})
        self.assertEqual(self.db.get_employees_in_group(group['id']), [])
        self.assertEqual(self.db.get_all_groups()[0]['legacy_member_count'], 1)
        self.assertEqual(self.db.bulk_assign_employees([' 张三 ', '张三', '李四'], group['id']), 2)
        self.db.init_db()
        self.assertEqual(set(self.db.get_employees_in_group(group['id'])), {'张三', '李四'})
        with closing(sqlite_database.get_conn()) as conn:
            self.assertEqual(conn.execute('SELECT emp_id FROM employee_groups').fetchone()[0], '001')


if __name__ == '__main__':
    unittest.main()
