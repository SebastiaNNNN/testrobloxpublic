from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote

import requests

from api._common import (
    as_users_map,
    find_case_insensitive_key,
    firebase_get,
    firebase_post,
    firebase_write,
    parse_int,
    read_json,
    send_json,
)

FIREBASE_WEB_API_KEY = os.environ.get(
    "FIREBASE_WEB_API_KEY",
    "AIzaSyAmKh9W10-wo7pXDNavKoy91JiA4YfM154",
).strip()
ROBLOX_API_KEY = os.environ.get("ROBLOX_API_KEY", "").strip()
UNIVERSE_ID = os.environ.get("UNIVERSE_ID", "").strip()

SHOP_ITEMS = {
    "cash_10k": {"name": "$10,000 Cash", "cost": 10, "kind": "cash", "amount": 10000},
    "cash_50k": {"name": "$50,000 Cash", "cost": 45, "kind": "cash", "amount": 50000},
    "rp_25": {"name": "25 RP Points", "cost": 14, "kind": "rp", "amount": 25},
    "garage_1": {"name": "+1 Slot Garaj", "cost": 28, "kind": "garage", "amount": 1},
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_path(value: str) -> str:
    return quote((value or "").strip(), safe="")


def _firebase_patch(path: str, payload: dict):
    return firebase_write(path, payload, "PATCH")


def _as_dict(payload) -> dict:
    return payload if isinstance(payload, dict) else {}


def _flatten_apps(apps_raw) -> list[dict]:
    apps = []
    for app_id, app_data in _as_dict(apps_raw).items():
        if not isinstance(app_data, dict):
            continue
        row = dict(app_data)
        row["id"] = str(app_id)
        apps.append(row)
    apps.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return apps


def _verify_identity(id_token: str):
    if not id_token:
        return False, None, "Lipseste idToken."
    if not FIREBASE_WEB_API_KEY:
        return False, None, "FIREBASE_WEB_API_KEY lipseste."

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={FIREBASE_WEB_API_KEY}"
    try:
        response = requests.post(url, json={"idToken": id_token}, timeout=10)
    except Exception as exc:
        return False, None, f"Eroare verificare token: {exc}"

    if response.status_code != 200:
        return False, None, "Token invalid sau expirat."

    try:
        payload = response.json()
    except Exception:
        return False, None, "Raspuns invalid la verificarea tokenului."

    users = payload.get("users") or []
    if not users:
        return False, None, "Token invalid (fara user)."

    uid = str(users[0].get("localId", "")).strip()
    if not uid:
        return False, None, "UID lipsa in token."

    ok, site_user_raw, err = firebase_get(f"site_users/{_safe_path(uid)}")
    if not ok:
        return False, None, err
    site_user = _as_dict(site_user_raw)

    username = str(site_user.get("robloxUsername", "")).strip()
    if not username:
        return False, None, "Userul nu are robloxUsername in site_users."

    return True, {"uid": uid, "username": username}, ""


def _send_roblox_command(admin_name: str, command_type: str, target: str, value: str, reason: str):
    if not ROBLOX_API_KEY or not UNIVERSE_ID:
        return False, "Sync joc indisponibil (ROBLOX_API_KEY / UNIVERSE_ID lipsesc)."

    url = f"https://apis.roblox.com/messaging-service/v1/universes/{UNIVERSE_ID}/topics/DiscordCommands"
    payload = {
        "Admin": admin_name,
        "Type": command_type,
        "Target": target,
        "Value": str(value),
        "Reason": str(reason),
    }

    try:
        response = requests.post(
            url,
            headers={"x-api-key": ROBLOX_API_KEY, "Content-Type": "application/json"},
            json={"message": json.dumps(payload)},
            timeout=10,
        )
    except Exception as exc:
        return False, f"Sync joc esuat: {exc}"

    if response.status_code == 200:
        return True, "Sync joc ok."
    return False, f"Sync joc HTTP {response.status_code}: {response.text[:180]}"


def _is_leader(users: dict, username: str, faction: str):
    if not faction or faction == "Civil":
        return False

    me = _as_dict(users.get(username))
    if parse_int(me.get("rank", 0), 0) >= 5:
        return True

    ok, raw, _ = firebase_get(f"panel/faction_leaders/{_safe_path(faction)}/{_safe_path(username)}")
    return ok and bool(raw)


def _available_factions(users: dict):
    found = set()
    for _, data in users.items():
        faction = str(_as_dict(data).get("factiune", "Civil")).strip()
        if faction and faction != "Civil":
            found.add(faction)
    return sorted(found)


def _handle_list_hub(identity: dict, users: dict):
    username = identity["username"]
    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profilul tau Roblox nu exista in users."}

    all_apps = _flatten_apps(firebase_get("panel/faction_applications")[1])
    my_apps = [app for app in all_apps if str(app.get("applicant", "")) == username][:40]

    my_faction = str(profile.get("factiune", "Civil")) or "Civil"
    is_leader = _is_leader(users, username, my_faction)

    leader_pending = []
    leader_members = []
    if is_leader:
        leader_pending = [
            app
            for app in all_apps
            if str(app.get("status", "pending")) == "pending" and str(app.get("faction", "")) == my_faction
        ][:100]

        for member_name, member_data in users.items():
            member = _as_dict(member_data)
            if str(member.get("factiune", "Civil")) != my_faction:
                continue
            leader_members.append(
                {
                    "username": member_name,
                    "rank": parse_int(member.get("rank", 0), 0),
                    "warns_total": parse_int(member.get("faction_warns_total", 0), 0),
                    "last_online": member.get("last_online", "-"),
                }
            )
        leader_members.sort(key=lambda row: (-row["rank"], row["username"].lower()))

    all_purchases = _as_dict(firebase_get("panel/shop_purchases")[1])
    history = []
    for _, row in all_purchases.items():
        if not isinstance(row, dict):
            continue
        if str(row.get("buyer", "")) != username:
            continue
        history.append(row)
    history.sort(key=lambda row: row.get("ts", 0), reverse=True)

    return {
        "ok": True,
        "me": username,
        "my_faction": my_faction,
        "is_leader": is_leader,
        "my_pp": parse_int(profile.get("premium_points", 0), 0),
        "available_factions": _available_factions(users),
        "my_applications": my_apps,
        "leader_pending_apps": leader_pending,
        "leader_members": leader_members,
        "shop_history": history[:40],
    }


def _handle_submit_application(identity: dict, users: dict, body: dict):
    username = identity["username"]
    faction = str(body.get("faction", "")).strip()
    message = str(body.get("message", "")).strip()
    if not faction:
        return {"ok": False, "msg": "Faction este obligatorie."}

    if faction not in _available_factions(users):
        return {"ok": False, "msg": "Factiunea selectata nu exista."}

    all_apps = _flatten_apps(firebase_get("panel/faction_applications")[1])
    for app in all_apps:
        if (
            str(app.get("applicant", "")) == username
            and str(app.get("faction", "")) == faction
            and str(app.get("status", "pending")) == "pending"
        ):
            return {"ok": False, "msg": "Ai deja aplicatie pending la aceasta factiune."}

    payload = {
        "applicant": username,
        "faction": faction,
        "message": message[:600],
        "status": "pending",
        "created_at": _now_ms(),
    }

    ok, _, err = firebase_post("panel/faction_applications", payload)
    if not ok:
        return {"ok": False, "msg": err}

    return {"ok": True, "msg": "Aplicatia a fost trimisa."}


def _handle_review_application(identity: dict, users: dict, body: dict):
    username = identity["username"]
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction):
        return {"ok": False, "msg": "Nu ai drepturi de leader."}

    app_id = str(body.get("appId", "")).strip()
    decision = str(body.get("decision", "")).strip().lower()
    if not app_id or decision not in ("accept", "reject"):
        return {"ok": False, "msg": "appId/decision invalid."}

    ok, app_raw, err = firebase_get(f"panel/faction_applications/{_safe_path(app_id)}")
    if not ok:
        return {"ok": False, "msg": err}
    app = _as_dict(app_raw)
    if not app:
        return {"ok": False, "msg": "Aplicatia nu exista."}

    app_faction = str(app.get("faction", "")).strip()
    if app_faction != my_faction:
        return {"ok": False, "msg": "Nu poti procesa aplicatii din alta factiune."}
    if str(app.get("status", "pending")) != "pending":
        return {"ok": False, "msg": "Aplicatia nu mai este pending."}

    applicant = str(app.get("applicant", "")).strip()
    if not applicant:
        return {"ok": False, "msg": "Aplicatie invalida: applicant lipsa."}

    sync_note = ""
    if decision == "accept":
        ok_user, _, err_user = _firebase_patch(
            f"users/{_safe_path(applicant)}",
            {"factiune": my_faction, "rank": 1, "faction_joined_at": _now_ms()},
        )
        if not ok_user:
            return {"ok": False, "msg": err_user}
        synced, note = _send_roblox_command(username, "SetFaction", applicant, my_faction, "1")
        if note:
            sync_note = note
        if not synced:
            sync_note = note

    ok_app, _, err_app = _firebase_patch(
        f"panel/faction_applications/{_safe_path(app_id)}",
        {
            "status": "accepted" if decision == "accept" else "rejected",
            "reviewed_by": username,
            "reviewed_at": _now_ms(),
        },
    )
    if not ok_app:
        return {"ok": False, "msg": err_app}

    firebase_post(
        "panel/faction_logs",
        {
            "faction": my_faction,
            "actor": username,
            "target": applicant,
            "event": f"application_{decision}",
            "ts": _now_ms(),
        },
    )
    return {"ok": True, "msg": f"Aplicatia a fost {decision}ata.", "sync_note": sync_note}


