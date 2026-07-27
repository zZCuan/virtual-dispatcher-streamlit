"""Volcengine Ark Agent Plan client for dispatch ticket review and handoff."""

from __future__ import annotations

import json
import socket
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


def _chat_endpoint(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    return endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"


def _safe_http_error(exc: urllib.error.HTTPError) -> str:
    """Return useful diagnostics without exposing credentials or full responses."""
    detail = ""
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            code = error.get("code") or payload.get("code")
            message = error.get("message") or payload.get("message")
            detail = " · ".join(str(value) for value in (code, message) if value)
    except Exception:
        pass
    return f"HTTP {exc.code}{' · ' + detail[:240] if detail else ''}"


def _request_content(request: urllib.request.Request, timeout: float) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"]).strip()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_safe_http_error(exc)) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"请求超时（{int(timeout)} 秒）") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", "")
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise RuntimeError(f"请求超时（{int(timeout)} 秒）") from exc
        raise RuntimeError(f"网络连接失败 · {str(reason)[:180]}") from exc
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("方舟返回格式异常，未找到模型回复内容") from exc


def _parse_json_object(content: str) -> dict:
    """Accept plain JSON, fenced JSON, or JSON surrounded by short prose."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("模型回复不是有效 JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("模型回复中的 JSON 无法解析") from exc


class ArkAgent:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 25,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def _complete(self, system_prompt: str, user_data: dict) -> dict:
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(user_data, ensure_ascii=False),
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            _chat_endpoint(self.base_url),
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return _parse_json_object(_request_content(request, self.timeout_seconds))

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
            "仅规范中文表达，不执行设备操作，不改变下发对象；"
            "不得新增、删除或猜测线路名、开关编号、刀闸编号及操作顺序。"
            "遇到不确定或高风险信息时保持原文。"
            "只返回 JSON 对象，字段必须为 title 和 steps。"
        )
        try:
            reviewed = self._complete(
                system_prompt,
                {
                    "发送方": sender,
                    "接收方": receiver,
                    "目标区县": target_county,
                    "任务名称": title,
                    "操作步骤": steps,
                },
            )
            reviewed_title = str(reviewed.get("title", "")).strip()
            reviewed_steps = str(reviewed.get("steps", "")).strip()
            if not reviewed_title or not reviewed_steps:
                raise RuntimeError("模型返回的操作票字段为空")
            return TicketReview(
                reviewed_title,
                reviewed_steps,
                True,
                "已通过火山方舟调度智能体完成文字审校",
            )
        except Exception as exc:
            return TicketReview(
                title,
                steps,
                False,
                f"方舟调用失败，已使用原操作票：{exc}",
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
        level_name = {
            "province": "省级",
            "city": "市级",
            "county": "区县级",
        }.get(level, "调度")
        fallback_analysis = (
            f"{level_name}智能体已校验任务层级、接收范围与目标区域，"
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
            "负责理解上级目标、校验管辖范围，并向下一层智能体形成明确任务。"
            "不得改变发送方、接收方和目标地区；不得新增、删除或猜测线路名称、"
            "开关编号、刀闸编号或操作顺序；不得宣称已执行设备操作。"
            "只返回 JSON 对象，字段必须为 analysis 和 delegated_task。"
            "analysis 用一句话说明任务判断和分派理由；delegated_task 保留原任务"
            "全部关键步骤，仅允许改善表达和补充职责边界。"
        )
        try:
            result = self._complete(
                system_prompt,
                {
                    "智能体层级": level_name,
                    "发送智能体": sender,
                    "接收智能体": receiver,
                    "目标地区": target_region,
                    "任务名称": title,
                    "原始任务": task_text,
                },
            )
            analysis = str(result.get("analysis", "")).strip()
            delegated_task = str(result.get("delegated_task", "")).strip()
            if not analysis or not delegated_task:
                raise RuntimeError("模型返回的任务分派字段不完整")
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
                f"{level_name}智能体调用失败，已使用规则化分派：{exc}",
            )
