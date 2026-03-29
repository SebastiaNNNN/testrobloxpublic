from http.server import BaseHTTPRequestHandler

from api._common import (
    as_users_map,
    find_case_insensitive_key,
    firebase_get,
    player_summary,
    read_json,
    safe_username_path,
    search_users,
    send_json,
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

        username = str(body.get("username", "")).strip()
        if not username:
            send_json(self, 400, {"ok": False, "msg": "username este obligatoriu"})
            return

        exact_ok, exact_data, exact_err = firebase_get(f"users/{safe_username_path(username)}")
        if not exact_ok:
            send_json(self, 502, {"ok": False, "msg": exact_err})
            return

        if isinstance(exact_data, dict):
            send_json(
                self,
                200,
                {
                    "ok": True,
                    "username": username,
                    "player": player_summary(username, exact_data),
                },
            )
            return

        all_ok, all_users_raw, all_err = firebase_get("users")
        if not all_ok:
            send_json(self, 502, {"ok": False, "msg": all_err})
            return

        all_users = as_users_map(all_users_raw)
        if not all_users:
            send_json(self, 404, {"ok": False, "msg": "Nu exista jucatori in Firebase"})
            return

        found_key = find_case_insensitive_key(all_users.keys(), username)
        if found_key and isinstance(all_users.get(found_key), dict):
            send_json(
                self,
                200,
                {
                    "ok": True,
                    "username": found_key,
                    "player": player_summary(found_key, all_users[found_key]),
                },
            )
            return

        suggestions = search_users(all_users, username, 8)
        send_json(
            self,
            404,
            {
                "ok": False,
                "msg": "Jucatorul nu a fost gasit",
                "suggestions": suggestions,
            },
        )
