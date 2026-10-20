import csv
import math
from datetime import datetime
from io import StringIO

from flask import jsonify, request

from .db_transaction import transaction


MAX_BIN_UPLOAD_BYTES = 5 * 1024 * 1024
REQUIRED_COLUMNS = {"bin_id", "latitude", "longitude", "fill_level", "status"}


def parse_bins_csv(content: bytes) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be valid UTF-8") from exc

    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        raise ValueError(
            "CSV must include columns: bin_id, latitude, longitude, fill_level, status (timestamp optional)"
        )

    bins = []
    seen_ids = set()
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"CSV row {row_number} has too many columns")
        if not any(value and value.strip() for value in row.values() if isinstance(value, str)):
            continue
        if any(row.get(column) is None for column in REQUIRED_COLUMNS):
            raise ValueError(f"CSV row {row_number} is missing a required value")

        bin_id = row["bin_id"].strip()
        if not bin_id:
            raise ValueError(f"CSV row {row_number} has an empty bin_id")
        if bin_id in seen_ids:
            raise ValueError(f"CSV row {row_number} duplicates bin_id {bin_id!r}")
        seen_ids.add(bin_id)

        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            fill_level_raw = float(row["fill_level"])
            status_raw = float(row["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV row {row_number} contains a non-numeric value") from exc

        if not all(math.isfinite(value) for value in (latitude, longitude, fill_level_raw, status_raw)):
            raise ValueError(f"CSV row {row_number} contains a non-finite value")
        if not -90 <= latitude <= 90:
            raise ValueError(f"CSV row {row_number} latitude must be between -90 and 90")
        if not -180 <= longitude <= 180:
            raise ValueError(f"CSV row {row_number} longitude must be between -180 and 180")
        if not 0 <= fill_level_raw <= 100:
            raise ValueError(f"CSV row {row_number} fill_level must be between 0 and 100")
        if not status_raw.is_integer() or int(status_raw) not in (0, 1):
            raise ValueError(f"CSV row {row_number} status must be 0 or 1")

        fill_level = int(fill_level_raw)
        bins.append({
            "id": bin_id,
            "lat": latitude,
            "lng": longitude,
            "fill": fill_level,
            "status": 1 if fill_level >= 80 else 0,
        })

    if not bins:
        raise ValueError("CSV contains no data rows")
    return bins


def register_bins_upload_endpoint(app, get_db_connection):
    @app.post("/api/bins_upload")
    def api_bins_upload():
        if "file" not in request.files:
            return jsonify({"error": "missing file field 'file'"}), 400

        uploaded_file = request.files["file"]
        if not uploaded_file.filename:
            return jsonify({"error": "empty filename"}), 400

        try:
            try:
                content = uploaded_file.read()
            finally:
                uploaded_file.close()
            bins = parse_bins_csv(content)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            with transaction(get_db_connection()) as cursor:
                for bin_data in bins:
                    now = datetime.now()
                    cursor.execute(
                        """
                        INSERT INTO bins (id, latitude, longitude, fill_level, status, last_updated, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            fill_level = EXCLUDED.fill_level,
                            status = EXCLUDED.status,
                            last_updated = EXCLUDED.last_updated
                        """,
                        (
                            bin_data["id"], bin_data["lat"], bin_data["lng"],
                            bin_data["fill"], bin_data["status"], now, now,
                        ),
                    )
        except Exception:
            app.logger.exception("Bin CSV upload database operation failed")
            return jsonify({"error": "Database error while saving uploaded bins"}), 500

        return jsonify({
            "ok": True,
            "count": len(bins),
            "message": f"Saved {len(bins)} bins to database",
        })
