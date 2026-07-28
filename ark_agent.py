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

    def _complete(
        self,
        system_prompt: str,
        user_data: dict,
        timeout_seconds: float | None = None,
    ) -> dict:
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
        return _parse_json_object(
            _request_content(
                request,
                self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            )
        )

    def test_connection(self, timeout_seconds: float = 5) -> tuple[bool, str]:
        """Run a minimal Ark request and return a user-safe diagnostic."""
        if not self.enabled:
            return False, "方舟 API Key、Base URL 或模型名称尚未完整配置"
        try:
            result = self._complete(
                "你是接口连通性检测程序。只返回 JSON 对象：{\"status\":\"ok\"}。",
                {"action": "health_check"},
                timeout_seconds=timeout_seconds,
            )
            if str(result.get("status", "")).lower() != "ok":
                return False, "模型已响应，但返回内容未通过格式校验"
            return True, f"方舟模型 {self.model} 调用正常"
        except Exception as exc:
            return False, str(exc)[:240]

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
            "你是配电网三级调度系统中的操作票理解与文字审校智能体。"
            "首要目标是帮助接收操作员准确理解上级下发任务，而不是单纯润色文字。"
            "请先在内部判断任务目的、目标区域、操作对象、关键动作、先后顺序、"
            "完成条件和回令要求，再将原文整理成清晰、简洁、无歧义的调度表达。"
            "title 应直接概括任务对象与动作；steps 应按原有逻辑分段或编号，"
            "使操作员能够快速核对“做什么、对哪里做、按什么顺序、完成后如何反馈”。"
            "必须保留原文全部有效信息，不执行设备操作，不改变发送方、接收方和目标区域；"
            "不得新增、删除、替换或猜测线路名、开关编号、刀闸编号及操作顺序。"
            "原文未提供的信息不得自行补齐；遇到歧义、不确定或高风险内容时保持原文，"
            "不得用看似合理的内容掩盖信息缺失。"
            "只返回 JSON 对象，字段必须为 title 和 steps，不要返回解释或 Markdown。"
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
            f"【任务理解】执行“{title}”，按操作票原文完成规定动作。\n"
            f"【责任边界】由{receiver}承接，执行范围限定为{target_region}。\n"
            "【核对重点】按操作票顺序执行并逐项核对，不扩展原文未说明的设备或动作。\n"
            f"【完成与回令】完成后记录执行结果，并向{sender}提交回令。"
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
            "你的核心职责是帮助当前层级操作员理解上级任务，并把上级调度意图"
            "转换为下一层操作员容易核对、容易执行、容易回令的任务说明。"
            "请按以下逻辑分析："
            "1.任务目的：说明本次调度希望达到什么结果；"
            "2.责任边界：说明当前接收智能体和目标地区分别承担什么；"
            "3.关键动作：按原文顺序提取必须执行和核对的动作；"
            "4.注意事项：指出原文中需要重点核对的对象、编号、顺序或歧义，"
            "没有明确风险时写“按操作票顺序执行并逐项核对”；"
            "5.完成标准：说明执行完成后需要确认什么并向谁回令。"
            "不得改变发送方、接收方和目标地区；不得新增、删除或猜测线路名称、"
            "开关编号、刀闸编号或操作顺序；不得宣称已执行设备操作。"
            "只返回 JSON 对象，字段必须为 analysis 和 delegated_task。"
            "analysis 使用简短分行文本，依次包含【任务理解】【责任边界】"
            "【核对重点】【完成与回令】，用于直接展示给操作员阅读。"
            "delegated_task 是发给下一级智能体的正式任务正文，应保留原任务全部"
            "关键步骤和准确参数，可改善表达并明确职责边界，但不得凭空补充技术信息。"
            "不要返回 JSON 以外的解释、代码块或 Markdown。"
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
                    "输出用途": "供当前层级操作员理解任务，并供下一级智能体承接执行",
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
