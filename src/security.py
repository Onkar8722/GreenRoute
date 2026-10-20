from hmac import compare_digest

from flask import current_app, jsonify, request


WRITE_PROTECTED_PATHS = frozenset({
    "/api/bins_upload",
    "/api/fleet/schedule",
    "/api/fleet/track",
    "/api/scenarios",
    "/api/telemetry",
    "/api/telemetry/",
    "/api/simulation/start",
    "/api/simulation/stop",
})


def protect_write_endpoints():
    """Require a server-configured bearer token before accepting writes."""
    if request.method != "POST" or request.path not in WRITE_PROTECTED_PATHS:
        return None

    expected_token = current_app.config.get("WRITE_API_TOKEN")
    if not expected_token:
        return jsonify({"error": "Write API is disabled: WRITE_API_TOKEN is not configured"}), 503

    authorization = request.headers.get("Authorization", "")
    scheme, separator, provided_token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not provided_token
        or not compare_digest(provided_token, expected_token)
    ):
        return jsonify({"error": "A valid bearer token is required"}), 401
    return None
