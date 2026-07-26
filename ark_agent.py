"""Volcengine Ark Agent Plan client used for operation-ticket text review.

The model is deliberately kept outside the transport and persistence path:
failure, timeout, or malformed output always returns the operator's original
ticket unchanged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketReview:
    title: str
    steps: str
    used_ai: bool
    note: str = ""


class ArkAgent:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 15,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def review_ticket(
        self,
        *,
        title: str,
        steps: str,
        sender: str,
        receiver: str,
        target_county: str,
    ) -> TicketReview:
        if not self.enabled:
            return TicketReview(title, steps, False, "火山方舟未配置")

        system_prompt = (
            "你是配电网三级调度系统中的操作票文字审校智能体。"
            "你的工作仅限于规范中文表达，不执行设备操作，不改变下发对象，"
            "不得新增、删除或猜测线路名、开关编号、刀闸编号和操作顺序。"
            "如果原文存在不确定或高风险信息，保持原文，不要自行修正。"
            "只返回JSON对象，字段必须为 title 和 steps；steps使用换行分隔各项。"
        )
        user_prompt = json.dumps(
            {
                "发送方": sender,
                "接收方": receiver,
                "目标区县": target_county,
                "任务名称": title,
                "操作步骤": steps,
            },
            ensure_ascii=False,
        )
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = str(body["choices"][0]["message"]["content"]).strip()
            if content.startswith("```"):
                content = content.removeprefix("```json").removeprefix("```")
                content = content.removesuffix("```").strip()
            reviewed = json.loads(content)
            reviewed_title = str(reviewed.get("title", "")).strip()
            reviewed_steps = str(reviewed.get("steps", "")).strip()
            if not reviewed_title or not reviewed_steps:
                raise ValueError("Ark returned empty ticket fields")
            return TicketReview(
                reviewed_title,
                reviewed_steps,
                True,
                "已通过火山方舟调度智能体文字审校",
            )
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            return TicketReview(
                title,
                steps,
                False,
                f"方舟调用失败，已使用原操作票：{type(exc).__name__}",
            )
