from __future__ import annotations

import json
import os
import re
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

DEFAULT_GAME_FACTIONS = [
    {"name": "Politie Romana", "level_req": 1, "max_members": 50, "apps_open": True},
    {"name": "Politia Locala", "level_req": 10, "max_members": 30, "apps_open": False},
    {"name": "Jandarmeria", "level_req": 20, "max_members": 40, "apps_open": False},
    {"name": "SMURD / Medici", "level_req": 7, "max_members": 30, "apps_open": False},
    {"name": "School Instructors", "level_req": 12, "max_members": 20, "apps_open": False},
    {"name": "Mafia Sandwich", "level_req": 5, "max_members": 25, "apps_open": False},
    {"name": "Clanul Sportivilor", "level_req": 10, "max_members": 30, "apps_open": False},
    {"name": "Hitman Agency", "level_req": 25, "max_members": 15, "apps_open": False},
]

_FACTION_CONFIG_CACHE = {"ts": 0, "data": []}


def _normalize_status(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("pending", "in_review"):
        return "pending"
    if raw in ("accepted", "invite", "invited"):
        return "invited"
    if raw in ("rejected", "reject"):
        return "rejected"
    if raw in ("declined", "archived_declined"):
        return "archived_declined"
    if raw in ("archived", "archive", "archived_rejected"):
        return "archived_rejected"
    if raw in ("joined",):
        return "joined"
    return raw or "pending"


def _extract_block(text: str, opening_brace_index: int):
    if opening_brace_index < 0 or opening_brace_index >= len(text) or text[opening_brace_index] != "{":
        return "", -1
    depth = 0
    idx = opening_brace_index
    while idx < len(text):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening_brace_index + 1 : idx], idx
        idx += 1
    return "", -1


def _parse_game_factions():
    now = int(time.time())
    if _FACTION_CONFIG_CACHE["data"] and now - _FACTION_CONFIG_CACHE["ts"] < 30:
        return _FACTION_CONFIG_CACHE["data"]

    base_dir = os.path.dirname(os.path.dirname(__file__))
    candidates = [
        os.path.join(base_dir, "scripts", "ReplicatedStorage", "SharedConfigs", "FactionConfig.luau"),
        os.path.join(base_dir, "src", "ReplicatedStorage", "SharedConfigs", "FactionConfig.luau"),
    ]

    text = ""
    for cfg_path in candidates:
        try:
            with open(cfg_path, "r", encoding="utf-8", errors="ignore") as fp:
                text = fp.read()
            if text:
                break
        except Exception:
            continue
    if not text:
        return list(DEFAULT_GAME_FACTIONS)

    table_match = re.search(r"FactionConfig\.Factions\s*=\s*\{", text)
    if not table_match:
        return list(DEFAULT_GAME_FACTIONS)

    table_open = text.find("{", table_match.start())
    table_content, table_end = _extract_block(text, table_open)
    if table_end < 0:
        return list(DEFAULT_GAME_FACTIONS)

    rows = []
    cursor = 0
    while cursor < len(table_content):
        entry_match = re.search(r'\["([^"]+)"\]\s*=\s*\{', table_content[cursor:])
        if not entry_match:
            break

        faction_name = entry_match.group(1).strip()
        entry_global_start = cursor + entry_match.start()
        block_open = cursor + entry_match.end() - 1
        block_content, block_end = _extract_block(table_content, block_open)
        if block_end < 0:
            break

        level_match = re.search(r"LevelReq\s*=\s*(\d+)", block_content)
        max_match = re.search(r"MaxMembers\s*=\s*(\d+)", block_content)
        apps_match = re.search(r"AppsOpen\s*=\s*(true|false)", block_content, flags=re.IGNORECASE)

        rows.append(
            {
                "name": faction_name,
                "level_req": int(level_match.group(1)) if level_match else 1,
                "max_members": int(max_match.group(1)) if max_match else 0,
                "apps_open": bool(apps_match and apps_match.group(1).lower() == "true"),
            }
        )

        cursor = block_end + 1
        if cursor <= entry_global_start:
            break

    if not rows:
        rows = list(DEFAULT_GAME_FACTIONS)

    _FACTION_CONFIG_CACHE["ts"] = now
    _FACTION_CONFIG_CACHE["data"] = rows
    return rows


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_path(value: str) -> str:
    return quote((value or "").strip(), safe="")


