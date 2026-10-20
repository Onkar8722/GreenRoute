import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from src.bin_upload import (
    MAX_BIN_UPLOAD_BYTES,
    parse_bins_csv,
    register_bins_upload_endpoint,
)
from src.db_transaction import transaction
from src.fleet_management_api import FleetManagementAPI
from src.telemetry_storage import save_telemetry


ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, fail_on_execute=None):
        self.fail_on_execute = fail_on_execute
        self.execute_count = 0
        self.closed = False
        self.statements = []

    def execute(self, statement, params=None):
        self.execute_count += 1
        self.statements.append((statement, params))
        if self.execute_count == self.fail_on_execute:
            raise RuntimeError("simulated database failure")

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail_on_execute=None):
        self.fake_cursor = FakeCursor(fail_on_execute)
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


class TransactionTests(unittest.TestCase):
    def test_transaction_rolls_back_and_closes_resources_on_failure(self):
        connection = FakeConnection(fail_on_execute=1)

        with self.assertRaisesRegex(RuntimeError, "simulated database failure"):
            with transaction(connection) as cursor:
                cursor.execute("UPDATE bins SET fill_level = %s", (99,))

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_transaction_commits_and_closes_resources_on_success(self):
        connection = FakeConnection()

        with transaction(connection) as cursor:
            cursor.execute("UPDATE bins SET fill_level = %s", (20,))

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)


class BinUploadTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            MAX_CONTENT_LENGTH=MAX_BIN_UPLOAD_BYTES,
        )
        self.app.logger.disabled = True
        register_bins_upload_endpoint(self.app, lambda: self.connection)
        self.client = self.app.test_client()

    def upload(self, csv_text):
        response = self.client.post(
            "/api/bins_upload",
            data={"file": (io.BytesIO(csv_text.encode("utf-8")), "bins.csv")},
            content_type="multipart/form-data",
        )
        self.addCleanup(response.close)
        return response

    def test_actual_upload_route_is_registered_and_dashboard_uses_it(self):
        registered_paths = {rule.rule for rule in self.app.url_map.iter_rules()}
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")

        self.assertIn("/api/bins_upload", registered_paths)
        self.assertIn("fetch('/api/bins_upload'", template)
        self.assertIn("register_bins_upload_endpoint(app, get_db_connection)", app_source)

    def test_invalid_csv_is_rejected_before_database_connection(self):
        response = self.upload(
            "bin_id,latitude,longitude,fill_level,status\nB1,north,20,50,0\n"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.connection.fake_cursor.execute_count, 0)
        self.assertEqual(self.connection.commit_count, 0)
        self.assertFalse(self.connection.closed)

    def test_validation_rejects_bad_coordinates_status_and_duplicate_ids(self):
        invalid_rows = [
            "B1,91,20,50,0",
            "B1,18,181,50,0",
            "B1,18,20,101,0",
            "B1,18,20,50,2",
            "B1,18,20,nope,0",
            "B1,18,20,50,0\nB1,19,21,60,0",
        ]
        header = "bin_id,latitude,longitude,fill_level,status\n"

        for rows in invalid_rows:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    parse_bins_csv((header + rows + "\n").encode("utf-8"))

    def test_upload_database_failure_rolls_back_and_closes_resources(self):
        self.connection = FakeConnection(fail_on_execute=2)
        response = self.upload(
            "bin_id,latitude,longitude,fill_level,status\n"
            "B1,18,20,50,0\n"
            "B2,19,21,80,1\n"
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.connection.commit_count, 0)
        self.assertEqual(self.connection.rollback_count, 1)
        self.assertTrue(self.connection.fake_cursor.closed)
        self.assertTrue(self.connection.closed)

    def test_valid_upload_commits_once_and_closes_resources(self):
        response = self.upload(
            "bin_id,latitude,longitude,fill_level,status\nB1,18,20,85,1\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 1)
        self.assertEqual(self.connection.commit_count, 1)
        self.assertEqual(self.connection.rollback_count, 0)
        self.assertTrue(self.connection.fake_cursor.closed)
        self.assertTrue(self.connection.closed)
        self.assertIn("ON CONFLICT (id) DO UPDATE SET", self.connection.fake_cursor.statements[0][0])

    def test_oversized_upload_is_rejected_without_database_access(self):
        response = self.client.post(
            "/api/bins_upload",
            data=b"x",
            content_type="multipart/form-data",
            environ_overrides={"CONTENT_LENGTH": str(MAX_BIN_UPLOAD_BYTES + 1)},
        )
        self.addCleanup(response.close)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.connection.fake_cursor.execute_count, 0)
        self.assertEqual(self.connection.commit_count, 0)


class FleetWriteTransactionTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection(fail_on_execute=1)
        self.api = FleetManagementAPI(
            db_manager=types.SimpleNamespace(get_connection=lambda: self.connection),
            cache_manager=None,
        )

    def assert_failed_write_rolled_back(self, result):
        self.assertFalse(result["success"])
        self.assertEqual(self.connection.rollback_count, 1)
        self.assertEqual(self.connection.commit_count, 0)
        self.assertTrue(self.connection.fake_cursor.closed)
        self.assertTrue(self.connection.closed)

    def test_tracking_failure_rolls_back_and_closes(self):
        result = self.api.update_vehicle_location("assignment", 18, 20, None)
        self.assert_failed_write_rolled_back(result)

    def test_scenario_failure_rolls_back_and_closes(self):
        result = self.api.create_scenario("scenario", {}, {})
        self.assert_failed_write_rolled_back(result)

    def test_scheduling_failure_rolls_back_and_closes(self):
        fake_app = types.SimpleNamespace(
            route_distance_km=lambda bins: 0,
            get_bins_data=lambda: [],
        )
        with patch.dict(sys.modules, {"app": fake_app}):
            result = self.api.schedule_routes(
                routes_data=[["bin-1"]],
                drivers_data=[{"driver_id": "D1", "name": "Driver"}],
                vehicles_data=[{"vehicle_id": "V1", "license_plate": "PLATE"}],
            )

        self.assert_failed_write_rolled_back(result)


class TelemetryWriteTransactionTests(unittest.TestCase):
    def test_related_telemetry_bin_history_and_alert_writes_are_atomic(self):
        connection = FakeConnection(fail_on_execute=3)
        payload = {
            "truck_id": "truck",
            "gps_latitude": 18,
            "gps_longitude": 20,
            "recorded_at": "2026-10-19T12:00:00",
            "bin_id": "bin-1",
            "fill_level": 95,
            "status": 1,
        }

        with self.assertRaisesRegex(RuntimeError, "simulated database failure"):
            save_telemetry(connection, payload, lambda value: value)

        self.assertEqual(connection.fake_cursor.execute_count, 3)
        self.assertEqual(connection.rollback_count, 1)
        self.assertEqual(connection.commit_count, 0)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)


class ProductionSecretTests(unittest.TestCase):
    def load_config_module(self, environment):
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = lambda **kwargs: None
        spec = importlib.util.spec_from_file_location(
            "greenroute_config_under_test",
            ROOT / "config" / "config.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"dotenv": dotenv_module}), patch.dict(
            os.environ, environment, clear=True
        ):
            spec.loader.exec_module(module)
        return module

    def test_production_requires_configured_secret_and_accepts_private_value(self):
        missing_secret_module = self.load_config_module({"FLASK_ENV": "production"})
        with self.assertRaisesRegex(ValueError, "SECRET_KEY must be configured"):
            missing_secret_module.get_config("production")

        configured_secret = os.urandom(32).hex()
        configured_secret_module = self.load_config_module({
            "FLASK_ENV": "production",
            "SECRET_KEY": configured_secret,
        })
        production = configured_secret_module.get_config("production")
        self.assertEqual(production.SECRET_KEY, configured_secret)

    def test_source_has_no_predictable_app_secret_fallback(self):
        config_source = (ROOT / "config" / "config.py").read_text(encoding="utf-8")
        app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("dev-key-change-in-production", config_source)
        self.assertNotIn("dev-key-change-in-production", app_source)
        self.assertIn("app.config['SECRET_KEY'] = os.urandom(32)", app_source)


if __name__ == "__main__":
    unittest.main()
