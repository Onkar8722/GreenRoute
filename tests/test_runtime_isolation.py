import importlib
import os
import unittest
from unittest.mock import patch

import psycopg2


class RuntimeIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._testing_environment = patch.dict(
            os.environ,
            {"FLASK_ENV": "testing", "TESTING": "true"},
        )
        cls._testing_environment.start()

        # Reload under explicit test settings even when another test imported
        # the application earlier in development mode.
        cache_module = importlib.import_module("src.cache")
        with (
            patch.object(cache_module, "get_cache") as get_cache,
            patch.object(psycopg2, "connect") as connect,
        ):
            cls.app_module = importlib.import_module("src.app")
            cls.app_module = importlib.reload(cls.app_module)

        get_cache.assert_not_called()
        connect.assert_not_called()

    @classmethod
    def tearDownClass(cls):
        cls._testing_environment.stop()

    def test_testing_database_name_and_effective_database_match(self):
        from config.config import TestingConfig, get_config

        selected = get_config("testing")
        self.assertEqual(TestingConfig.DB_NAME, "greenroute_test_db")
        self.assertEqual(TestingConfig.DB_CONFIG["database"], TestingConfig.DB_NAME)
        self.assertEqual(selected.DB_CONFIG["database"], selected.DB_NAME)
        self.assertEqual(
            self.app_module.DB_CONFIG["database"],
            selected.DB_CONFIG["database"],
        )
        self.assertNotEqual(selected.DB_CONFIG["database"], "greenroute_db")
        self.assertFalse(selected.REDIS_ENABLED)
        self.assertFalse(selected.MQTT_ENABLED)

    def test_testing_environment_overrides_development_default(self):
        from config.config import TestingConfig, get_config

        with patch.dict(
            os.environ,
            {"FLASK_ENV": "development", "TESTING": "true"},
        ):
            self.assertIs(get_config(), TestingConfig)

    def test_database_connections_are_blocked_in_test_mode(self):
        with patch.object(self.app_module.psycopg2, "connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "disabled in TESTING mode"):
                self.app_module.get_db_connection()
            with self.assertRaisesRegex(RuntimeError, "disabled in TESTING mode"):
                self.app_module.db_manager.create_connection()

        connect.assert_not_called()

    def test_create_app_skips_external_services_and_model_training(self):
        with (
            patch.object(self.app_module.db_manager, "health_check") as health_check,
            patch.object(self.app_module, "get_cache") as get_cache,
            patch.object(self.app_module, "WasteForecaster") as forecaster,
            patch.object(self.app_module, "MQTTListener") as mqtt_listener,
        ):
            created_app = self.app_module.create_app()

        self.assertIs(created_app, self.app_module.app)
        self.assertTrue(created_app.config["TESTING"])
        self.assertFalse(self.app_module.cache.available)
        self.assertIsNone(self.app_module.forecaster)
        self.assertIsNone(self.app_module.mqtt_listener)
        health_check.assert_not_called()
        get_cache.assert_not_called()
        forecaster.assert_not_called()
        mqtt_listener.assert_not_called()

    def test_collection_window_endpoint_cannot_train_or_connect(self):
        client = self.app_module.app.test_client()
        with (
            patch.object(self.app_module, "CollectionWindowPredictor") as predictor,
            patch.object(self.app_module, "get_db_connection") as get_connection,
            patch.object(self.app_module.psycopg2, "connect") as connect,
        ):
            response = client.get("/api/collection-windows")

        self.assertEqual(response.status_code, 503)
        predictor.assert_not_called()
        get_connection.assert_not_called()
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
