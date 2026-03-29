from http.server import BaseHTTPRequestHandler

from api._common import as_users_map, firebase_get, read_json, send_json, validate_admin, vehicles_list


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

        model = str(body.get("model", "")).strip()
        owner = str(body.get("owner", "")).strip()
        limit = body.get("limit", 120)

        try:
            limit = int(limit)
        except Exception:
            limit = 120
        limit = max(1, min(limit, 500))

        all_ok, all_users_raw, all_err = firebase_get("users")
        if not all_ok:
            send_json(self, 502, {"ok": False, "msg": all_err})
            return

        users = as_users_map(all_users_raw)
        vehicles = vehicles_list(users, model, owner, limit)

        send_json(self, 200, {"ok": True, "count": len(vehicles), "vehicles": vehicles})
