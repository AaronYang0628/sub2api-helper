"""Isolated Docker smoke-test fixture. Never point at a real deployment."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import parse_qs, urlsplit

ACCOUNT = {"id": 1, "name": "夜间免费账号", "platform": "openai", "status": "active", "schedulable": False}
USERS = [
    {"id": 1, "username": "admin", "email": "admin@example.invalid", "role": "admin", "status": "active", "last_used_at": "2026-01-01T00:00:00Z"},
    {"id": 2, "username": "private-user", "email": "private@example.invalid", "role": "user", "status": "active", "last_used_at": "2026-01-01T00:00:00Z"},
    {"id": 4, "username": "second-user", "email": "second@example.invalid", "role": "user", "status": "active"},
    {"id": 5, "username": "third-user", "email": "third@example.invalid", "role": "user", "status": "active"},
]
GROUP = {"id": 3, "name": "订阅分组", "status": "active", "subscription_type": "subscription",
         "rate_multiplier": 1, "daily_limit_usd": 100, "weekly_limit_usd": 100, "monthly_limit_usd": 100}
RATES = {}
NOW = datetime.now(timezone.utc)
SUBSCRIPTION = {"id": 11, "user_id": 2, "group_id": 3, "status": "active", "starts_at": (NOW-timedelta(days=10)).isoformat(),
                "expires_at": (NOW+timedelta(hours=12)).isoformat(), "group": GROUP}
BALANCES = {1: 0, 2: 0, 4: 0, 5: 0}
SETTINGS = {"session_binding_enabled": False, "custom_menu_items": [
    {"id": "existing-docs", "label": "原有文档", "url": "https://example.invalid", "visibility": "user", "sort_order": 0, "icon_svg": ""}
]}
GIF_RESPONSE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def do_PUT(self):
        self.handle_api()

    def handle_api(self):
        path = self.path.split("?", 1)[0]
        query = parse_qs(urlsplit(self.path).query)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
        if path == "/":
            output = b'<!doctype html><html><head><title>sub2api fixture</title></head><body>Original upstream page</body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
            return
        if path.startswith("/api/v1/admin/"):
            if self.headers.get("x-api-key") != "fixture-admin-key":
                return self.reply(401, {})
        elif self.headers.get("x-api-key"):
            return self.reply(400, {"error": "management credential leaked"})
        if path.endswith("/accounts/1/schedulable"):
            ACCOUNT["schedulable"] = body["schedulable"]
            data = ACCOUNT
        elif path.endswith("/accounts"):
            data = self.page([ACCOUNT])
        elif path.endswith("/users"):
            data = self.page(USERS)
        elif re.fullmatch(r"/api/v1/admin/users/\d+/balance", path):
            uid = int(path.split("/")[-2])
            assert self.headers.get("Idempotency-Key") and body["operation"] == "add"
            BALANCES[uid] += body["balance"]
            data = {"id": uid, "balance": BALANCES[uid]}
        elif re.fullmatch(r"/api/v1/admin/users/\d+", path):
            uid = int(path.split("/")[-1])
            data = next(user for user in USERS if user["id"] == uid)
            if self.command == "PUT":
                for gid, value in body.get("group_rates", {}).items():
                    if value is None:
                        RATES.pop((uid, int(gid)), None)
                    else:
                        RATES[(uid, int(gid))] = value
                data.update({key: value for key, value in body.items() if key != "group_rates"})
            data = {**data, "balance": BALANCES[uid]}
        elif path == "/api/v1/admin/groups":
            data = self.page([GROUP])
        elif path == "/api/v1/admin/groups/3":
            if self.command == "PUT":
                GROUP.update(body)
            data = GROUP
        elif path == "/api/v1/admin/groups/3/rate-multipliers":
            data = [{"user_id": uid, "rate_multiplier": rate} for (uid, gid), rate in RATES.items() if gid == 3]
        elif path == "/api/v1/admin/subscriptions":
            data = self.page([SUBSCRIPTION])
        elif path == "/api/v1/admin/subscriptions/11":
            data = SUBSCRIPTION
        elif path == "/api/v1/admin/subscriptions/11/progress":
            quota = {"limit_usd": 100, "used_usd": 20, "remaining_usd": 80,
                     "window_start": (NOW-timedelta(days=1)).isoformat(), "resets_at": (NOW+timedelta(days=2)).isoformat()}
            data = {"id": 11, "expires_at": SUBSCRIPTION["expires_at"], "daily": quota, "weekly": quota, "monthly": quota}
        elif path == "/api/v1/admin/usage/stats":
            uid = int(query["user_id"][0])
            data = {"total_actual_cost": {2: 100, 4: 80, 5: 60}[uid], "total_requests": 10, "total_tokens": 100}
        elif path.endswith("/compliance"):
            data = {"required": False, "acknowledgement": {"admin_user_id": 1}}
        elif path.endswith("/api-keys") or path == "/api/v1/keys":
            owner = 1 if path.endswith("/api-keys") else 2
            data = self.page([{"id": owner, "name": "测试分组 Key", "user_id": owner,
                               "key": "fixture-model-key", "group_id": 3, "status": "active", "quota": 0,
                               "group": {"id": 3, "name": "标准分组", "status": "active"}}])
        elif path.endswith("/settings"):
            if self.command == "PUT":
                if set(body) != {"custom_menu_items"}:
                    return self.reply(400, {})
                SETTINGS.update(body)
            data = SETTINGS
        elif path.endswith("/system/version"):
            data = {"version": "0.2.22"}
        elif path == "/api/v1/user/profile":
            data = USERS[0] if self.headers.get("Authorization") == "Bearer fixture-admin-jwt" else USERS[1]
        elif path == "/api/v1/groups/available":
            if self.headers.get("Authorization") != "Bearer fixture-user-jwt":
                return self.reply(401, {})
            data = [GROUP]
        elif path.endswith("/models"):
            data = {"models": [{"model": "fixture-model", "requests": 10, "input_tokens": 100,
                                "output_tokens": 20, "total_tokens": 120, "actual_cost": 0.01}]}
        elif path.endswith("/users-ranking"):
            data = {"ranking": [{"user_id": 2, "username": "private-user", "email": "private@example.invalid",
                                 "requests": 10, "tokens": 120, "actual_cost": 0.01}]}
        elif path in {"/v1/chat/completions", "/v1/responses"}:
            if self.headers.get("Authorization") != "Bearer fixture-model-key":
                return self.reply(401, {})
            if "画廊验证" in json.dumps(body, ensure_ascii=False):
                artwork = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 40"><title>湖畔</title><rect width="60" height="40" fill="#1b766e"/><script>alert(1)</script></svg>'
                return self.reply(200, {"choices": [{"message": {"content": artwork}}]})
            prompt = body["messages"][0]["content"] if "messages" in body else body["input"]
            answers = {"计算": "391", "字符串": "6", "所有": "YES", "将列表": "[-1,2,9,11]", "有 3": "7"}
            answer = next(value for prefix, value in answers.items() if prompt.startswith(prefix))
            if path.endswith("responses"):
                content = [{"type": "output_text", "text": answer}]
                if prompt.startswith("计算"):
                    content.append({"type": "output_image", "image_url": GIF_RESPONSE})
                return self.reply(200, {"output": [{"type": "message", "content": content}]})
            return self.reply(200, {"choices": [{"message": {"content": answer}}]})
        else:
            return self.reply(404, {})
        self.reply(200, {"code": 0, "message": "success", "data": data})

    @staticmethod
    def page(items):
        return {"items": items, "total": len(items), "page": 1, "page_size": 100, "pages": 1}

    def reply(self, status, payload):
        output = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