def _handle_add_leader(identity: dict, users: dict, body: dict):
    username = identity["username"]
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction):
        return {"ok": False, "msg": "Nu ai drepturi de leader."}

    raw_target = str(body.get("target", "")).strip()
    if not raw_target:
        return {"ok": False, "msg": "Target lipsa."}

    target = find_case_insensitive_key(users.keys(), raw_target) or raw_target
    if target not in users:
        return {"ok": False, "msg": "Jucatorul nu exista in baza de date."}

    ok_user, _, err_user = _firebase_patch(
        f"users/{_safe_path(target)}",
        {"factiune": my_faction, "rank": 5},
    )
    if not ok_user:
        return {"ok": False, "msg": err_user}

    ok_flag, _, err_flag = firebase_write(
        f"panel/faction_leaders/{_safe_path(my_faction)}/{_safe_path(target)}",
        {"by": username, "ts": _now_ms()},
        "PUT",
    )
    if not ok_flag:
        return {"ok": False, "msg": err_flag}

    _send_roblox_command(username, "SetFaction", target, my_faction, "5")
    return {"ok": True, "msg": f"{target} este acum leader in {my_faction}."}


def _handle_warn_member(identity: dict, users: dict, body: dict):
    username = identity["username"]
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction):
        return {"ok": False, "msg": "Nu ai drepturi de leader."}

    raw_target = str(body.get("target", "")).strip()
    reason = str(body.get("reason", "")).strip()[:300]
    if not raw_target or not reason:
        return {"ok": False, "msg": "Target si reason sunt obligatorii."}

    target = find_case_insensitive_key(users.keys(), raw_target) or raw_target
    target_profile = _as_dict(users.get(target))
    if not target_profile:
        return {"ok": False, "msg": "Target inexistent."}
    if str(target_profile.get("factiune", "Civil")) != my_faction:
        return {"ok": False, "msg": "Poti da warn doar membrilor din factiunea ta."}

    ok_warn, _, err_warn = firebase_post(
        f"panel/faction_warns/{_safe_path(target)}",
        {
            "faction": my_faction,
            "from": username,
            "reason": reason,
            "ts": _now_ms(),
        },
    )
    if not ok_warn:
        return {"ok": False, "msg": err_warn}

    next_total = parse_int(target_profile.get("faction_warns_total", 0), 0) + 1
    ok_user, _, err_user = _firebase_patch(
        f"users/{_safe_path(target)}",
        {"faction_warns_total": next_total},
    )
    if not ok_user:
        return {"ok": False, "msg": err_user}

    return {"ok": True, "msg": f"Warn aplicat lui {target}. Total: {next_total}/3."}


