"""Utility helpers shared by Vercel Python API endpoints."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
DATABASE_URL = os.environ.get(
    "FIREBASE_DATABASE_URL",
    "https://projsmprobl-default-rtdb.europe-west1.firebasedatabase.app/",
)
FIREBASE_DB_SECRET = os.environ.get("FIREBASE_DB_SECRET", "")
PANEL_ORIGIN = os.environ.get("PANEL_ORIGIN", "*")


# ---------------------------------------------------------------------------
# HTTP / JSON helpers
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return DATABASE_URL.rstrip("/")


def _with_auth(url: str) -> str:
    if not FIREBASE_DB_SECRET:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}auth={FIREBASE_DB_SECRET}"


def firebase_url(path: str) -> str:
    cleaned = path.strip("/")
    return _with_auth(f"{_base_url()}/{cleaned}.json")


def read_json(handler) -> Tuple[Dict[str, Any], Optional[str]]:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        return {}, "Content-Length invalid"

    if length <= 0:
        return {}, None

    try:
        raw = handler.rfile.read(length)
        return json.loads(raw), None
    except Exception:
        return {}, "JSON invalid"


def cors(handler) -> None:
    handler.send_header("Access-Control-Allow-Origin", PANEL_ORIGIN)
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def send_json(handler, status: int, payload: Dict[str, Any]) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    cors(handler)
    handler.end_headers()
    handler.wfile.write(json.dumps(payload).encode("utf-8"))


def validate_admin(body: Dict[str, Any]) -> Tuple[bool, str]:
    if not ADMIN_KEY:
        return False, "ADMIN_KEY lipseste in Vercel env"
    if body.get("adminKey") != ADMIN_KEY:
        return False, "Cheie admin invalida"
    return True, ""


# ---------------------------------------------------------------------------
# Firebase helpers
# ---------------------------------------------------------------------------

def firebase_get(path: str) -> Tuple[bool, Any, str]:
    try:
        response = requests.get(firebase_url(path), timeout=12)
    except Exception as exc:
        return False, None, f"Conexiune Firebase esuata: {exc}"

    if response.status_code != 200:
        text = response.text[:300]
        return False, None, f"Firebase HTTP {response.status_code}: {text}"

    try:
        return True, response.json(), ""
    except Exception:
        return False, None, "Raspuns Firebase invalid (nu e JSON)"


def firebase_write(path: str, payload: Dict[str, Any], method: str) -> Tuple[bool, Any, str]:
    try:
        response = requests.request(
            method.upper(),
            firebase_url(path),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=12,
        )
    except Exception as exc:
        return False, None, f"Conexiune Firebase esuata: {exc}"

    if response.status_code not in (200, 201):
        return False, None, f"Firebase write HTTP {response.status_code}: {response.text[:250]}"

    try:
        data = response.json() if response.text else {}
    except Exception:
        data = {}
    return True, data, ""


def firebase_post(path: str, payload: Dict[str, Any]) -> Tuple[bool, Any, str]:
    return firebase_write(path, payload, "POST")


def firebase_put(path: str, payload: Dict[str, Any]) -> Tuple[bool, Any, str]:
    return firebase_write(path, payload, "PUT")


# ---------------------------------------------------------------------------
# Shared parsing
# ---------------------------------------------------------------------------

def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_query(value: str) -> str:
    return (value or "").strip().lower()


def safe_username_path(username: str) -> str:
    # Roblox usernames are URL-safe, but keep this defensive.
    return quote(username, safe="")


def find_case_insensitive_key(keys: Iterable[str], target: str) -> Optional[str]:
    normalized = normalize_query(target)
    for key in keys:
        if key.lower() == normalized:
            return key
    return None


def as_users_map(payload: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for username, data in payload.items():
        out[str(username)] = data if isinstance(data, dict) else {}
    return out


# ---------------------------------------------------------------------------
# Player models
# ---------------------------------------------------------------------------

def player_summary(username: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cars = data.get("masini")
    if not isinstance(cars, list):
        cars = []

    house_owned = data.get("casa_detinuta")
    if house_owned is None:
        house_owned = data.get("OwnedHouseID", 0)

    return {
        "username": username,
        "rp_name": data.get("nume_rp") or username,
        "avatar_url": data.get("avatar_url", ""),
        "last_online": data.get("last_online", "-"),
        "level": parse_int(data.get("level", 0), 0),
        "rp": parse_int(data.get("rp", 0), 0),
        "job": data.get("job_curent", "None"),
        "faction": data.get("factiune", "Civil"),
        "rank": parse_int(data.get("rank", 0), 0),
        "admin_level": parse_int(data.get("admin", 0), 0),
        "cash": parse_int(data.get("banii_cash", 0), 0),
        "bank": parse_int(data.get("banii_banca", 0), 0),
        "hours": parse_float(data.get("ore_jucate", 0), 0),
        "house_owned": parse_int(house_owned, 0),
        "garage_slots": parse_int(data.get("sloturi_garaj", 0), 0),
        "cars_count": parse_int(data.get("masini_detinute", len(cars)), len(cars)),
        "cars": cars,
    }


def compact_player(username: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cars = data.get("masini") if isinstance(data.get("masini"), list) else []
    return {
        "username": username,
        "rp_name": data.get("nume_rp") or username,
        "faction": data.get("factiune", "Civil"),
        "rank": parse_int(data.get("rank", 0), 0),
        "level": parse_int(data.get("level", 0), 0),
        "admin_level": parse_int(data.get("admin", 0), 0),
        "cash": parse_int(data.get("banii_cash", 0), 0),
        "bank": parse_int(data.get("banii_banca", 0), 0),
        "hours": parse_float(data.get("ore_jucate", 0), 0),
        "cars_count": parse_int(data.get("masini_detinute", len(cars)), len(cars)),
        "house_owned": parse_int(data.get("casa_detinuta", 0), 0),
        "last_online": data.get("last_online", "-"),
    }


def search_users(users: Dict[str, Dict[str, Any]], raw_query: str, limit: int) -> List[Dict[str, Any]]:
    query = normalize_query(raw_query)
    candidates: List[Tuple[int, str, Dict[str, Any]]] = []

    for username, data in users.items():
        uname = username.lower()
        rp_name = str(data.get("nume_rp", "")).lower()

        if query:
            if uname.startswith(query):
                score = 0
            elif query in uname:
                score = 1
            elif rp_name.startswith(query):
                score = 2
            elif query in rp_name:
                score = 3
            else:
                continue
        else:
            score = 4

        candidates.append((score, username, data))

    candidates.sort(key=lambda item: (item[0], item[1].lower()))

    result: List[Dict[str, Any]] = []
    for _, username, data in candidates[:limit]:
        result.append(compact_player(username, data if isinstance(data, dict) else {}))

    return result


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def faction_counts(users: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for _, payload in users.items():
        faction = str(payload.get("factiune", "Civil") or "Civil").strip() or "Civil"
        counts[faction] = counts.get(faction, 0) + 1

    rows = [{"name": name, "members": members} for name, members in counts.items()]
    rows.sort(key=lambda item: (-item["members"], item["name"].lower()))
    return rows


def faction_members(users: Dict[str, Dict[str, Any]], faction: str, query: str, limit: int) -> List[Dict[str, Any]]:
    target = normalize_query(faction)
    query_norm = normalize_query(query)
    result: List[Dict[str, Any]] = []

    for username, payload in users.items():
        payload_faction = str(payload.get("factiune", "Civil") or "Civil").strip()
        if normalize_query(payload_faction) != target:
            continue

        if query_norm and query_norm not in username.lower() and query_norm not in str(payload.get("nume_rp", "")).lower():
            continue

        info = compact_player(username, payload)
        result.append(info)

    result.sort(key=lambda item: (-item.get("rank", 0), -item.get("level", 0), item["username"].lower()))
    return result[:limit]


def houses_list(users: Dict[str, Dict[str, Any]], query: str, limit: int) -> List[Dict[str, Any]]:
    query_norm = normalize_query(query)
    out: List[Dict[str, Any]] = []

    for username, payload in users.items():
        house_id = parse_int(payload.get("casa_detinuta", 0), 0)
        if house_id <= 0:
            continue

        if query_norm:
            uname = username.lower()
            rp_name = str(payload.get("nume_rp", "")).lower()
            if query_norm not in uname and query_norm not in rp_name and query_norm not in str(house_id):
                continue

        out.append(
            {
                "house_id": house_id,
                "owner": username,
                "rp_name": payload.get("nume_rp") or username,
                "faction": payload.get("factiune", "Civil"),
                "level": parse_int(payload.get("level", 0), 0),
                "cash": parse_int(payload.get("banii_cash", 0), 0),
                "bank": parse_int(payload.get("banii_banca", 0), 0),
                "last_online": payload.get("last_online", "-"),
            }
        )

    out.sort(key=lambda row: (row["house_id"], row["owner"].lower()))
    return out[:limit]


def vehicles_list(users: Dict[str, Dict[str, Any]], model_query: str, owner_query: str, limit: int) -> List[Dict[str, Any]]:
    model_norm = normalize_query(model_query)
    owner_norm = normalize_query(owner_query)
    out: List[Dict[str, Any]] = []

    for username, payload in users.items():
        cars = payload.get("masini")
        if not isinstance(cars, list) or not cars:
            continue

        if owner_norm and owner_norm not in username.lower() and owner_norm not in str(payload.get("nume_rp", "")).lower():
            continue

        for car in cars:
            if not isinstance(car, dict):
                continue

            model = str(car.get("model", "") or "")
            display_name = str(car.get("nume", "") or "")
            if model_norm and model_norm not in model.lower() and model_norm not in display_name.lower():
                continue

            out.append(
                {
                    "owner": username,
                    "owner_rp": payload.get("nume_rp") or username,
                    "owner_faction": payload.get("factiune", "Civil"),
                    "model": model or "-",
                    "name": display_name or "-",
                    "status": str(car.get("status", "-") or "-"),
                    "km": parse_float(car.get("km", 0), 0),
                    "color": str(car.get("culoare", "-") or "-"),
                }
            )

    out.sort(key=lambda row: (-row["km"], row["owner"].lower(), row["model"].lower()))
    return out[:limit]


def top_richest(users: Dict[str, Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for username, payload in users.items():
        cash = parse_int(payload.get("banii_cash", 0), 0)
        bank = parse_int(payload.get("banii_banca", 0), 0)
        total = cash + bank
        rows.append(
            {
                "username": username,
                "rp_name": payload.get("nume_rp") or username,
                "faction": payload.get("factiune", "Civil"),
                "cash": cash,
                "bank": bank,
                "total": total,
            }
        )

    rows.sort(key=lambda row: (-row["total"], row["username"].lower()))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def log_event(admin: str, event_type: str, target: str, status: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
    body = {
        "admin": admin,
        "event_type": event_type,
        "target": target,
        "status": status,
        "message": message,
        "payload": payload or {},
        "ts": int(time.time()),
    }
    firebase_post("admin_panel_logs", body)


def read_logs(limit: int = 100) -> Tuple[bool, List[Dict[str, Any]], str]:
    ok, raw, err = firebase_get("admin_panel_logs")
    if not ok:
        return False, [], err

    if not isinstance(raw, dict):
        return True, [], ""

    rows: List[Dict[str, Any]] = []
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": key,
                "admin": item.get("admin", "WebPanel"),
                "event_type": item.get("event_type", "Unknown"),
                "target": item.get("target", "-"),
                "status": item.get("status", "INFO"),
                "message": item.get("message", ""),
                "payload": item.get("payload", {}),
                "ts": parse_int(item.get("ts", 0), 0),
            }
        )

    rows.sort(key=lambda row: (-row["ts"], row["id"]))
    return True, rows[:limit], ""
