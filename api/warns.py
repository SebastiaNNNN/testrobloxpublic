"""Returns admin warn count for a player username via Roblox Open Cloud DataStore."""
from __future__ import annotations
import json, os, requests
from api._common import cors_headers, read_json, json_response

ROBLOX_API_KEY = os.environ.get("ROBLOX_API_KEY", "").strip()
UNIVERSE_ID    = os.environ.get("UNIVERSE_ID", "").strip()

def _username_to_id(username: str) -> int | None:
    try:
        r = requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=8,
        )
        data = r.json()
        users = data.get("data", [])
        if users:
            return users[0]["id"]
    except Exception:
        pass
    return None


def _get_warns(user_id: int) -> int:
    try:
        url = (
            f"https://apis.roblox.com/datastores/v1/universes/{UNIVERSE_ID}"
            f"/standard-datastores/datastore/entries/entry"
            f"?datastoreName=WarnsData_V1&entryKey=Warns_{user_id}"
        )
        r = requests.get(url, headers={"x-api-key": ROBLOX_API_KEY}, timeout=8)
        if r.status_code == 200:
            val = r.json()
            return int(val) if isinstance(val, (int, float)) else 0
        return 0
    except Exception:
        return 0


def handler(request, response):
    headers = cors_headers()
    if request.method == "OPTIONS":
        response.status_code = 204
        for k, v in headers.items():
            response.headers[k] = v
        return

    body, err = read_json(request)
    if err:
        return json_response(response, {"ok": False, "msg": err}, 400, headers)

    username = (body.get("username") or "").strip()
    if not username:
        return json_response(response, {"ok": False, "msg": "username required"}, 400, headers)

    user_id = _username_to_id(username)
    if not user_id:
        return json_response(response, {"ok": True, "warns": 0}, 200, headers)

    warns = _get_warns(user_id)
    return json_response(response, {"ok": True, "warns": warns, "user_id": user_id}, 200, headers)
