from http.server import BaseHTTPRequestHandler

from api._common import log_event, read_json, read_logs, send_json, validate_admin


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def do_POST(self):
        body, err = read_json(self)
        if err:
            send_json(self, 400, {"ok": False, "msg": err})
            return

        ok, auth_err = validate_admin(body)
        if not ok:
            send_json(self, 401, {"ok": False, "msg": auth_err})
            return

        action = str(body.get("action", "list")).strip().lower()

        if action == "add":
            admin = str(body.get("adminName", "WebPanel"))
            event_type = str(body.get("event_type", "Manual"))
            target = str(body.get("target", "-"))
            status = str(body.get("status", "INFO"))
            message = str(body.get("message", ""))
            payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}

            log_event(admin, event_type, target, status, message, payload)
            send_json(self, 200, {"ok": True, "msg": "log adaugat"})
            return

        limit = body.get("limit", 120)
        try:
            limit = int(limit)
        except Exception:
            limit = 120
        limit = max(1, min(limit, 300))

        logs_ok, logs, logs_err = read_logs(limit=limit)
        if not logs_ok:
            send_json(self, 502, {"ok": False, "msg": logs_err})
            return

        send_json(self, 200, {"ok": True, "count": len(logs), "logs": logs})