def _firebase_patch(path: str, payload: dict, auth_token: str | None = None):
    return firebase_write(path, payload, "PATCH", auth_token=auth_token)


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


def _sanitize_letters_spaces(value: str, max_len: int = 15) -> str:
    clean = re.sub(r"[^A-Za-z\s]", "", str(value or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_len]


def _count_real_chars(value: str) -> int:
    return len(re.sub(r"\s+", "", str(value or "")))


def _looks_like_spam(value: str) -> bool:
    text = str(value or "")
    return bool(re.search(r"(.)\1{5,}", text))


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "open"):
            return True
        if text in ("false", "0", "no", "closed"):
            return False
    return default


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

    ok, site_user_raw, err = firebase_get(f"site_users/{_safe_path(uid)}", auth_token=id_token)
    if not ok:
        return False, None, err
    site_user = _as_dict(site_user_raw)

    username = str(site_user.get("robloxUsername", "")).strip()
    if not username:
        return False, None, "Userul nu are robloxUsername in site_users."

    return True, {"uid": uid, "username": username, "id_token": id_token}, ""


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


def _is_leader(users: dict, username: str, faction: str, auth_token: str | None = None):
    if not faction or faction == "Civil":
        return False

    me = _as_dict(users.get(username))
    if parse_int(me.get("rank", 0), 0) >= 5:
        return True

    ok, raw, _ = firebase_get(
        f"panel/faction_leaders/{_safe_path(faction)}/{_safe_path(username)}",
        auth_token=auth_token,
    )
    return ok and bool(raw)


def _available_factions(users: dict):
    cfg = _parse_game_factions()
    if cfg:
        return [row["name"] for row in cfg]

    found = set()
    for _, data in users.items():
        faction = str(_as_dict(data).get("factiune", "Civil")).strip()
        if faction and faction != "Civil":
            found.add(faction)
    return sorted(found)


def _load_faction_runtime_state(game_factions: list[dict], auth_token: str | None = None):
    ok, raw, _ = firebase_get("panel/faction_state", auth_token=auth_token)
    state = _as_dict(raw) if ok else {}
    stored_open = _as_dict(state.get("apps_open"))
    stored_sessions = _as_dict(state.get("sessions"))

    apps_open = {}
    sessions = {}
    for row in game_factions:
        name = row["name"]
        apps_open[name] = _as_bool(stored_open[name], False) if name in stored_open else _as_bool(row.get("apps_open", False), False)
        sessions[name] = parse_int(stored_sessions.get(name, 0), 0)

    return apps_open, sessions


def _save_faction_runtime_state(apps_open: dict, sessions: dict, auth_token: str | None = None):
    payload = {"apps_open": apps_open, "sessions": sessions}
    return firebase_write("panel/faction_state", payload, "PUT", auth_token=auth_token)


