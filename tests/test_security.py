import unittest
import ast
from pathlib import Path

from flask import Flask, jsonify

from src.security import WRITE_PROTECTED_PATHS, protect_write_endpoints


EXPECTED_WRITE_PATHS = {
    "/api/bins_upload",
    "/api/fleet/schedule",
    "/api/fleet/track",
    "/api/scenarios",
    "/api/telemetry",
    "/api/telemetry/",
    "/api/simulation/start",
    "/api/simulation/stop",
}


class WriteApiProtectionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, WRITE_API_TOKEN="")
        self.app.before_request(protect_write_endpoints)
        self.called = []

        def write_stub():
            self.called.append(True)
            return jsonify({"success": True})

        for index, path in enumerate(WRITE_PROTECTED_PATHS):
            self.app.add_url_rule(
                path,
                endpoint=f"protected_write_{index}",
                view_func=write_stub,
                methods=["POST"],
            )

        self.client = self.app.test_client()

    def test_all_write_endpoints_fail_closed_without_server_token(self):
        for path in WRITE_PROTECTED_PATHS:
            with self.subTest(path=path):
                response = self.client.post(path, json={})

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json["error"],
                    "Write API is disabled: WRITE_API_TOKEN is not configured",
                )
        self.assertEqual(self.called, [])

    def test_all_operational_write_paths_are_protected_in_app(self):
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "src" / "app.py").read_text(encoding="utf-8")

        self.assertEqual(WRITE_PROTECTED_PATHS, EXPECTED_WRITE_PATHS)
        self.assertIn("app.before_request(protect_write_endpoints)", app_source)

    def test_missing_and_invalid_bearer_tokens_are_rejected(self):
        self.app.config["WRITE_API_TOKEN"] = "test-only-token"

        missing = self.client.post("/api/scenarios", json={})
        invalid = self.client.post(
            "/api/scenarios",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(self.called, [])

    def test_configured_token_allows_trusted_write(self):
        self.app.config["WRITE_API_TOKEN"] = "test-only-token"

        response = self.client.post(
            "/api/scenarios",
            json={},
            headers={"Authorization": "Bearer test-only-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["success"], True)
        self.assertEqual(self.called, [True])


class SecurityRegressionTests(unittest.TestCase):
    def test_development_debug_setting_is_not_forced_on(self):
        root = Path(__file__).resolve().parents[1]
        config = (root / "config" / "config.py").read_text(encoding="utf-8")

        self.assertIn("class DevelopmentConfig(Config):", config)
        self.assertIn("DEBUG = Config.DEBUG", config)

    def test_uploaded_bin_and_scenario_values_are_rendered_safely(self):
        root = Path(__file__).resolve().parents[1]
        scenarios = (root / "templates" / "scenarios.html").read_text(encoding="utf-8")
        dashboard = (root / "templates" / "index.html").read_text(encoding="utf-8")
        debug = (root / "templates" / "debug.html").read_text(encoding="utf-8")
        routes = (root / "templates" / "routes.html").read_text(encoding="utf-8")
        tracking = (root / "templates" / "tracking.html").read_text(encoding="utf-8")

        self.assertIn("name.textContent = scenario.name", scenarios)
        self.assertIn("document.createTextNode(scenario.name)", scenarios)
        self.assertNotIn("${scenario.name}", scenarios)
        self.assertIn("${escapeHtml(bin.id)}", dashboard)
        self.assertIn("${escapeHtml(sample.id)}", debug)
        self.assertIn("${escapeHtml(id)}", routes)
        self.assertIn("${escapeHtml(v.current_bin || 'Moving')}", tracking)
        self.assertIn("${escapeHtml(v.driver)}", tracking)
        self.assertNotIn("onclick=\"selectVehicle('${v.assignment_id}')\"", tracking)

    def test_bin_upload_does_not_truncate_existing_data(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("TRUNCATE TABLE bins CASCADE", source)
        self.assertIn("register_bins_upload_endpoint(app, get_db_connection)", source)

    def test_route_post_does_not_mutate_shared_cache(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        route_view = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "api_route"
        )

        self.assertFalse(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete_pattern"
            for node in ast.walk(route_view)
        ))


if __name__ == "__main__":
    unittest.main()
