from datetime import datetime

from .db_transaction import transaction


def save_telemetry(connection, payload: dict, json_adapter):
    with transaction(connection) as cursor:
        cursor.execute(
            """
            INSERT INTO vehicle_telemetry (
                truck_id, gps_latitude, gps_longitude, gps_accuracy,
                speed_kmh, heading_degrees, harsh_braking_events,
                harsh_acceleration_events, idle_duration_min,
                fuel_consumption_liters_per_km, engine_temp_celsius,
                engine_diagnostics, driver_id, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.get("truck_id"),
                payload.get("gps_latitude"),
                payload.get("gps_longitude"),
                payload.get("gps_accuracy"),
                payload.get("speed_kmh"),
                payload.get("heading_degrees"),
                payload.get("harsh_braking_events", 0),
                payload.get("harsh_acceleration_events", 0),
                payload.get("idle_duration_min", 0),
                payload.get("fuel_consumption_liters_per_km"),
                payload.get("engine_temp_celsius"),
                json_adapter(payload.get("engine_diagnostics", {})),
                payload.get("driver_id"),
                payload.get("recorded_at", datetime.now()),
            ),
        )

        bin_id = payload.get("bin_id")
        fill_level = payload.get("fill_level")
        if not bin_id or fill_level is None:
            return

        status = payload.get("status")
        if status is None:
            cursor.execute(
                "UPDATE bins SET fill_level = %s, last_updated = %s WHERE id = %s",
                (fill_level, datetime.now(), bin_id),
            )
        else:
            cursor.execute(
                "UPDATE bins SET fill_level = %s, status = %s, last_updated = %s WHERE id = %s",
                (fill_level, status, datetime.now(), bin_id),
            )

        cursor.execute(
            "INSERT INTO fill_history (bin_id, fill_level, recorded_at) VALUES (%s, %s, %s)",
            (bin_id, fill_level, datetime.now()),
        )

        if fill_level >= 90:
            cursor.execute(
                "INSERT INTO alerts (bin_id, alert_type, severity, message) VALUES (%s, %s, %s, %s)",
                (bin_id, "overflow", "high", f"Bin fill level is critically high at {fill_level}%"),
            )
        if status == 2:
            cursor.execute(
                "INSERT INTO alerts (bin_id, alert_type, severity, message) VALUES (%s, %s, %s, %s)",
                (bin_id, "malfunction", "critical", "Bin sensor reports malfunction"),
            )
