import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.db_transaction import transaction
from src.fleet_management_api import validate_schedule_payload


ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(
        self, rows=None, foreign_keys=None, unique_keys=None, fail_on_execute=None
    ):
        self.rows = rows or []
        self.foreign_keys = foreign_keys or []
        self.unique_keys = unique_keys or []
        self.fail_on_execute = fail_on_execute
        self.execute_count = 0
        self.statements = []
        self.closed = False
        self.rowcount = 1
        self.last_statement = ""

    def execute(self, statement, params=None):
        self.execute_count += 1
        self.last_statement = statement
        self.statements.append((statement, params))
        if self.execute_count == self.fail_on_execute:
            raise RuntimeError("simulated database failure")

    def fetchall(self):
        if "constraint_type = 'FOREIGN KEY'" in self.last_statement:
            return self.foreign_keys
        if "PRIMARY KEY" in self.last_statement:
            return self.unique_keys
        return self.rows

    def fetchone(self):
        return ("2.0.0",)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(
        self, rows=None, foreign_keys=None, unique_keys=None, fail_on_execute=None
    ):
        self.fake_cursor = FakeCursor(rows, foreign_keys, unique_keys, fail_on_execute)
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


def load_db_module():
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.Error = Exception
    psycopg2.connect = Mock()
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
        spec = importlib.util.spec_from_file_location(
            "src.db_phase1b_test", ROOT / "src" / "db.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


class DatabaseInitializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = load_db_module()

    def create_manager(self, connection):
        manager = self.db_module.DatabaseManager({})
        manager.create_connection = Mock(return_value=connection)
        return manager

    def full_schema_metadata(self):
        columns = [
            (table, column)
            for table, required_columns in self.db_module.REQUIRED_SCHEMA_COLUMNS.items()
            for column in required_columns
        ]
        return {
            "rows": columns,
            "foreign_keys": self.db_module.REQUIRED_FOREIGN_KEYS,
            "unique_keys": self.db_module.REQUIRED_UNIQUE_KEYS,
        }

    def test_default_schema_path_is_repository_root_relative_and_has_fleet_tables(self):
        connection = FakeConnection(**self.full_schema_metadata())
        manager = self.create_manager(connection)

        self.assertTrue(manager.initialize_schema())
        schema_sql = connection.fake_cursor.statements[0][0]
        self.assertIn("CREATE TABLE IF NOT EXISTS route_assignments", schema_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS vehicle_tracking", schema_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS scenarios", schema_sql)
        self.assertEqual(
            connection.fake_cursor.execute_count,
            4,
        )
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_canonical_schema_has_relational_constraints_and_consistent_history_time(self):
        schema = (ROOT / "docs" / "database_schema.sql").read_text(encoding="utf-8")
        predictor = (
            ROOT / "src" / "collection_window_predictor.py"
        ).read_text(encoding="utf-8")

        for table in self.db_module.REQUIRED_SCHEMA_COLUMNS:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)
        self.assertIn("REFERENCES bins(id) ON DELETE RESTRICT", schema)
        self.assertIn("REFERENCES route_assignments(assignment_id) ON DELETE RESTRICT", schema)
        self.assertIn("CHECK (latitude BETWEEN -90 AND 90)", schema)
        self.assertIn("idx_fill_history_bin_recorded ON fill_history(bin_id, recorded_at DESC)", schema)
        self.assertIn("fh.recorded_at", predictor)
        self.assertNotIn("fh.timestamp", predictor)

    def test_incompatible_existing_schema_fails_and_rolls_back(self):
        connection = FakeConnection(rows=[("bins", "id")])
        manager = self.create_manager(connection)

        self.assertFalse(manager.initialize_schema())
        self.assertIn("incompatible", manager.last_error)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_schema_missing_required_foreign_keys_fails_and_rolls_back(self):
        metadata = self.full_schema_metadata()
        metadata["foreign_keys"] = ()
        connection = FakeConnection(**metadata)
        manager = self.create_manager(connection)

        self.assertFalse(manager.initialize_schema())
        self.assertIn("missing foreign keys", manager.last_error)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_schema_missing_required_unique_key_fails_and_rolls_back(self):
        metadata = self.full_schema_metadata()
        metadata["unique_keys"] = ()
        connection = FakeConnection(**metadata)
        manager = self.create_manager(connection)

        self.assertFalse(manager.initialize_schema())
        self.assertIn("missing primary/unique keys", manager.last_error)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_csv_bin_id_maps_to_bins_primary_key_and_commits_atomically(self):
        connection = FakeConnection()
        manager = self.create_manager(connection)
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "bins.csv"
            csv_path.write_text(
                "bin_id,latitude,longitude,fill_level,status\n"
                "BIN-7,18.5,73.8,85,1\n",
                encoding="utf-8",
            )

            self.assertEqual(manager.load_sample_data(csv_path), 1)

        statement, params = connection.fake_cursor.statements[0]
        self.assertIn("(id, latitude, longitude, fill_level, status)", statement)
        self.assertEqual(params[0], "BIN-7")
        self.assertEqual(params[1:], (18.5, 73.8, 85, 1))
        self.assertIn("ON CONFLICT (id) DO NOTHING", statement)
        self.assertNotIn("DO UPDATE", statement)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_csv_write_failure_rolls_back_and_closes_resources(self):
        connection = FakeConnection(fail_on_execute=2)
        manager = self.create_manager(connection)
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "bins.csv"
            csv_path.write_text(
                "bin_id,latitude,longitude,fill_level,status\n"
                "BIN-1,18,73,50,0\nBIN-2,19,74,60,0\n",
                encoding="utf-8",
            )

            self.assertEqual(manager.load_sample_data(csv_path), 0)

        self.assertIn("simulated database failure", manager.last_error)
        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_invalid_csv_is_rejected_before_database_connection(self):
        manager = self.db_module.DatabaseManager({})
        manager.create_connection = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "invalid.csv"
            csv_path.write_text(
                "bin_id,latitude,longitude,fill_level,status\n"
                "BIN-1,not-a-coordinate,73,50,0\n",
                encoding="utf-8",
            )

            self.assertEqual(manager.load_sample_data(csv_path), 0)

        manager.create_connection.assert_not_called()

    def test_init_database_uses_canonical_schema_and_data_paths(self):
        manager = Mock()
        manager.initialize_schema.return_value = True
        manager.load_sample_data.return_value = 3
        manager.record_migration.return_value = True
        manager.health_check.return_value = {"status": "healthy"}
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            self.db_module, "DatabaseManager", return_value=manager
        ):
            root = Path(temp_dir)
            (root / "docs").mkdir()
            (root / "data").mkdir()
            self.assertTrue(self.db_module.init_database(str(root), {}))

        self.assertEqual(
            manager.initialize_schema.call_args.args[0],
            root / "docs" / "database_schema.sql",
        )
        self.assertEqual(
            manager.load_sample_data.call_args.args[0],
            root / "data" / "pune_smart_bin_dataset.csv",
        )


