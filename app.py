from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from threading import Thread
import requests
import json
import os

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "cheie_secreta_schimba_asta")

ROBLOX_API_KEY  = os.getenv("ROBLOX_API_KEY")
UNIVERSE_ID     = os.getenv("UNIVERSE_ID")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "admin123")   # schimba in .env

ROBLOX_MSG_URL = f"https://apis.roblox.com/messaging-service/v1/universes/{UNIVERSE_ID}/topics/DiscordCommands"

FACTIONS = [
    {"name": "Politie Romana",   "color": "#2850ff", "maxMembers": 50,  "appsOpen": True},
    {"name": "Politia Locala",   "color": "#5078ff", "maxMembers": 30,  "appsOpen": False},
    {"name": "Jandarmeria",      "color": "#1e2864", "maxMembers": 40,  "appsOpen": False},
    {"name": "SMURD / Medici",   "color": "#ff3232", "maxMembers": 30,  "appsOpen": False},
    {"name": "School Instructors","color":"#009664", "maxMembers": 20,  "appsOpen": False},
    {"name": "Mafia Sandwich",   "color": "#8b4513", "maxMembers": 25,  "appsOpen": False},
    {"name": "Clanul Sportivilor","color":"#646464","maxMembers": 30,  "appsOpen": False},
    {"name": "Hitman Agency",    "color": "#141414", "maxMembers": 15,  "appsOpen": False},
]

def send_to_roblox(payload: dict):
    if not ROBLOX_API_KEY or not UNIVERSE_ID:
        return False, "Lipsesc variabilele de mediu ROBLOX_API_KEY / UNIVERSE_ID"
    headers = {"x-api-key": ROBLOX_API_KEY, "Content-Type": "application/json"}
    body    = {"message": json.dumps(payload)}
    try:
        r = requests.post(ROBLOX_MSG_URL, headers=headers, json=body, timeout=8)
        if r.status_code == 200:
            return True, "OK"
        return False, f"HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, str(e)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ── AUTH ──────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pwd  = request.form.get("password", "")
        name = request.form.get("admin_name", "WebAdmin")
        if pwd == ADMIN_PASSWORD:
            session["logged_in"]  = True
            session["admin_name"] = name
            return redirect(url_for("index"))
        error = "Parolă incorectă!"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── PAGINI ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html",
                           admin_name=session.get("admin_name", "Admin"),
                           factions=FACTIONS)

# ── API ───────────────────────────────────────────────────────────────────
@app.route("/api/command", methods=["POST"])
@login_required
def api_command():
    data = request.get_json(force=True) or {}
    cmd_type = data.get("type", "")
    target   = data.get("target", "")
    value    = str(data.get("value", "0"))
    reason   = data.get("reason", "Admin Panel")
    admin    = session.get("admin_name", "WebAdmin")

    if not cmd_type or not target:
        return jsonify({"ok": False, "message": "tip sau target lipsa"})

    payload = {
        "Admin":  admin,
        "Type":   cmd_type,
        "Target": target,
        "Value":  value,
        "Reason": reason
    }
    ok, msg = send_to_roblox(payload)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/factions")
@login_required
def api_factions():
    return jsonify(FACTIONS)

# ── KEEP ALIVE ────────────────────────────────────────────────────────────
def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