def _handle_list_hub(identity: dict, users: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profilul tau Roblox nu exista in users."}

    game_factions_base = _parse_game_factions()
    apps_open_map, session_map = _load_faction_runtime_state(game_factions_base, auth_token=auth_token)
    game_factions = []
    for row in game_factions_base:
        item = dict(row)
        item["apps_open"] = _as_bool(apps_open_map.get(row["name"]), _as_bool(row.get("apps_open", False), False))
        item["session_id"] = parse_int(session_map.get(row["name"], 0), 0)
        game_factions.append(item)

    available = [row["name"] for row in game_factions] if game_factions else _available_factions(users)
    open_factions = [row["name"] for row in game_factions if row.get("apps_open")] if game_factions else []

    all_apps = _flatten_apps(firebase_get("panel/faction_applications", auth_token=auth_token)[1])
    my_apps = []
    for app in all_apps:
        if str(app.get("applicant", "")) != username:
            continue
        row = dict(app)
        row["status"] = _normalize_status(row.get("status", "pending"))
        my_apps.append(row)
    my_apps = my_apps[:60]

    my_faction = str(profile.get("factiune", "Civil")) or "Civil"
    is_leader = _is_leader(users, username, my_faction, auth_token=auth_token)

    leader_pending = []
    leader_members = []
    if is_leader:
        leader_pending = [
            app
            for app in all_apps
            if _normalize_status(app.get("status", "pending")) == "pending"
            and str(app.get("faction", "")) == my_faction
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

    all_purchases = _as_dict(firebase_get("panel/shop_purchases", auth_token=auth_token)[1])
    history = []
    for _, row in all_purchases.items():
        if not isinstance(row, dict):
            continue
        if str(row.get("buyer", "")) != username:
            continue
        history.append(row)
    history.sort(key=lambda row: row.get("ts", 0), reverse=True)
    my_notifications = 0
    for app in my_apps:
        st = _normalize_status(app.get("status", "pending"))
        if st in ("invited", "rejected"):
            my_notifications += 1

    return {
        "ok": True,
        "me": username,
        "my_faction": my_faction,
        "my_faction_apps_open": _as_bool(apps_open_map.get(my_faction, False), False),
        "my_notifications": my_notifications,
        "is_leader": is_leader,
        "my_pp": parse_int(profile.get("premium_points", 0), 0),
        "available_factions": available,
        "open_factions": open_factions,
        "game_factions": game_factions,
        "my_applications": my_apps,
        "leader_pending_apps": leader_pending,
        "leader_members": leader_members,
        "shop_history": history[:40],
    }


def _handle_submit_application(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    faction = str(body.get("faction", "")).strip()
    message = str(body.get("message", "")).strip()
    form = _as_dict(body.get("form"))
    if not faction:
        return {"ok": False, "msg": "Faction este obligatorie."}

    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profilul tau Roblox nu exista in users."}
    if str(profile.get("factiune", "Civil")) != "Civil":
        return {"ok": False, "msg": "Esti deja intr-o factiune."}

    game_factions = _parse_game_factions()
    by_name = {row["name"]: row for row in game_factions}
    if faction not in by_name:
        return {"ok": False, "msg": "Factiunea selectata nu exista."}

    apps_open_map, sessions_map = _load_faction_runtime_state(game_factions, auth_token=auth_token)
    if not _as_bool(apps_open_map.get(faction), _as_bool(by_name[faction].get("apps_open", False), False)):
        return {"ok": False, "msg": "Aplicatiile sunt inchise la aceasta factiune."}

    my_level = parse_int(profile.get("level", 1), 1)
    required_level = parse_int(by_name[faction].get("level_req", 1), 1)
    if my_level < required_level:
        return {
            "ok": False,
            "msg": f"Nivel prea mic. Ai Level {my_level}, minim {required_level}.",
        }

    current_session = parse_int(sessions_map.get(faction, 0), 0)

    all_apps = _flatten_apps(firebase_get("panel/faction_applications", auth_token=auth_token)[1])
    for app in all_apps:
        if str(app.get("applicant", "")) != username or str(app.get("faction", "")) != faction:
            continue

        status = _normalize_status(app.get("status", "pending"))
        if status in ("pending", "invited"):
            return {"ok": False, "msg": "Ai deja o aplicatie activa la aceasta factiune."}

        app_session = parse_int(app.get("session_id", 0), 0)
        if status in ("rejected", "archived_rejected", "archived_declined") and app_session == current_session:
            return {"ok": False, "msg": "Ai fost deja respins in sesiunea curenta. Asteapta redeschiderea aplicatiilor."}

    form_name = _sanitize_letters_spaces(form.get("name", username), 15)
    form_age_raw = re.sub(r"\D+", "", str(form.get("age", "")))[:2]
    form_about = re.sub(r"\s+", " ", str(form.get("about", "")).strip())[:240]
    form_reason = str(form.get("reason", message)).strip()[:3000]
    real_chars = _count_real_chars(form_reason)

    if len(form_name) < 3:
        return {"ok": False, "msg": "Numele din aplicatie trebuie sa aiba minim 3 litere."}
    if not form_age_raw:
        return {"ok": False, "msg": "Varsta este obligatorie."}

    form_age = parse_int(form_age_raw, 0)
    if form_age < 10 or form_age > 99:
        return {"ok": False, "msg": "Varsta trebuie sa fie intre 10 si 99."}
    if len(form_about) < 20:
        return {"ok": False, "msg": "Descrierea scurta trebuie sa aiba minim 20 caractere."}
    if real_chars < 500:
        return {"ok": False, "msg": f"Aplicatia e prea scurta. Minim 500 caractere reale, acum: {real_chars}."}
    if _looks_like_spam(form_reason):
        return {"ok": False, "msg": "Aplicatia pare spam (repetitii excesive)."}

    full_message = f"SCURTA DESCRIERE: {form_about}\n\nAPLICATIE:\n{form_reason}"
    preview = form_reason[:180] + ("..." if len(form_reason) > 180 else "")

    payload = {
        "applicant": username,
        "faction": faction,
        "message": full_message,
        "preview": preview,
        "form": {
            "name": form_name,
            "age": form_age,
            "about": form_about,
            "reason": form_reason,
            "real_chars": real_chars,
        },
        "status": "pending",
        "session_id": current_session,
        "feedback": "",
        "created_at": _now_ms(),
    }

    ok, _, err = firebase_post("panel/faction_applications", payload, auth_token=auth_token)
    if not ok:
        return {"ok": False, "msg": err}

    return {"ok": True, "msg": f"Aplicatia a fost trimisa ({real_chars} caractere reale)."}


def _handle_review_application(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction, auth_token=auth_token):
        return {"ok": False, "msg": "Nu ai drepturi de leader."}

    app_id = str(body.get("appId", "")).strip()
    decision = str(body.get("decision", "")).strip().lower()
    reason = str(body.get("reason", "")).strip()[:300]
    if not app_id or decision not in ("accept", "reject"):
        return {"ok": False, "msg": "appId/decision invalid."}

    ok, app_raw, err = firebase_get(f"panel/faction_applications/{_safe_path(app_id)}", auth_token=auth_token)
    if not ok:
        return {"ok": False, "msg": err}
    app = _as_dict(app_raw)
    if not app:
        return {"ok": False, "msg": "Aplicatia nu exista."}

    app_faction = str(app.get("faction", "")).strip()
    if app_faction != my_faction:
        return {"ok": False, "msg": "Nu poti procesa aplicatii din alta factiune."}
    if _normalize_status(app.get("status", "pending")) != "pending":
        return {"ok": False, "msg": "Aplicatia nu mai este pending."}

    applicant = str(app.get("applicant", "")).strip()
    if not applicant:
        return {"ok": False, "msg": "Aplicatie invalida: applicant lipsa."}

    if decision == "reject" and len(reason) < 3:
        return {"ok": False, "msg": "Motivul de respingere trebuie sa aiba minim 3 caractere."}

    next_status = "invited" if decision == "accept" else "rejected"
    feedback = (
        "Felicitari! Ai fost acceptat. Verifica notificarile si confirma intrarea."
        if decision == "accept"
        else reason
    )

    ok_app, _, err_app = _firebase_patch(
        f"panel/faction_applications/{_safe_path(app_id)}",
        {
            "status": next_status,
            "feedback": feedback,
            "reviewed_by": username,
            "reviewed_at": _now_ms(),
        },
        auth_token=auth_token,
    )
    if not ok_app:
        return {"ok": False, "msg": err_app}

    firebase_post(
        "panel/faction_logs",
        {
            "faction": my_faction,
            "actor": username,
            "target": applicant,
            "event": "application_invited" if decision == "accept" else "application_rejected",
            "ts": _now_ms(),
        },
        auth_token=auth_token,
    )
    if decision == "accept":
        return {"ok": True, "msg": f"{applicant} a fost acceptat si a primit invite in {my_faction}."}
    return {"ok": True, "msg": f"Aplicatia lui {applicant} a fost respinsa."}


def _handle_respond_invite(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    app_id = str(body.get("appId", "")).strip()
    decision = str(body.get("decision", "")).strip().lower()
    if not app_id or decision not in ("accept", "decline", "archive"):
        return {"ok": False, "msg": "appId/decision invalid."}

    ok, app_raw, err = firebase_get(f"panel/faction_applications/{_safe_path(app_id)}", auth_token=auth_token)
    if not ok:
        return {"ok": False, "msg": err}
    app = _as_dict(app_raw)
    if not app:
        return {"ok": False, "msg": "Aplicatia nu exista."}

    applicant = str(app.get("applicant", "")).strip()
    if applicant != username:
        return {"ok": False, "msg": "Nu poti modifica aplicatia altui jucator."}

    status = _normalize_status(app.get("status", "pending"))
    faction = str(app.get("faction", "")).strip()
    if not faction:
        return {"ok": False, "msg": "Aplicatie invalida: factiune lipsa."}

    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profil user lipsa."}

    if decision == "accept":
        if status != "invited":
            return {"ok": False, "msg": "Aplicatia nu este in status invite."}
        if str(profile.get("factiune", "Civil")) != "Civil":
            return {"ok": False, "msg": "Esti deja intr-o factiune."}

        ok_user, _, err_user = _firebase_patch(
            f"users/{_safe_path(username)}",
            {"factiune": faction, "rank": 1, "faction_joined_at": _now_ms()},
            auth_token=auth_token,
        )
        if not ok_user:
            return {"ok": False, "msg": err_user}

        _, sync_note = _send_roblox_command(username, "SetFaction", username, faction, "1")

        ok_app, _, err_app = _firebase_patch(
            f"panel/faction_applications/{_safe_path(app_id)}",
            {"status": "joined", "responded_at": _now_ms()},
            auth_token=auth_token,
        )
        if not ok_app:
            return {"ok": False, "msg": err_app}

        firebase_post(
            "panel/faction_logs",
            {
                "faction": faction,
                "actor": username,
                "target": username,
                "event": "invite_accepted",
                "ts": _now_ms(),
            },
            auth_token=auth_token,
        )
        return {"ok": True, "msg": f"Ai intrat in {faction}.", "sync_note": sync_note}

    if decision == "decline":
        if status != "invited":
            return {"ok": False, "msg": "Aplicatia nu este in status invite."}
        ok_app, _, err_app = _firebase_patch(
            f"panel/faction_applications/{_safe_path(app_id)}",
            {"status": "archived_declined", "responded_at": _now_ms()},
            auth_token=auth_token,
        )
        if not ok_app:
            return {"ok": False, "msg": err_app}
        return {"ok": True, "msg": "Ai refuzat invitatia."}

    if status != "rejected":
        return {"ok": False, "msg": "Doar aplicatiile respinse pot fi arhivate."}

    ok_app, _, err_app = _firebase_patch(
        f"panel/faction_applications/{_safe_path(app_id)}",
        {"status": "archived_rejected", "responded_at": _now_ms()},
        auth_token=auth_token,
    )
    if not ok_app:
        return {"ok": False, "msg": err_app}
    return {"ok": True, "msg": "Aplicatia respinsa a fost mutata in istoric."}


def _handle_toggle_apps(identity: dict, users: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction, auth_token=auth_token):
        return {"ok": False, "msg": "Nu ai drepturi de leader."}

    game_factions = _parse_game_factions()
    by_name = {row["name"]: row for row in game_factions}
    if my_faction not in by_name:
        return {"ok": False, "msg": "Factiunea ta nu exista in configuratia jocului."}

    apps_open, sessions = _load_faction_runtime_state(game_factions, auth_token=auth_token)
    current = _as_bool(apps_open.get(my_faction), _as_bool(by_name[my_faction].get("apps_open", False), False))
    next_state = not current
    apps_open[my_faction] = next_state
    if next_state:
        sessions[my_faction] = int(time.time())

    ok_save, _, save_err = _save_faction_runtime_state(apps_open, sessions, auth_token=auth_token)
    if not ok_save:
        return {"ok": False, "msg": save_err}

    state_text = "DESCHISE" if next_state else "INCHISE"
    return {"ok": True, "apps_open": next_state, "msg": f"Aplicatiile pentru {my_faction} sunt acum {state_text}."}


def _handle_add_leader(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction, auth_token=auth_token):
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
        auth_token=auth_token,
    )
    if not ok_user:
        return {"ok": False, "msg": err_user}

    ok_flag, _, err_flag = firebase_write(
        f"panel/faction_leaders/{_safe_path(my_faction)}/{_safe_path(target)}",
        {"by": username, "ts": _now_ms()},
        "PUT",
        auth_token=auth_token,
    )
    if not ok_flag:
        return {"ok": False, "msg": err_flag}

    _send_roblox_command(username, "SetFaction", target, my_faction, "5")
    return {"ok": True, "msg": f"{target} este acum leader in {my_faction}."}


def _handle_warn_member(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    me = _as_dict(users.get(username))
    my_faction = str(me.get("factiune", "Civil")) or "Civil"
    if not _is_leader(users, username, my_faction, auth_token=auth_token):
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
        auth_token=auth_token,
    )
    if not ok_warn:
        return {"ok": False, "msg": err_warn}

    next_total = parse_int(target_profile.get("faction_warns_total", 0), 0) + 1
    ok_user, _, err_user = _firebase_patch(
        f"users/{_safe_path(target)}",
        {"faction_warns_total": next_total},
        auth_token=auth_token,
    )
    if not ok_user:
        return {"ok": False, "msg": err_user}

    return {"ok": True, "msg": f"Warn aplicat lui {target}. Total: {next_total}/3."}


def _handle_buy_shop_item(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
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

    ok_user, _, err_user = _firebase_patch(f"users/{_safe_path(username)}", patch, auth_token=auth_token)
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
        auth_token=auth_token,
    )

    return {
        "ok": True,
        "msg": f"Ai cumparat {item['name']} cu {item['cost']} PP.",
        "new_pp": current_pp - item["cost"],
    }


def _handle_set_spawn_preference(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profil user lipsa."}

    raw_preference = str(body.get("preference", "")).strip().lower()
    if raw_preference in ("house", "casa"):
        preference = "House"
    elif raw_preference in ("spawn",):
        preference = "Spawn"
    else:
        return {"ok": False, "msg": "Preferinta invalida. Alege Spawn sau House."}

    owned_house = parse_int(profile.get("casa_detinuta", 0), 0)
    rented_house = parse_int(profile.get("casa_inchiriata", 0), 0)
    if preference == "House" and owned_house <= 0 and rented_house <= 0:
        return {"ok": False, "msg": "Nu ai casa detinuta sau chirie activa pentru spawn House."}

    ok_sync, sync_note = _send_roblox_command(username, "SetSpawnPref", username, preference, "Panel Properties")
    if not ok_sync:
        return {"ok": False, "msg": sync_note}

    patch = {
        "spawn_preferat": preference,
        "spawn_actual": preference,
        "spawn_updated_at": _now_ms(),
    }
    ok_user, _, err_user = _firebase_patch(
        f"users/{_safe_path(username)}",
        patch,
        auth_token=auth_token,
    )
    if not ok_user:
        return {"ok": False, "msg": err_user}

    return {
        "ok": True,
        "msg": f"Spawn preference a fost schimbat pe {preference}.",
        "preference": preference,
        "sync_note": sync_note,
    }


def _handle_property_control(identity: dict, users: dict, body: dict):
    username = identity["username"]
    auth_token = identity.get("id_token", "")
    profile = _as_dict(users.get(username))
    if not profile:
        return {"ok": False, "msg": "Profil user lipsa."}

    owned_house = parse_int(profile.get("casa_detinuta", 0), 0)
    if owned_house <= 0:
        return {"ok": False, "msg": "Nu ai o casa detinuta pe cont."}

    action = str(body.get("propertyAction", "")).strip().lower()
    if action not in ("toggle_rentable", "set_rent_price", "evict_all"):
        return {"ok": False, "msg": "Actiune proprietate invalida."}

    current_rentable = _as_bool(profile.get("casa_detinuta_chirie_activata"), False)
    current_price = parse_int(profile.get("casa_detinuta_pret_chirie", 0), 0)
    current_tenants = parse_int(profile.get("casa_detinuta_chiriasi", 0), 0)

    patch = {}
    if action == "toggle_rentable":
        next_state = not current_rentable
        ok_sync, sync_note = _send_roblox_command(
            username,
            "SetHouseRentable",
            username,
            "true" if next_state else "false",
            "Panel Properties",
        )
        if not ok_sync:
            return {"ok": False, "msg": sync_note}
        patch["casa_detinuta_chirie_activata"] = next_state
        ok_user, _, err_user = _firebase_patch(f"users/{_safe_path(username)}", patch, auth_token=auth_token)
        if not ok_user:
            return {"ok": False, "msg": err_user}
        return {
            "ok": True,
            "msg": "Chiria a fost activata." if next_state else "Chiria a fost dezactivata.",
            "rentable": next_state,
            "sync_note": sync_note,
        }

    if action == "set_rent_price":
        price = parse_int(body.get("price", 0), 0)
        if price < 0:
            return {"ok": False, "msg": "Pretul chiriei nu poate fi negativ."}
        ok_sync, sync_note = _send_roblox_command(
            username,
            "SetHouseRentPrice",
            username,
            str(price),
            "Panel Properties",
        )
        if not ok_sync:
            return {"ok": False, "msg": sync_note}
        patch["casa_detinuta_pret_chirie"] = price
        ok_user, _, err_user = _firebase_patch(f"users/{_safe_path(username)}", patch, auth_token=auth_token)
        if not ok_user:
            return {"ok": False, "msg": err_user}
        return {
            "ok": True,
            "msg": f"Pretul chiriei a fost setat la ${price}.",
            "price": price,
            "sync_note": sync_note,
        }

    ok_sync, sync_note = _send_roblox_command(
        username,
        "EvictAllTenants",
        username,
        "",
        "Panel Properties",
    )
    if not ok_sync:
        return {"ok": False, "msg": sync_note}
    patch["casa_detinuta_chiriasi"] = 0
    patch["casa_detinuta_nume_chiriasi"] = []
    ok_user, _, err_user = _firebase_patch(f"users/{_safe_path(username)}", patch, auth_token=auth_token)
    if not ok_user:
        return {"ok": False, "msg": err_user}
    return {
        "ok": True,
        "msg": f"Au fost evacuati {current_tenants} chiriasi.",
        "tenants": 0,
        "sync_note": sync_note,
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

        ok_users, users_raw, users_err = firebase_get("users", auth_token=id_token)
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

        if action == "respond_invite":
            payload = _handle_respond_invite(identity, users, body)
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

        if action == "toggle_apps":
            payload = _handle_toggle_apps(identity, users)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "set_spawn_preference":
            payload = _handle_set_spawn_preference(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        if action == "property_control":
            payload = _handle_property_control(identity, users, body)
            send_json(self, 200 if payload.get("ok") else 400, payload)
            return

        send_json(self, 400, {"ok": False, "msg": "Actiune necunoscuta."})
