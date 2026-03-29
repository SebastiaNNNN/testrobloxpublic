from http.server import BaseHTTPRequestHandler

from api._common import (
    as_users_map,
    faction_counts,
    firebase_get,
    parse_int,
    read_json,
    send_json,
    top_richest,
    validate_admin,
)


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

        total_houses = 0
        total_vehicles = 0
        admins_count = 0

        for _, payload in users.items():
            if parse_int(payload.get("casa_detinuta", 0), 0) > 0:
                total_houses += 1

            cars = payload.get("masini")
            if isinstance(cars, list):
                total_vehicles += len(cars)

            if parse_int(payload.get("admin", 0), 0) > 0:
                admins_count += 1

        factions = faction_counts(users)
        richest = top_richest(users, 8)

        send_json(
            self,
            200,
            {
                "ok": True,
                "totals": {
                    "players": len(users),
                    "factions": len(factions),
                    "houses": total_houses,
                    "vehicles": total_vehicles,
                    "admins": admins_count,
                },
                "top_factions": factions[:8],
                "richest": richest,
            },
        )
