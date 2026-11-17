"""Conservative, previewable subscription rates and monthly balance rewards.

The parent scheduler serializes this engine with configuration changes. Balance
credits deliberately use an intent-before-write ledger and never replay an
uncertain operation, even when the upstream advertises an Idempotency-Key.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import time
from zoneinfo import ZoneInfo

from .engine import parse_time


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("A timezone-aware current time is required")
    return value.astimezone(timezone.utc)


def _number(value, *, minimum=Decimal("0"), positive=False):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric value")
    try:
        number = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError("Invalid numeric value") from None
    if not number.is_finite() or number < minimum or (positive and number <= 0):
        raise ValueError("Invalid numeric value")
    return number


def _rate(value):
    return None if value is None else float(_number(value, positive=True))


def _same(left, right):
    if left is None or right is None:
        return left is right
    return _number(left) == _number(right)


def _id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid object ID")
    return value


def _ordinary(user, active=True, user_id=None):
    return (isinstance(user, dict) and user.get("role") == "user"
            and (user_id is None or (type(user.get("id")) is int and user.get("id") == user_id))
            and user.get("status") in ({"active"} if active else {"active", "disabled"}))


def settlement_period(now, timezone_name="Asia/Shanghai"):
    local = _now(now).astimezone(ZoneInfo(timezone_name))
    boundary = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = boundary.date() - timedelta(days=1)
    first = last.replace(day=1)
    return first.strftime("%Y-%m"), first.isoformat(), last.isoformat(), boundary.astimezone(timezone.utc)


class AutomationEngine:
    def __init__(self, store, upstream):
        self.store = store
        self.upstream = upstream

    def _instance(self):
        address = getattr(self.upstream, "base_url", "").rstrip("/")
        if not address:
            raise ValueError("No upstream instance is configured")
        return hashlib.sha256(address.encode()).hexdigest()[:24]

    def _prefix(self):
        return f"automation:multiplier:{self._instance()}:"

    async def tick(self, now=None):
        now = _now(now)
        results = []
        for rule in self.store.list("automations"):
            try:
                if rule.get("enabled"):
                    result = await self.run(rule, now)
                elif rule.get("type") == "subscription_multiplier":
                    result = await self.deactivate(rule, now)
                else:
                    continue
                errors = result.get("errors", [])
                self.store.update("automations", rule["id"], {"last_run_at": now.isoformat(), "last_error": "; ".join(errors) or None})
                results.append(result)
            except Exception as exc:
                detail = type(exc).__name__
                self.store.update("automations", rule["id"], {"last_run_at": now.isoformat(), "last_error": detail})
                self.store.audit("automation.tick", rule["id"], "error", detail)
                results.append({"rule_id": rule["id"], "errors": [detail], "executed": 0})
        return results

    async def preview(self, rule, now=None):
        now = _now(now)
        if rule.get("type") == "subscription_multiplier":
            return await self._rate_plan(rule, now)
        if rule.get("type") == "monthly_rewards":
            return await self._reward_plan(rule, now)
        raise ValueError("Unsupported automation type")

    async def run(self, rule, now=None, manual=False):
        now = _now(now)
        if not rule.get("enabled", False):
            return {"type": rule.get("type"), "rule_id": rule.get("id"), "actions": [], "errors": [], "executed": 0, "skipped": [{"reason": "Rule is disabled"}]}
        _id(rule.get("id"))
        if rule.get("type") == "monthly_rewards":
            _, _, _, boundary = settlement_period(now, rule.get("timezone", "Asia/Shanghai"))
            activated = parse_time(rule.get("enabled_at"))
            if not manual and (rule.get("mode", "automatic") != "automatic" or activated is None or activated > boundary):
                return {"type": rule["type"], "rule_id": rule["id"], "actions": [], "errors": [], "executed": 0, "skipped": [{"reason": "Automatic settlement requires activation before the month boundary; approval mode requires an explicit manual run"}]}
            return await self._run_rewards(rule, now)
        if rule.get("type") == "subscription_multiplier":
            return await self._execute_rates(await self._rate_plan(rule, now), now)
        raise ValueError("Unsupported automation type")

    async def deactivate(self, rule, now=None):
        if rule.get("type") != "subscription_multiplier":
            return {"type": rule.get("type"), "rule_id": rule.get("id"), "actions": [], "skipped": [], "errors": [], "executed": 0}
        try:
            if not rule.get("restore_on_exit", True):
                released = []
                for key, state in self.store.ledger_list(self._prefix()).items():
                    if state.get("rule_id") == rule.get("id") and state.get("status") in {"owned", "applying", "restoring"}:
                        self.store.ledger_set(key, state | {"status": "restored", "released_at": _now(now).isoformat(), "reason": "Restoration disabled; value retained and ownership released"})
                        self.store.audit("automation.rate.release", key, detail="Restoration disabled; upstream value retained")
                        released.append({"action": "release", "status": "done", "reason": "Upstream value retained; ownership released"})
                return {"type": rule["type"], "rule_id": rule.get("id"), "actions": released, "skipped": [], "errors": [], "executed": 0}
            return await self._execute_rates(await self._rate_plan(rule, _now(now), deactivate=True), _now(now))
        except Exception as exc:
            return {"type": rule.get("type"), "rule_id": rule.get("id"), "actions": [], "skipped": [], "errors": [type(exc).__name__], "executed": 0}

    async def _qualifying(self, rule, now):
        group_id = _id(rule.get("group_id"))
        threshold = _number(rule.get("remaining_fraction_gt", 0.5))
        if threshold > 1:
            raise ValueError("Remaining fraction must be at most one")
        horizon = _number(rule.get("expires_within_days", 2), positive=True)
        period = {"day": "daily", "week": "weekly", "month": "monthly"}.get(rule.get("quota_period", "month"))
        if period is None:
            raise ValueError("Unsupported quota period")
        group = await self.upstream.get_group(group_id)
        if not isinstance(group, dict) or group.get("id") != group_id or group.get("subscription_type") != "subscription":
            raise ValueError("Target is not a confirmed subscription group")
        if group.get("status") != "active":
            return {}, set(), [{"id": group_id, "reason": "Subscription group is disabled"}]
        candidates, unknown, skipped = {}, set(), []
        subscriptions = await self.upstream.list_subscriptions(group_id)
        if not isinstance(subscriptions, list):
            raise ValueError("Invalid subscription list")
        for row in subscriptions:
            user_id = row.get("user_id") if isinstance(row, dict) else None
            try:
                subscription_id = _id(row.get("id"))
                matched = await self._subscription_match(rule, subscription_id, user_id, now)
                if matched:
                    user_id = matched["user_id"]
                    candidates[user_id] = matched
            except Exception as exc:
                # Unknown quotas are not evidence that an existing discount should end.
                unknown.add(user_id if type(user_id) is int else None)
                skipped.append({"id": row.get("id") if isinstance(row, dict) else None, "reason": type(exc).__name__})
        return candidates, unknown, skipped

    async def _subscription_match(self, rule, subscription_id, expected_user, now):
        started = time.monotonic()
        subscription = await self.upstream.get_subscription(subscription_id)
        if (not isinstance(subscription, dict) or subscription.get("id") != subscription_id
                or subscription.get("group_id") != rule["group_id"]):
            raise ValueError("Subscription identity changed")
        user_id = _id(subscription.get("user_id"))
        if expected_user is not None and expected_user != user_id:
            raise ValueError("Subscription user changed")
        expiry, starts = parse_time(subscription.get("expires_at")), parse_time(subscription.get("starts_at"))
        if expiry is None or starts is None:
            raise ValueError("Subscription timing is unknown")
        if (subscription.get("status") != "active" or subscription.get("revoked_at") or starts > now
                or not now < expiry <= now + timedelta(days=float(_number(rule.get("expires_within_days", 2), positive=True)))):
            return None
        user = await self.upstream.get_user(user_id)
        if not _ordinary(user, user_id=user_id):
            return None
        group = await self.upstream.get_group(rule["group_id"])
        if group.get("status") != "active" or group.get("subscription_type") != "subscription":
            return None
        progress = await self.upstream.subscription_progress(subscription_id)
        if (not isinstance(progress, dict) or progress.get("id") != subscription_id
                or parse_time(progress.get("expires_at")) != expiry):
            raise ValueError("Subscription quota identity or expiry is unknown")
        period = {"day": "daily", "week": "weekly", "month": "monthly"}[rule.get("quota_period", "month")]
        quota = progress.get(period)
        if not isinstance(quota, dict):
            raise ValueError("Selected quota window is unavailable or unlimited")
        start, reset = parse_time(quota.get("window_start")), parse_time(quota.get("resets_at"))
        now = now + timedelta(seconds=max(0, time.monotonic() - started))
        if not now < expiry:
            return None
        if start is None or reset is None or not start <= now < reset:
            raise ValueError("Selected quota window is inactive or expired")
        limit = _number(quota.get("limit_usd"), positive=True)
        if limit != _number(group.get(period + "_limit_usd"), positive=True):
            raise ValueError("Quota cap changed during inspection")
        used, remaining = _number(quota.get("used_usd")), _number(quota.get("remaining_usd"))
        if remaining > limit or abs(remaining - max(limit - used, Decimal(0))) > Decimal("0.000001"):
            raise ValueError("Quota amounts are inconsistent")
        if remaining / limit <= _number(rule.get("remaining_fraction_gt", 0.5)):
            return None
        return {"user_id": user_id, "subscription_id": subscription_id, "remaining_fraction": float(remaining / limit), "expires_at": expiry.isoformat()}

    async def _current_rate(self, scope, user_id, group_id):
        if scope == "global_group":
            group = await self.upstream.get_group(group_id)
            if not isinstance(group, dict) or group.get("id") != group_id:
                raise ValueError("Group identity is unknown")
            value = _rate(group.get("rate_multiplier"))
            if value is None:
                raise ValueError("Group multiplier is unavailable")
            return value
        user = await self.upstream.get_user(user_id)
        if not _ordinary(user, active=False, user_id=user_id):
            raise ValueError("Rate target is not a confirmed ordinary user")
        return _rate(await self.upstream.user_group_rate(user_id, group_id))

    async def _rate_plan(self, rule, now, deactivate=False):
        started = time.monotonic()
        scope = rule.get("scope", "user_group")
        if scope not in {"user_group", "global_group"}:
            raise ValueError("Unsupported multiplier scope")
        group_id = _id(rule.get("group_id"))
        desired = _rate(rule.get("multiplier", 0.5))
        if desired is None or desired > 100:
            raise ValueError("Multiplier must be positive and at most 100")
        candidates, unknown, skipped = ({}, set(), []) if deactivate else await self._qualifying(rule, now)
        prefix = self._prefix()
        owned = {key: value for key, value in self.store.ledger_list(prefix).items()
                 if value.get("rule_id") == rule.get("id") and value.get("status") in {"owned", "applying", "restoring"}}
        targets = [(None, group_id)] if scope == "global_group" and candidates else [(uid, group_id) for uid in sorted(candidates)]
        keys = set()
        actions, errors = [], []
        for user_id, target_group in targets:
            key = prefix + (f"group:{target_group}" if scope == "global_group" else f"user:{user_id}:group:{target_group}")
            keys.add(key)
            state = self.store.ledger_get(key)
            if state and state.get("status") in {"owned", "applying", "restoring"} and state.get("rule_id") != rule.get("id"):
                errors.append(f"Rate target {user_id or 'global'}/{target_group} is owned by another rule")
                continue
            try:
                current = await self._current_rate(scope, user_id, target_group)
            except Exception as exc:
                errors.append(f"Rate read {user_id or 'global'}/{target_group}: {type(exc).__name__}")
                continue
            existing = owned.get(key)
            if existing and existing.get("status") == "restoring" and _same(current, existing.get("baseline")):
                existing = None  # A completed restore can be followed by a new qualifying window.
            if existing and not (_same(current, existing.get("applied")) or (existing.get("status") == "applying" and _same(current, existing.get("previous")))):
                errors.append(f"Rate target {user_id or 'global'}/{target_group} was edited outside this rule; no write")
                continue
            if _same(current, desired):
                if existing and existing.get("status") != "owned":
                    actions.append(self._rate_action("confirm", key, existing, current, desired))
                continue
            state = (existing or {"rule_id": rule.get("id"), "scope": scope, "user_id": user_id, "group_id": target_group, "baseline": current}).copy()
            state["condition"] = candidates[user_id] if user_id is not None else next(iter(candidates.values()))
            actions.append(self._rate_action("apply", key, state, current, desired))
        if rule.get("restore_on_exit", True):
            for key, state in owned.items():
                if key in keys:
                    continue
                user_id = state.get("user_id")
                if not deactivate and (None in unknown or user_id in unknown or (state["scope"] == "global_group" and unknown)):
                    skipped.append({"id": user_id, "reason": "Quota state is unknown; retaining owned rate"})
                    continue
                try:
                    current = await self._current_rate(state["scope"], user_id, state["group_id"])
                except Exception as exc:
                    errors.append(f"Rate read {user_id or 'global'}/{state['group_id']}: {type(exc).__name__}")
                    continue
                baseline = state.get("baseline")
                if _same(current, baseline):
                    actions.append(self._rate_action("release", key, state, current, baseline))
                elif _same(current, state.get("applied")) or (state.get("status") == "applying" and _same(current, state.get("previous"))):
                    actions.append(self._rate_action("restore", key, state, current, baseline))
                else:
                    action = self._rate_action("release", key, state, current, current)
                    action["reason"] = "Confirmed manual override; ownership released without changing the rate"
                    actions.append(action)
                    skipped.append({"id": user_id, "reason": action["reason"]})
        return {"type": "subscription_multiplier", "rule_id": rule.get("id"), "actions": actions, "skipped": skipped, "errors": errors, "matched_users": len(candidates), "rule": rule.copy(), "planned_monotonic": started}

    def _rate_action(self, operation, key, state, previous, desired):
        return {"action": ("restore_" if operation in {"restore", "release"} else "set_") + ("group_rate" if state["scope"] == "global_group" else "user_group_rate"),
                "operation": operation, "scope": state["scope"], "user_id": state.get("user_id"), "group_id": state["group_id"],
                "multiplier": desired, "previous_multiplier": previous, "status": "ready", "ledger_key": key, "ownership": state.copy()}

    async def _execute_rates(self, plan, now):
        plan["executed"] = 0
        for action in plan["actions"]:
            try:
                state = action["ownership"].copy()
                restoring = action["operation"] in {"restore", "release"}
                if action["operation"] == "apply":
                    condition = state["condition"]
                    current_time = now + timedelta(seconds=max(0, time.monotonic() - plan["planned_monotonic"]))
                    if not await self._subscription_match(plan["rule"], condition["subscription_id"], condition["user_id"], current_time):
                        action.update(status="skipped", reason="Subscription or user condition changed before applying")
                        continue
                # Read the rate after eligibility checks, as close to the write
                # as the upstream API permits (it has no atomic compare-and-set).
                current = await self._current_rate(action["scope"], action["user_id"], action["group_id"])
                if not _same(current, action["previous_multiplier"]):
                    raise ValueError("Rate changed after planning; no write")
                if action["operation"] in {"apply", "restore"}:
                    state.update(status="restoring" if restoring else "applying", previous=current, updated_at=now.isoformat())
                    if not restoring:
                        state["applied"] = action["multiplier"]
                    self.store.ledger_set(action["ledger_key"], state)
                    if action["scope"] == "global_group":
                        await self.upstream.set_group_rate(action["group_id"], action["multiplier"])
                    else:
                        await self.upstream.set_user_group_rate(action["user_id"], action["group_id"], action["multiplier"])
                    plan["executed"] += 1
                state.update(status="restored" if restoring else "owned", updated_at=now.isoformat())
                self.store.ledger_set(action["ledger_key"], state)
                action["status"] = "done"
                self.store.audit("automation.rate", action["ledger_key"], detail=f"{action['operation']} rule={plan['rule_id']}")
            except Exception as exc:
                action["status"] = "error"
                plan["errors"].append(f"Rate {action['user_id'] or 'global'}/{action['group_id']}: {type(exc).__name__}")
                self.store.audit("automation.rate", action["ledger_key"], "error", type(exc).__name__)
        return plan

    def _reward_key(self, month):
        return f"automation:monthly_rewards:{self._instance()}:{month}"

    async def _reward_plan(self, rule, now):
        month, start, end, boundary = settlement_period(now, rule.get("timezone", "Asia/Shanghai"))
        key = self._reward_key(month)
        snapshot = self.store.ledger_get(key)
        if snapshot is None:
            rewards = rule.get("rewards", [{"rank": 1, "amount": 0}, {"rank": 2, "amount": 0}, {"rank": 3, "amount": 0}])
            amounts = {_id(item.get("rank")): _number(item.get("amount")) for item in rewards}
            if len(rewards) != 3 or set(amounts) != {1, 2, 3}:
                raise ValueError("Reward ranks must be exactly one, two and three")
            rows = await self.upstream.monthly_spending(start, end, rule.get("timezone", "Asia/Shanghai"))
            if not isinstance(rows, list):
                raise ValueError("Monthly spending list is incomplete")
            eligible, seen = [], set()
            for row in rows:
                user_id = _id(row.get("user_id"))
                if user_id in seen:
                    raise ValueError("Monthly spending contains duplicate users")
                seen.add(user_id)
                spending = _number(row.get("actual_cost"))
                if spending <= 0:
                    continue
                user = await self.upstream.get_user(user_id)
                if _ordinary(user, user_id=user_id):
                    eligible.append((spending, _number(row.get("tokens", 0)), user_id))
            eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))
            winners = []
            for rank, (spending, _, user_id) in enumerate(eligible[:3], 1):
                winners.append({"action": "credit_balance", "user_id": user_id, "rank": rank, "amount": str(amounts[rank]),
                                "actual_cost": str(spending), "month": month, "status": "ready" if amounts[rank] else "skipped",
                                "idempotency_key": "helper-monthly-" + hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()})
            snapshot = {"month": month, "start_date": start, "end_date": end, "timezone": rule.get("timezone", "Asia/Shanghai"),
                        "settlement_at": boundary.isoformat(), "rule_id": rule.get("id"), "actions": winners}
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("actions"), list):
            raise ValueError("Settlement snapshot is invalid")
        return {"type": "monthly_rewards", "rule_id": rule.get("id"), "month": month,
                "start_date": snapshot["start_date"], "end_date": snapshot["end_date"], "actions": [dict(a) for a in snapshot["actions"]],
                "snapshot": snapshot, "ledger_key": key, "frozen": self.store.ledger_get(key) is not None, "skipped": [], "errors": []}

    async def _run_rewards(self, rule, now):
        plan = await self._reward_plan(rule, now)
        snapshot = plan["snapshot"]
        if not plan["frozen"] and not any(_number(a["amount"]) > 0 for a in snapshot["actions"]):
            plan["executed"] = 0
            plan["skipped"].append({"reason": "No positive reward is payable; no settlement snapshot was consumed"})
            return plan
        # The winners and amounts become immutable before the first financial call.
        if not plan["frozen"]:
            self.store.ledger_set(plan["ledger_key"], snapshot)
        plan["executed"] = 0
        for index, award in enumerate(snapshot["actions"]):
            if award["status"] in {"done", "skipped"}:
                continue
            if award["status"] in {"pending", "needs_review"}:
                plan["errors"].append(f"Reward for user {award['user_id']} requires manual balance-history review; it will not be replayed")
                continue
            try:
                user = await self.upstream.get_user(award["user_id"])
                if not _ordinary(user, user_id=award["user_id"]):
                    award.update(status="skipped", reason="Winner is no longer an active ordinary user; no replacement")
                    self.store.ledger_set(plan["ledger_key"], snapshot)
                    continue
            except Exception as exc:
                plan["errors"].append(f"Reward eligibility {award['user_id']}: {type(exc).__name__}")
                continue  # No financial intent yet; eligibility reads may be retried.
            award.update(status="pending", attempted_at=now.isoformat())
            self.store.ledger_set(plan["ledger_key"], snapshot)
            try:
                await self.upstream.add_balance(award["user_id"], float(_number(award["amount"], positive=True)), award["idempotency_key"])
            except Exception as exc:
                # Even an HTTP error may follow a committed upstream increment.
                award.update(status="needs_review", error=type(exc).__name__)
                self.store.ledger_set(plan["ledger_key"], snapshot)
                self.store.audit("automation.reward", award["user_id"], "error", f"month={plan['month']} outcome requires review; no automatic retry")
                plan["errors"].append(f"Reward for user {award['user_id']} requires manual balance-history review")
                continue
            award.update(status="done", completed_at=now.isoformat())
            self.store.ledger_set(plan["ledger_key"], snapshot)
            self.store.audit("automation.reward", award["user_id"], detail=f"month={plan['month']} rank={award['rank']} amount={award['amount']}")
            plan["executed"] += 1
        plan["actions"] = [dict(a) for a in snapshot["actions"]]
        return plan