class FleetSchedulePayloadTests(unittest.TestCase):
    def setUp(self):
        self.drivers = [{"driver_id": "D1", "name": "Driver 1"}]
        self.vehicles = [{"vehicle_id": "V1", "license_plate": "PLATE-1"}]

    def test_bin_id_route_payload_is_accepted(self):
        self.assertIsNone(
            validate_schedule_payload([["BIN-1", "BIN-2"]], self.drivers, self.vehicles)
        )

    def test_route_object_payload_is_rejected(self):
        error = validate_schedule_payload(
            [[{"id": "BIN-1", "lat": 18.5, "lng": 73.8}]],
            self.drivers,
            self.vehicles,
        )
        self.assertIn("bin ID strings", error)

    def test_missing_driver_or_vehicle_fields_are_rejected(self):
        self.assertIn(
            "driver_id",
            validate_schedule_payload([["BIN-1"]], [{"name": "Driver"}], self.vehicles),
        )
        self.assertIn(
            "license_plate",
            validate_schedule_payload(
                [["BIN-1"]], self.drivers, [{"vehicle_id": "V1"}]
            ),
        )

    def test_duplicate_or_excessive_bin_ids_are_rejected(self):
        self.assertIn(
            "more than once",
            validate_schedule_payload(
                [["BIN-1"], ["BIN-1"]], self.drivers, self.vehicles
            ),
        )
        self.assertIn(
            "at most 200",
            validate_schedule_payload(
                [[f"BIN-{index}" for index in range(201)]],
                self.drivers,
                self.vehicles,
            ),
        )

    def test_scheduling_persists_bin_id_lists_and_uses_database_bins(self):
        from src.fleet_management_api import FleetManagementAPI

        connection = FakeConnection()
        manager = types.SimpleNamespace(get_connection=lambda: connection)
        fake_app = types.SimpleNamespace(
            route_distance_km=lambda bins: 3.5,
            get_bins_data=lambda: [{"id": "BIN-1", "lat": 18.5, "lng": 73.8}],
        )
        api = FleetManagementAPI(manager, cache_manager=None)

        with patch.dict(sys.modules, {"app": fake_app}):
            result = api.schedule_routes([["BIN-1"]], self.drivers, self.vehicles)

        self.assertTrue(result["success"])
        statement, params = connection.fake_cursor.statements[0]
        self.assertIn("INSERT INTO route_assignments", statement)
        self.assertEqual(params[1], '["BIN-1"]')
        self.assertEqual(params[2], 3.5)
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)


if __name__ == "__main__":
    unittest.main()
