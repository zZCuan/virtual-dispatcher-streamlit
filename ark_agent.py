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


@dataclass(frozen=True)
class AgentHandoff:
    analysis: str
    delegated_task: str
    used_ai: bool
    note: str = ""


class ArkAgent:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 8,
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
        except Exception as exc:
            return TicketReview(
                title,
                steps,
                False,
                f"方舟调用失败，已使用原操作票：{type(exc).__name__}",
            )

    def coordinate_handoff(
        self,
        *,
        level: str,
        title: str,
        task_text: str,
        sender: str,
        receiver: str,
        target_region: str,
    ) -> AgentHandoff:
        """Create a traceable superior-agent analysis and downstream task."""
        level_name = "省级" if level == "province" else "市级"
        fallback_analysis = (
            f"{level_name}智能体已核验任务层级、接收范围与目标区域，"
            f"确认由{receiver}承接并继续闭环处理。"
        )
        if not self.enabled:
            return AgentHandoff(
                fallback_analysis,
                task_text,
                False,
                "火山方舟未配置，已使用规则化智能体协同结果",
            )

        system_prompt = (
            f"你是配电网三级调度系统中的{level_name}调度智能体。"
            "你负责理解上级目标、校验管辖范围，并向下一级智能体形成明确任务。"
            "不得改变发送方、接收方和目标地区；不得新增、删除、猜测线路名称、"
            "开关编号、刀闸编号或操作顺序；不得宣称已经执行任何设备操作。"
            "只返回JSON对象，字段必须为 analysis 和 delegated_task。"
            "analysis用一句话说明任务判断和分派理由；delegated_task保留原任务全部"
            "关键步骤，仅允许改善表达和补充职责边界。"
        )
        user_prompt = json.dumps(
            {
                "智能体层级": level_name,
                "发送智能体": sender,
                "接收智能体": receiver,
                "目标地区": target_region,
                "任务名称": title,
                "原始任务": task_text,
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
            result = json.loads(content)
            analysis = str(result.get("analysis", "")).strip()
            delegated_task = str(result.get("delegated_task", "")).strip()
            if not analysis or not delegated_task:
                raise ValueError("Ark returned incomplete handoff fields")
            return AgentHandoff(
                analysis,
                delegated_task,
                True,
                f"已由{level_name}智能体完成任务分析与下级分派",
            )
        except Exception as exc:
            return AgentHandoff(
                fallback_analysis,
                task_text,
                False,
                f"{level_name}智能体调用失败，已使用规则化分派：{type(exc).__name__}",
            )
