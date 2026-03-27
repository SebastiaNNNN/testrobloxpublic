from http.server import BaseHTTPRequestHandler
import json, os, requests

ROBLOX_API_KEY = os.environ.get("ROBLOX_API_KEY", "")
UNIVERSE_ID    = os.environ.get("UNIVERSE_ID", "")
ADMIN_KEY      = os.environ.get("ADMIN_KEY", "")

ROBLOX_URL = f"https://apis.roblox.com/messaging-service/v1/universes/{UNIVERSE_ID}/topics/DiscordCommands"

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            self._json(400, {"ok": False, "msg": "JSON invalid"})
            return

        # --- Auth ---
        if not ADMIN_KEY or body.get("adminKey") != ADMIN_KEY:
            self._json(401, {"ok": False, "msg": "Cheie admin incorectă."})
            return

        cmd_type = body.get("type", "")
        target   = body.get("target", "")
        if not cmd_type or not target:
            self._json(400, {"ok": False, "msg": "type și target sunt obligatorii."})
            return

        payload = {
            "Admin":  body.get("adminName", "WebPanel"),
            "Type":   cmd_type,
            "Target": target,
            "Value":  str(body.get("value", "0")),
            "Reason": body.get("reason", "Admin Panel")
        }

        if not ROBLOX_API_KEY or not UNIVERSE_ID:
            self._json(500, {"ok": False, "msg": "Variabilele ROBLOX_API_KEY / UNIVERSE_ID lipsesc din Vercel."})
            return

        headers = {"x-api-key": ROBLOX_API_KEY, "Content-Type": "application/json"}
        roblox_body = {"message": json.dumps(payload)}

        try:
            r = requests.post(ROBLOX_URL, headers=headers, json=roblox_body, timeout=8)
            if r.status_code == 200:
                self._json(200, {"ok": True,  "msg": "Comandă trimisă cu succes!"})
            else:
                self._json(200, {"ok": False, "msg": f"Roblox API: HTTP {r.status_code} — {r.text[:200]}"})
        except requests.exceptions.Timeout:
            self._json(200, {"ok": False, "msg": "Timeout la conexiunea cu Roblox."})
        except Exception as e:
            self._json(500, {"ok": False, "msg": str(e)})

    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