def _handle_buy_shop_item(identity: dict, users: dict, body: dict):
    username = identity["username"]
    item_id = str(body.get("itemId", "")).strip()
    item = SHOP_ITEMS.get(item_id)
    if not item:
        return {"ok": False, "msg": "Item invalid."}

    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profil user lipsa."}

    current_pp = parse_int(profile.get("premium_points", 0), 0)
    if current_pp < item["cost"]:
        return {"ok": False, "msg": "Nu ai destule PP."}

    patch = {"premium_points": current_pp - item["cost"]}
    if item["kind"] == "cash":
        patch["banii_cash"] = parse_int(profile.get("banii_cash", 0), 0) + int(item["amount"])
    elif item["kind"] == "rp":
        patch["rp"] = parse_int(profile.get("rp", 0), 0) + int(item["amount"])
    elif item["kind"] == "garage":
        patch["sloturi_garaj"] = parse_int(profile.get("sloturi_garaj", 0), 0) + int(item["amount"])

    ok_user, _, err_user = _firebase_patch(f"users/{_safe_path(username)}", patch)
    if not ok_user:
        return {"ok": False, "msg": err_user}

    sync_note = ""
    if item["kind"] == "cash":
        _, sync_note = _send_roblox_command(username, "Cash", username, str(item["amount"]), "PP Shop")

    firebase_post(
        "panel/shop_purchases",
        {
            "buyer": username,
            "item_id": item_id,
            "item_name": item["name"],
            "cost_pp": item["cost"],
            "status": "done",
            "sync_note": sync_note,
            "ts": _now_ms(),
        },
    )

    return {
        "ok": True,
        "msg": f"Ai cumparat {item['name']} cu {item['cost']} PP.",
        "new_pp": current_pp - item["cost"],
    }


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def do_POST(self):
        body, err = read_json(self)
        if err:
            send_json(self, 400, {"ok": False, "msg": err})
            return

        action = str(body.get("action", "")).strip()
        id_token = str(body.get("idToken", "")).strip()

        ok_identity, identity, auth_err = _verify_identity(id_token)
        if not ok_identity:
            send_json(self, 401, {"ok": False, "msg": auth_err})
            return

        ok_users, users_raw, users_err = firebase_get("users")
        if not ok_users:
            send_json(self, 502, {"ok": False, "msg": users_err})
            return
        users = as_users_map(users_raw)

        if action == "list_hub":
            payload = _handle_list_hub(identity, users)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "submit_application":
            payload = _handle_submit_application(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "review_application":
            payload = _handle_review_application(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "add_leader":
            payload = _handle_add_leader(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "warn_member":
            payload = _handle_warn_member(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "buy_shop_item":
            payload = _handle_buy_shop_item(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        send_json(self, 400, {"ok": False, "msg": "Actiune necunoscuta."})
