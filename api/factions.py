from http.server import BaseHTTPRequestHandler

from api._common import as_users_map, faction_counts, firebase_get, read_json, send_json, validate_admin


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

        all_ok, all_users_raw, all_err = firebase_get("users")
        if not all_ok:
            send_json(self, 502, {"ok": False, "msg": all_err})
            return

        users = as_users_map(all_users_raw)
        rows = faction_counts(users)

        send_json(
            self,
            200,
            {
                "ok": True,
                "total_players": len(users),
                "factions": rows,
            },
        )
