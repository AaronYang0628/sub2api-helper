"""Build first, then run: python tests/container_smoke.py [image-tag].

Creates and removes only its own Docker network/containers and anonymous volume.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def main():
    image = sys.argv[1] if len(sys.argv) > 1 else "sub2api-helper:" + (Path(__file__).resolve().parents[1] / "helper/VERSION").read_text(encoding="ascii").strip()
    suffix = uuid.uuid4().hex[:10]
    network, mock, app, native = [f"helper-smoke-{kind}-{suffix}" for kind in ("net", "upstream", "app", "native")]
    token = ""
    fixture = str(Path(__file__).with_name("mock_upstream.py").resolve())

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True).strip()

    try:
        docker("network", "create", network)
        docker("run", "-d", "--name", mock, "--user", "0", "--network", network, "--network-alias", "upstream",
               "-v", fixture + ":/fixtures/mock_upstream.py:ro", image, "python", "/fixtures/mock_upstream.py")
        docker("run", "-d", "--name", app, "--network", network, "-p", "127.0.0.1::8080",
               "-e", "NO_PROXY=" + os.environ.get("NO_PROXY", "") + ",upstream",
               "-e", "no_proxy=" + os.environ.get("no_proxy", "") + ",upstream",
               "-e", "SUB2API_URL=http://upstream:8000", "-e", "SUB2API_PUBLIC_URL=https://sub2api.example.invalid",
               "-e", "HELPER_NATIVE_UI=false",
               "-e", "HELPER_PUBLIC_URL=https://helper.example.invalid", image)
        port = json.loads(docker("inspect", app))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port

        def request(path, method="GET", body=None, headers=None):
            head = {"Authorization": "Bearer " + token, **(headers or {})}
            if body is not None:
                head["Content-Type"] = "application/json"
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=head, method=method)
            with urllib.request.urlopen(req, timeout=15) as response:
                data = response.read()
                return json.loads(data) if "json" in response.headers.get("Content-Type", "") else data

        def ready():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    request("/_helper/healthz")
                    return
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            raise RuntimeError("Container not ready: " + docker("logs", app))

        ready()
        expected_version = (Path(__file__).resolve().parents[1] / "helper/VERSION").read_text(encoding="ascii").strip()
        assert request("/_helper/healthz")["version"] == expected_version, "Image runtime version differs from the canonical release version"
        assert b"sub2api" in request("/_helper/setup")
        assert b"<header" not in request("/_helper/setup") and b"<aside" not in request("/_helper/setup")
        assert request("/_helper/api/admin/connection")["configured"] is False
        try:
            request("/_helper/api/admin/status")
            raise AssertionError("Features open before connection")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        token = request("/_helper/api/admin/connection", "POST", {"url": "http://upstream:8000", "admin_key": "fixture-admin-key"})["session"]
        assert "fixture-admin-key" not in json.dumps(request("/_helper/api/admin/status"))
        assert "https://sub2api.example.invalid" in urllib.request.urlopen(base + "/_helper/setup").headers["Content-Security-Policy"]
        try:
            request("/_helper/api/admin/accounts", headers={"Authorization": "Bearer invalid"})
            raise AssertionError("Management auth bypass")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        now = datetime.now(timezone.utc)
        schedule = request("/_helper/api/admin/schedules", "POST", {"name": "restart smoke", "account_ids": [1],
                           "start": (now - timedelta(hours=1)).strftime("%H:%M"),
                           "end": (now + timedelta(hours=1)).strftime("%H:%M"),
                           "timezone": "UTC", "weekdays": list(range(7)), "enabled": True})
        docker("restart", app)
        port = json.loads(docker("inspect", app))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port
        ready()
        assert request("/_helper/api/admin/connection")["configured"] is True, "Credential binding did not survive restart"
        token = request("/_helper/api/admin/connection", "POST", {"url": "http://upstream:8000", "admin_key": "fixture-admin-key"})["session"]
        assert request("/_helper/api/admin/schedules")[0]["id"] == schedule["id"], "SQLite did not survive restart"
        assert request("/_helper/api/admin/accounts")[0]["schedulable"] is True, "Schedule did not reconcile on restart"
        flags = {"benchmark": True, "pricing": True, "ranking": True}
        assert request("/_helper/api/admin/modules", "PUT", flags) == flags
        assert b"feature.js" in request("/_helper/features/ranking?token=fixture-user-jwt")
        assert b"gallery-view.js" in request("/_helper/features/benchmark")
        opts = request("/_helper/api/admin/benchmark/options")
        assert opts["keys"][0]["group_id"] == 3 and "fixture-model-key" not in json.dumps(opts)
        result = request("/_helper/api/admin/benchmark", "POST", {"model": "fixture-model", "key_id": 1, "group_id": 3})
        assert result["score"] == 5 and result["completed"] == 5
        session = request("/_helper/api/portal/session", "POST", {"token": "fixture-user-jwt"})["session"]
        headers = {"Authorization": "", "X-Portal-Session": session}
        ranking = request("/_helper/api/portal/ranking", headers=headers)
        assert "private-user" not in json.dumps(ranking) and "private@example" not in json.dumps(ranking)
        opts = request("/_helper/api/portal/benchmark/options", headers=headers)
        assert opts["keys"][0]["id"] == 2
        result = request("/_helper/api/portal/benchmark", "POST", {"model": "fixture-model", "key_id": 2, "group_id": 3,
                         "api_format": "responses"}, headers=headers)
        assert result["score"] == 5
        first = result["results"][0]
        assert first["answer"] == "391" and first["scored"] is True
        assert first["media"][0]["src"].startswith("data:image/gif;base64,")
        gallery_job = {"name": "湖畔作品", "model": "fixture-model", "key_id": 1,
                       "prompt": "画廊验证：用 SVG 画湖畔。", "api_format": "chat"}
        assert request("/_helper/api/admin/gallery/3", "PUT", gallery_job)["group"]["enabled"] is False
        gallery_run = request("/_helper/api/admin/gallery/3/run", "POST", {})
        work_path = f"/_helper/api/portal/gallery/3/works/{gallery_run['id']}"
        for _ in range(30):
            artwork = request(work_path, headers=headers)
            if artwork["status"] != "running":
                break
            time.sleep(.1)
        assert artwork["status"] == "success" and "<svg" in artwork["content"] and "<script" not in artwork["content"]
        assert artwork["prompt"] == gallery_job["prompt"] and "fixture-model-key" not in json.dumps(artwork)
        gallery = request("/_helper/api/portal/gallery/3", headers=headers)
        assert gallery["stats"]["total"] == 1 and "key_id" not in gallery["group"]
        second_job = {**gallery_job, "name": "第二分组作品", "model": "fixture-other-model", "key_id": 9}
        request("/_helper/api/admin/gallery/4", "PUT", second_job)
        shared_prompt = "画廊验证：同一份提示词，用 SVG 画湖畔。"
        comparison = request("/_helper/api/admin/gallery/comparison", "PUT", {"group_ids": [3, 4], "prompt": shared_prompt})
        assert comparison["config"]["enabled"] is False
        round_id = request("/_helper/api/admin/gallery/comparison/run", "POST", {})["round"]["id"]
        for _ in range(50):
            result = request("/_helper/api/portal/gallery/comparison", headers=headers)["round"]
            if result["status"] != "running":
                break
            time.sleep(.1)
        assert result["id"] == round_id and result["status"] == "success"
        assert {item["model"] for item in result["items"]} == {"fixture-model", "fixture-other-model"}
        for item in result["items"]:
            detail = request(f"/_helper/api/portal/gallery/{item['group_id']}/works/{item['work']['id']}", headers=headers)
            assert detail["prompt"] == shared_prompt and detail["round_id"] == round_id
        assert request("/_helper/api/portal/models", headers=headers)["models"][0]["requests"] == 10
        assert request("/_helper/api/admin/groups")[0]["id"] == 3
        rate_rule = request("/_helper/api/admin/automations", "POST", {"name": "到期倍率测试", "type": "subscription_multiplier",
                            "enabled": True, "group_id": 3, "expires_within_days": 2,
                            "remaining_fraction_gt": .5, "multiplier": .5, "quota_period": "month"})
        plan = request(f"/_helper/api/admin/automations/{rate_rule['id']}/preview", "POST")
        assert plan["matched_users"] == 1 and not plan["errors"]
        result = request(f"/_helper/api/admin/automations/{rate_rule['id']}/run", "POST")
        assert not result["errors"]

        def raw_get(path):
            code = "import json,urllib.request; r=urllib.request.Request('http://upstream:8000' + " + repr(path) + ",headers={'x-api-key':'fixture-admin-key'}); print(urllib.request.urlopen(r).read().decode())"
            return json.loads(docker("exec", app, "python", "-c", code))["data"]

        assert raw_get("/api/v1/admin/groups/3/rate-multipliers")[0]["rate_multiplier"] == .5
        request(f"/_helper/api/admin/automations/{rate_rule['id']}", "DELETE")
        assert raw_get("/api/v1/admin/groups/3/rate-multipliers") == [], "Rate baseline not restored on deletion"
        rewards = {"name": "月度奖励测试", "type": "monthly_rewards", "enabled": True, "mode": "approval",
                   "rewards": [{"rank": 1, "amount": 30}, {"rank": 2, "amount": 20}, {"rank": 3, "amount": 10}]}
        reward_rule = request("/_helper/api/admin/automations", "POST", rewards)
        plan = request(f"/_helper/api/admin/automations/{reward_rule['id']}/preview", "POST")
        assert [item["user_id"] for item in plan["actions"]] == [2, 4, 5]
        assert raw_get("/api/v1/admin/users/2")["balance"] == 0, "Preview changed a balance"
        result = request(f"/_helper/api/admin/automations/{reward_rule['id']}/run", "POST")
        assert result["executed"] == 3 and not result["errors"]
        assert raw_get("/api/v1/admin/users/2")["balance"] == 30
        assert raw_get("/api/v1/admin/users/4")["balance"] == 20
        assert raw_get("/api/v1/admin/users/5")["balance"] == 10
        assert request(f"/_helper/api/admin/automations/{reward_rule['id']}/run", "POST")["executed"] == 0
        request(f"/_helper/api/admin/automations/{reward_rule['id']}", "DELETE")
        cloned = request("/_helper/api/admin/automations", "POST", rewards)
        assert request(f"/_helper/api/admin/automations/{cloned['id']}/run", "POST")["executed"] == 0
        assert "fixture-user-jwt" not in docker("logs", app), "JWT leaked to access log"
        docker("stop", app)
        docker("run", "-d", "--name", native, "--network", network, "-p", "127.0.0.1::8080",
               "-e", "NO_PROXY=" + os.environ.get("NO_PROXY", "") + ",upstream",
               "-e", "no_proxy=" + os.environ.get("no_proxy", os.environ.get("NO_PROXY", "")) + ",upstream",
               "-e", "SUB2API_URL=http://upstream:8000", "-e", "SUB2API_ADMIN_KEY=fixture-admin-key",
               "-e", "HELPER_NATIVE_UI=true",
               "-e", "SUB2API_PUBLIC_URL=https://sub2api.example.invalid", image)
        port = json.loads(docker("inspect", native))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port
        token = "fixture-admin-jwt"
        ready()
        page = request("/", headers={"Accept": "text/html"})
        assert b"Original upstream page" in page and b"/_helper/native.js" in page
        native_document = urllib.request.Request(base, headers={"Accept": "text/html"})
        assert urllib.request.urlopen(native_document).headers["Content-Security-Policy"] == "default-src 'self'; script-src 'self'; style-src 'self'"
        assert b"__sub2apiHelperNative" in request("/_helper/native.js")
        assert request("/_helper/api/accounts/1/schedule")["managed"] is False
        payload = {"mode": "window", "start": (now + timedelta(hours=2)).strftime("%H:%M"),
                   "end": (now + timedelta(hours=3)).strftime("%H:%M"), "timezone": "UTC", "weekdays": list(range(7))}
        saved = request("/_helper/api/accounts/1/schedule", "PUT", payload)
        assert saved["managed"] is True and saved["schedulable"] is False
        saved = request("/_helper/api/accounts/1/schedule", "PUT", {"mode": "all_day"})
        assert saved["managed"] is False and saved["schedulable"] is True
        try:
            request("/_helper/api/status", headers={"Authorization": "Bearer fixture-user-jwt"})
            raise AssertionError("Ordinary user accessed native admin actions")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        opaque = request("/_helper/api/session", "POST", {})["session"]
        assert request("/_helper/api/admin/status", headers={"Authorization": "Bearer " + opaque})["version"] == expected_version
        assert "fixture-admin-jwt" not in docker("logs", native), "Native JWT leaked to access log"
        print("Container smoke passed: auth, restart schedule, menu sync, private stats, model keys, chat/responses, subscription multiplier restoration, monthly top3 awards and duplicate prevention across rule recreation.")
    except Exception:
        for name in (app, mock, native):
            print(name, docker("logs", name))
        raise
    finally:
        for container in (app, mock, native):
            subprocess.run(["docker", "rm", "-fv", container], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    main()
