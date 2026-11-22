from datetime import time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Schedule(Input):
    name: str = Field(min_length=1, max_length=100)
    account_ids: list[int] = Field(min_length=1, max_length=500)
    start: str = "23:00"
    end: str = "09:00"
    timezone: str = "Asia/Shanghai"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    enabled: bool = False

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value):
        if len(value) != 5 or value[2] != ":":
            raise ValueError("时间格式必须为 HH:MM")
        time.fromisoformat(value)
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效时区")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.start == self.end:
            raise ValueError("开始与结束时间不能相同")
        if any(n < 0 or n > 6 for n in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("星期取值为 0 至 6，不能重复")
        if any(n <= 0 for n in self.account_ids) or len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("账号 ID 必须为正整数且不能重复")
        return self


class Rule(Input):
    name: str = Field(min_length=1, max_length=100)
    days: int = Field(default=30, ge=1, le=3650)
    enabled: bool = False
    subject: str = Field(default="账号闲置提醒", min_length=1, max_length=200)
    body: str = Field(default="您好 {username}，您的账号已有 {days} 天未使用，请联系管理员了解详情。", min_length=1, max_length=20000)
    disable_after_email: bool = False

    @field_validator("subject")
    @classmethod
    def no_header_injection(cls, value):
        if "\r" in value or "\n" in value:
            raise ValueError("邮件标题不能包含换行")
        return value


class Email(Input):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("subject")
    @classmethod
    def safe_subject(cls, value):
        return Rule.no_header_injection(value)


class EmailTemplate(Input):
    name: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("subject")
    @classmethod
    def safe_subject(cls, value):
        return Rule.no_header_injection(value)


class Toggle(Input):
    schedulable: bool


class UserStatus(Input):
    status: Literal["active", "disabled"]


class Modules(Input):
    benchmark: bool = False
    pricing: bool = False
    ranking: bool = False


class Session(Input):
    token: str = Field(min_length=1, max_length=8192)


class Connection(Input):
    url: str = Field(min_length=1, max_length=2048)
    admin_key: str = Field(min_length=1, max_length=8192, repr=False)
    helper_url: str = Field(default="", max_length=2048)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        from .config import _url
        return _url(value, "服务地址")

    @field_validator("helper_url")
    @classmethod
    def valid_helper_url(cls, value):
        from .config import _url
        return _url(value, "助手地址", False)


class Benchmark(Input):
    model: str = Field(min_length=1, max_length=150)
    api_key: str = Field(min_length=1, max_length=4096)
    api_format: Literal["chat", "responses"] = "chat"


class BenchmarkSelection(Input):
    model: str = Field(min_length=1, max_length=150)
    key_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    api_format: Literal["chat", "responses"] = "chat"


class RankReward(Input):
    rank: int = Field(ge=1, le=3)
    amount: Decimal = Field(default=Decimal("0"), ge=0, le=1000000, max_digits=13, decimal_places=6)


class Automation(Input):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["subscription_multiplier", "monthly_rewards"]
    enabled: bool = False
    timezone: str = "Asia/Shanghai"
    group_id: int | None = Field(default=None, gt=0)
    expires_within_days: int = Field(default=2, ge=1, le=365)
    remaining_fraction_gt: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    quota_period: Literal["day", "week", "month"] = "month"
    multiplier: float = Field(default=.5, gt=0, le=100, allow_inf_nan=False)
    scope: Literal["user_group", "global_group"] = "user_group"
    restore_on_exit: bool = True
    mode: Literal["automatic", "approval"] = "automatic"
    rewards: list[RankReward] = Field(default_factory=lambda: [RankReward(rank=n) for n in (1, 2, 3)], min_length=3, max_length=3)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        return Schedule.valid_zone(value)

    @model_validator(mode="after")
    def valid_automation(self):
        if self.type == "subscription_multiplier" and self.group_id is None:
            raise ValueError("订阅倍率规则必须选择分组")
        if {r.rank for r in self.rewards} != {1, 2, 3}:
            raise ValueError("奖励名次必须为第 1、2、3 名且不能重复")
        if self.type == "monthly_rewards" and self.enabled and not any(r.amount > 0 for r in self.rewards):
            raise ValueError("启用月度奖励前请至少设置一个大于零的奖励金额")
        return self
