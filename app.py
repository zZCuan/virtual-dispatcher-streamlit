"""龙江电网虚拟配网调度中心——Streamlit 演示版。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, time as datetime_time
from pathlib import Path
from textwrap import dedent
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components
from pypinyin import lazy_pinyin

from agent_gateway import AgentGateway
from ark_agent import ArkAgent


st.set_page_config(
    page_title="龙江电网虚拟配网调度中心",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    #MainMenu, header, footer, [data-testid="stToolbar"] {display:none!important}
    [data-testid="stAppViewContainer"] {background:#eef5f3}
    [data-testid="stMain"] > div {padding:0!important;max-width:none!important}
    [data-testid="stExpander"] {background:#fff;border-color:#d6e5e1!important}
    [data-testid="stExpander"] details {border-radius:0 0 10px 10px}
    .stButton>button[kind="primary"] {background:linear-gradient(105deg,#007f66,#00a779);border:0}
    .stTextInput input,.stTextArea textarea,[data-baseweb="select"]>div {
      background:#fff!important;border-color:#bddbd3!important;color:#193a33!important
    }
    iframe {display:block}
    .workspace-brand {
      position:relative;margin:0;padding:18px 24px;background:linear-gradient(105deg,#006c58 0%,#008c70 58%,#169b79 100%);
      color:#fff;border-radius:10px 10px 0 0;box-shadow:0 5px 18px rgba(0,81,66,.16)
    }
    .workspace-brand b {font-size:22px;letter-spacing:1px}
    .workspace-brand small {display:block;margin-top:5px;color:#ccebe3;letter-spacing:2px}
    .workspace-logout {
      position:absolute;right:22px;top:50%;transform:translateY(-50%);
      padding:9px 18px;border:1px solid rgba(255,255,255,.55);border-radius:7px;
      background:rgba(255,255,255,.1);color:#fff!important;text-decoration:none!important;
      font-size:13px;transition:.2s ease
    }
    .workspace-logout:hover {background:#fff;color:#00745e!important;box-shadow:0 5px 15px rgba(0,71,57,.18)}
    </style>
    """,
    unsafe_allow_html=True,
)

DB_PATH = Path("/tmp/virtual_dispatcher_messages.db")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
try:
    streamlit_agent_api_url = str(st.secrets.get("DISPATCH_AGENT_API_URL", ""))
    streamlit_agent_api_token = str(st.secrets.get("DISPATCH_AGENT_API_TOKEN", ""))
    streamlit_ark_api_key = str(st.secrets.get("ARK_API_KEY", ""))
    streamlit_ark_base_url = str(st.secrets.get("ARK_BASE_URL", ""))
    streamlit_ark_model = str(st.secrets.get("ARK_MODEL", ""))
except Exception:
    streamlit_agent_api_url = ""
    streamlit_agent_api_token = ""
    streamlit_ark_api_key = ""
    streamlit_ark_base_url = ""
    streamlit_ark_model = ""
AGENT_API_URL = (os.getenv("DISPATCH_AGENT_API_URL") or streamlit_agent_api_url).strip()
AGENT_GATEWAY = AgentGateway(
    AGENT_API_URL,
    (os.getenv("DISPATCH_AGENT_API_TOKEN") or streamlit_agent_api_token).strip(),
) if AGENT_API_URL else None
ARK_AGENT = ArkAgent(
    os.getenv("ARK_API_KEY") or streamlit_ark_api_key,
    os.getenv("ARK_BASE_URL") or streamlit_ark_base_url,
    os.getenv("ARK_MODEL") or streamlit_ark_model,
)
CLEAR_DISPATCH_RECORDS_VERSION = "2026-07-25-clear-02"
NETWORK_COMPONENT_PATH = Path(__file__).parent / "network_component"
network_component = components.declare_component(
    "network_topology", path=str(NETWORK_COMPONENT_PATH)
)
DEMO_ACCOUNTS = {
    "hlj_province": {"password": "demo123", "role": "province", "name": "黑龙江省级调度账号"},
}


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dispatch_messages (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            title TEXT NOT NULL,
            ticket_no TEXT NOT NULL,
            target_county TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '已送达',
            acknowledged_at REAL,
            executed_at REAL,
            executed_by TEXT,
            request_key TEXT
        )
        """
    )
    existing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(dispatch_messages)")
    }
    if "executed_at" not in existing_columns:
        connection.execute("ALTER TABLE dispatch_messages ADD COLUMN executed_at REAL")
    if "executed_by" not in existing_columns:
        connection.execute("ALTER TABLE dispatch_messages ADD COLUMN executed_by TEXT")
    if "request_key" not in existing_columns:
        connection.execute("ALTER TABLE dispatch_messages ADD COLUMN request_key TEXT")
    connection.execute(
        """
        UPDATE dispatch_messages
        SET executed_at=COALESCE(executed_at, acknowledged_at),
            executed_by=COALESCE(executed_by, '历史演示账号')
        WHERE status='已执行' AND executed_at IS NULL
        """
    )
    connection.execute(
        """
        DELETE FROM dispatch_messages
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM dispatch_messages
            GROUP BY ticket_no, sender, receiver
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dispatch_request_key
        ON dispatch_messages(request_key)
        WHERE request_key IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_presence (
            agent_id TEXT PRIMARY KEY,
            level TEXT NOT NULL,
            last_seen REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cleared = connection.execute(
        "SELECT 1 FROM app_meta WHERE key=?",
        (CLEAR_DISPATCH_RECORDS_VERSION,),
    ).fetchone()
    if cleared is None:
        connection.execute("DELETE FROM dispatch_messages")
        connection.execute(
            "INSERT INTO app_meta(key, value) VALUES (?, ?)",
            (CLEAR_DISPATCH_RECORDS_VERSION, str(time.time())),
        )
    connection.commit()
    return connection


def touch_agent(agent_id: str, level: str) -> None:
    if AGENT_GATEWAY:
        AGENT_GATEWAY.heartbeat(agent_id, level)
        return
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO agent_presence(agent_id, level, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET level=excluded.level, last_seen=excluded.last_seen
            """,
            (agent_id, level, time.time()),
        )
        connection.commit()


def load_online_agents(max_age_seconds: int = 30) -> set[str]:
    if AGENT_GATEWAY:
        return AGENT_GATEWAY.online_agents(max_age_seconds)
    with connect_db() as connection:
        rows = connection.execute(
            "SELECT agent_id FROM agent_presence WHERE last_seen>=?",
            (time.time() - max_age_seconds,),
        ).fetchall()
    return {row[0] for row in rows}


def beijing_datetime(timestamp: float | None = None) -> datetime:
    if timestamp is None:
        return datetime.now(BEIJING_TZ)
    return datetime.fromtimestamp(timestamp, BEIJING_TZ)


def format_beijing_time(timestamp: float, pattern: str = "%m-%d %H:%M:%S") -> str:
    return beijing_datetime(timestamp).strftime(pattern)


def send_message(
    target_county: str,
    title: str,
    steps: str,
    sender: str = "黑龙江省调度中心",
    receiver: str = "哈尔滨市调度中心",
    request_key: str | None = None,
) -> str:
    review = ARK_AGENT.review_ticket(
        title=title,
        steps=steps,
        sender=sender,
        receiver=receiver,
        target_county=target_county,
    )
    title, steps = review.title, review.steps
    st.session_state["ark_last_review"] = {
        "used_ai": review.used_ai,
        "note": review.note,
    }
    if AGENT_GATEWAY:
        return AGENT_GATEWAY.create_ticket(
            {
                "target_county": target_county,
                "title": title,
                "steps": steps,
                "sender": sender,
                "receiver": receiver,
                "request_key": request_key,
            }
        )
    ticket_no = f"HLJ-{beijing_datetime().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    content = (
        f"{receiver}调度员，请执行以下操作票任务。{title}。"
        f"{steps.replace(chr(10), '；')}。"
        "操作完成后立即回令。"
    )
    with connect_db() as connection:
        if request_key:
            existing = connection.execute(
                "SELECT ticket_no FROM dispatch_messages WHERE request_key=? LIMIT 1",
                (request_key,),
            ).fetchone()
            if existing:
                return existing[0]
        connection.execute(
            """
            INSERT INTO dispatch_messages
            (id, created_at, sender, receiver, title, ticket_no, target_county, content, status, request_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                time.time(),
                sender,
                receiver,
                title,
                ticket_no,
                target_county,
                content,
                "已送达",
                request_key,
            ),
        )
        connection.commit()
    return ticket_no


def acknowledge_message(message_id: str) -> None:
    if AGENT_GATEWAY:
        AGENT_GATEWAY.acknowledge(message_id)
        return
    with connect_db() as connection:
        connection.execute(
            "UPDATE dispatch_messages SET status='已签收', acknowledged_at=? WHERE id=?",
            (time.time(), message_id),
        )
        connection.commit()


def execute_message(message_id: str, executed_by: str) -> None:
    if AGENT_GATEWAY:
        AGENT_GATEWAY.execute(message_id, executed_by)
        return
    with connect_db() as connection:
        ticket_row = connection.execute(
            "SELECT ticket_no FROM dispatch_messages WHERE id=?",
            (message_id,),
        ).fetchone()
        if ticket_row is None:
            return
        connection.execute(
            """
            UPDATE dispatch_messages
            SET status='已执行', acknowledged_at=?, executed_at=?, executed_by=?
            WHERE ticket_no=?
            """,
            (time.time(), time.time(), executed_by, ticket_row[0]),
        )
        connection.commit()


def forward_message_to_county(message: sqlite3.Row) -> None:
    if AGENT_GATEWAY:
        AGENT_GATEWAY.forward(message["id"])
        return
    county = message["target_county"]
    city_center = message["receiver"]
    city_name = city_center.removesuffix("调度中心")
    forwarded_content = message["content"].replace(
        f"{city_center}调度员",
        f"{county}调度员",
        1,
    )
    with connect_db() as connection:
        connection.execute(
            """
            INSERT INTO dispatch_messages
            (id, created_at, sender, receiver, title, ticket_no, target_county, content, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                time.time(),
                city_center,
                county_agent_id(city_name, county),
                message["title"],
                message["ticket_no"],
                county,
                forwarded_content,
                "已送达",
            ),
        )
        connection.execute(
            "UPDATE dispatch_messages SET status='已转发', acknowledged_at=? WHERE id=?",
            (time.time(), message["id"]),
        )
        connection.commit()


def load_messages() -> list[sqlite3.Row]:
    if AGENT_GATEWAY:
        return AGENT_GATEWAY.list_tickets()
    connection = connect_db()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM dispatch_messages ORDER BY created_at DESC"
    ).fetchall()
    connection.close()
    return rows


def load_messages_for(receiver: str) -> list[sqlite3.Row]:
    if AGENT_GATEWAY:
        return AGENT_GATEWAY.list_tickets(receiver=receiver)
    connection = connect_db()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT * FROM dispatch_messages
        WHERE receiver=?
        ORDER BY created_at DESC
        """,
        (receiver,),
    ).fetchall()
    connection.close()
    return rows


def load_city_messages(city_name: str) -> list[sqlite3.Row]:
    city_center = f"{city_name}调度中心"
    if AGENT_GATEWAY:
        return AGENT_GATEWAY.list_tickets(sender_or_receiver=city_center)
    connection = connect_db()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT * FROM dispatch_messages
        WHERE receiver=? OR sender=?
        ORDER BY created_at DESC
        """,
        (city_center, city_center),
    ).fetchall()
    connection.close()
    return rows


def load_today_dispatch_stats() -> tuple[int, str]:
    """Return today's real province-originated instruction count and delivery summary."""
    now = beijing_datetime()
    day_start = datetime.combine(now.date(), datetime_time.min, BEIJING_TZ).timestamp()
    if AGENT_GATEWAY:
        rows = [
            row for row in AGENT_GATEWAY.list_tickets()
            if row["sender"] == "黑龙江省调度中心" and row["created_at"] >= day_start
        ]
        total = len(rows)
        delivered = sum(
            1 for row in rows
            if row["status"] in {"已送达", "已签收", "已转发", "已执行"}
        )
    else:
        with connect_db() as connection:
            total, delivered = connection.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN status IN ('已送达', '已签收', '已转发', '已执行')
                             THEN 1 ELSE 0 END)
                FROM dispatch_messages
                WHERE sender='黑龙江省调度中心' AND created_at>=?
                """,
                (day_start,),
            ).fetchone()
    total = int(total or 0)
    delivered = int(delivered or 0)
    if total == 0:
        return 0, "暂无下发"
    return total, f"{round(delivered / total * 100)}% 送达"


def render_login() -> None:
    st.markdown(
        """
        <style>
        .login-shell{max-width:760px;margin:6vh auto 18px;display:block;
        min-height:285px;border-radius:18px;overflow:hidden;background:#fff;box-shadow:0 28px 80px rgba(18,71,59,.18)}
        .login-brand{padding:56px 48px;background:linear-gradient(145deg,#006451,#009b77);color:#fff;
        display:flex;flex-direction:column;justify-content:space-between}
        .login-logo{width:58px;height:58px;display:grid;place-items:center;border-radius:14px 28px;background:#fff;color:#008469;font-size:30px}
        .login-brand h1{margin:25px 0 12px;font-size:30px;line-height:1.35}.login-brand p{color:#d0ece5;line-height:1.8}
        .login-points{display:grid;gap:12px;color:#d8f1eb;font-size:13px}.login-points span:before{content:"✓";margin-right:9px;color:#8cf0d2}
        .login-side{display:none}
        .demo-accounts{max-width:1040px;margin:0 auto;padding:13px 18px;border:1px solid #cfe3de;border-radius:9px;background:#f7fbfa;color:#55776f;font-size:12px;text-align:center}
        </style>
        <div class="login-shell">
          <section class="login-brand">
            <div><div class="login-logo">⌁</div><h1>龙江电网<br>虚拟配网调度中心</h1><p>统一身份认证 · 分级权限控制 · 调度指令闭环</p></div>
            <div class="login-points"><span>省、市、区县三级工作台</span><span>账号权限自动识别</span><span>操作票与语音指令协同</span></div>
          </section>
          <section class="login-side"><h2>调度账号登录</h2><p>请输入已授权的调度账号和密码</p></section>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, form_col, _ = st.columns([1.15, 1, 1.15])
    with form_col:
        with st.form("dispatch_login"):
            username = st.text_input("调度账号", placeholder="请输入用户名")
            password = st.text_input("登录密码", type="password", placeholder="请输入密码")
            submitted = st.form_submit_button("安全登录", type="primary", use_container_width=True)
        if submitted:
            account = DEMO_ACCOUNTS.get(username.strip())
            if account and password == account["password"]:
                st.session_state["auth_role"] = account["role"]
                st.session_state["auth_name"] = account["name"]
                st.session_state["auth_county"] = account.get("county")
                st.session_state["auth_city"] = account.get("city")
                st.session_state["auth_username"] = username.strip()
                st.rerun()
            else:
                st.error("账号或密码错误，请核对后重试。")
    st.markdown(
        """
        <div class="demo-accounts">
          演示账号：省级 <b>hlj_province</b>　·　市级 <b>harbin_city</b>　·　
          区县级 <b>nangang_county</b>　　统一密码：<b>demo123</b><br>
          已配置 1 个省级、13 个市级、125 个区县级账号；市/区县账号采用“地区全拼 + 权限后缀”。
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_harbin_workspace() -> None:
    counties = ["南岗区", "道里区", "道外区", "香坊区", "平房区", "松北区", "呼兰区", "阿城区", "双城区", "依兰县", "方正县", "宾县", "巴彦县", "木兰县", "通河县", "延寿县", "尚志市", "五常市"]
    messages = load_messages_for("哈尔滨市调度中心")
    unread = sum(1 for item in messages if item["status"] == "已送达")
    forwarded = sum(1 for item in messages if item["status"] == "已转发")

    st.markdown(
        """
        <div class="workspace-brand">
          <b>龙江电网 · 哈尔滨市虚拟配网调度中心</b>
          <small>哈尔滨市级调度账号　·　指令接收与区县转发工作台</small>
          <a class="workspace-logout" href="/" target="_self">退出账号</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    @st.dialog("向下属区县新建操作票", width="large")
    def downstream_dialog() -> None:
        downstream_county = st.selectbox("接收区县", counties, key="downstream_county")
        downstream_title = st.text_input("调度任务", value="区域配网运行方式调整", key="downstream_title")
        downstream_steps = st.text_area(
            "操作步骤（可修改）",
            value="第一项，核对当前线路运行状态。\n第二项，执行指定开关操作。\n第三项，复核并向哈尔滨市调回令。",
            height=130,
            key="downstream_steps",
        )
        if st.button(f"下发至{downstream_county}智能体", type="primary", use_container_width=True):
            ticket = send_message(
                downstream_county, downstream_title, downstream_steps,
                sender="哈尔滨市调度中心", receiver=f"{downstream_county}调度智能体",
            )
            ark_note = st.session_state.get("ark_last_review", {}).get("note", "")
            st.toast(f"操作票 {ticket} 已下发。{ark_note}", icon="✅")

    stats, action = st.columns([6, 1], vertical_alignment="center")
    with stats:
        st.markdown(
            f"""
            <div style="display:flex;height:76px;align-items:center;background:#fff;border-bottom:1px solid #d6e5e1">
              <div style="padding:0 28px"><small style="color:#68847d">市级视角</small><b style="display:block;font-size:19px">哈尔滨市调度中心</b></div>
              <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">区县智能体</small><b style="display:block;color:#007f66;font-size:22px">18 <i style="font-size:10px">在线</i></b></div>
              <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">待签收</small><b style="display:block;color:#d58a18;font-size:22px">{unread}</b></div>
              <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">已转发</small><b style="display:block;color:#007f66;font-size:22px">{forwarded}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with action:
        if st.button("＋ 新建操作票", type="primary", use_container_width=True):
            downstream_dialog()

    left, center, right = st.columns([1.15, 3.2, 1.25], gap="small")
    with left:
        st.markdown("#### 区县智能体")
        st.caption("18 / 18 在线 · 独立链路")
        county_html = "".join(
            f'<div style="padding:8px 10px;margin:5px 0;border:1px solid #d6e5e1;border-radius:6px;background:#fff"><b style="color:#007f66">区</b>　{county}<span style="float:right;color:#00a779;font-size:11px">● 在线</span></div>'
            for county in counties
        )
        st.markdown(f'<div style="max-height:590px;overflow:auto">{county_html}</div>', unsafe_allow_html=True)
    with right:
        st.markdown("#### 当前链路")
        st.markdown(
            """
            <div style="padding:16px;border:1px solid #d6e5e1;background:#fff;border-radius:7px;line-height:2">
              <b>黑龙江省调度中心</b><br><span style="color:#00a779">↓ 省市专线</span><br>
              <b>哈尔滨市调度中心</b><br><span style="color:#00a779">↓ 区县独立链路</span><br>
              <b>目标区县智能体</b>
            </div>
            <div style="margin-top:12px;padding:14px;background:#e9f6f2;border-radius:7px;color:#00745e">
              ● 加密通信正常<br>● 自动接收已开启<br>● 语音服务可用
            </div>
            """,
            unsafe_allow_html=True,
        )
    with center:
        st.markdown("#### 省调下发指令")
        st.caption("新指令按时间置顶 · 卡片内完成试听、签收与转发")
        if not messages:
            st.info("正在监听省级调度中心，暂无待接收指令。")
        for message in messages:
            is_new = message["status"] == "已送达"
            created = format_beijing_time(message["created_at"])
            border = "#00a779" if is_new else "#cfe3de"
            with st.container(border=True):
                st.markdown(
                    f"**{message['title']}**　　<span style='color:#78928b;font-size:12px'>{created}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(f"{message['sender']} → 哈尔滨市调度中心 → {message['target_county']}智能体")
                st.markdown(
                    f"<div style='padding:10px 12px;border-left:3px solid {border};background:#f4f8f7;font-size:13px'>{message['content']}</div>",
                    unsafe_allow_html=True,
                )
                st.caption(f"操作票号：{message['ticket_no']}　·　状态：{message['status']}")
                play, stop, process, spacer = st.columns([1, 1, 1.4, 2])
                with play:
                    if st.button("▶ 试听", key=f"voice-{message['id']}", use_container_width=True):
                        voice_text = json.dumps(message["content"], ensure_ascii=False)
                        components.html(f"<script>speechSynthesis.cancel();const u=new SpeechSynthesisUtterance({voice_text});u.lang='zh-CN';u.rate=.88;speechSynthesis.speak(u)</script>", height=0)
                with stop:
                    if st.button("■ 停止", key=f"stop-{message['id']}", use_container_width=True):
                        components.html("<script>speechSynthesis.cancel()</script>", height=0)
                with process:
                    if is_new and st.button("签收指令", key=f"ack-{message['id']}", type="primary", use_container_width=True):
                        acknowledge_message(message["id"]); st.rerun()
                    elif message["status"] == "已签收" and st.button(f"转发至{message['target_county']}", key=f"forward-{message['id']}", type="primary", use_container_width=True):
                        forward_message_to_county(message); st.rerun()


def render_city_dashboard(city_name: str, counties: list[str], username: str) -> None:
    city_center = f"{city_name}调度中心"
    touch_agent(city_center, "city")
    if success_message := st.session_state.pop("city_dispatch_success", None):
        st.toast(success_message, icon="✅")
    online_agents = load_online_agents()
    online_counties = [
        county for county in counties if county_agent_id(city_name, county) in online_agents
    ]
    rows = load_city_messages(city_name)
    message_data = [
        {
            "id": row["id"], "title": row["title"], "sender": row["sender"],
            "receiver": row["receiver"],
            "county": row["target_county"], "ticket": row["ticket_no"],
            "content": row["content"], "status": row["status"],
            "direction": "outgoing" if row["sender"] == city_center else "incoming",
            "time": format_beijing_time(row["created_at"]),
            "createdAt": int(row["created_at"] * 1000),
            "executedAt": format_beijing_time(row["executed_at"]) if row["executed_at"] else "",
            "executedBy": row["executed_by"] or "",
        }
        for row in rows
    ]
    unread = sum(
        1 for row in rows
        if row["receiver"] == city_center and row["status"] == "已送达"
    )
    forwarded = sum(1 for row in rows if row["sender"] == city_center)
    html = dedent(
        r"""
        <!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
        :root{--green:#007f66;--bright:#00a779;--text:#193a33;--muted:#708a84;--line:#d6e5e1;--bg:#eef5f3}
        *{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:"Microsoft YaHei UI","PingFang SC",sans-serif;overflow:hidden}
        button{font:inherit}.app{height:930px;display:flex;flex-direction:column}.top{height:70px;padding:0 28px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(105deg,#006c58,#169b79);color:#fff}.brand b{font-size:18px;letter-spacing:2px}.brand small{display:block;margin-top:5px;color:#ccebe3;font-size:8px;letter-spacing:2px}.logout{padding:8px 15px;border:1px solid #ffffff88;border-radius:6px;color:#fff;text-decoration:none;font-size:10px;background:#ffffff12;cursor:pointer}
        .bar{height:82px;padding:0 28px;display:flex;align-items:center;background:#fff;border-bottom:1px solid var(--line)}.title{min-width:230px}.title small{display:block;color:var(--green);font-size:8px;letter-spacing:2px}.title b{font-size:17px}.stats{display:flex;flex:1}.stat{min-width:135px;padding:0 25px;border-left:1px solid var(--line)}.stat span{display:block;color:var(--muted);font-size:8px}.stat b{font-size:21px;color:var(--green)}.stat em{font-size:8px;color:var(--bright);font-style:normal;margin-left:5px}.new{height:40px;padding:0 18px;border:0;border-radius:7px;background:linear-gradient(105deg,var(--green),var(--bright));color:#fff;font-size:10px;font-weight:700;cursor:pointer;box-shadow:0 5px 14px #007f6630}
        .work{flex:1;display:grid;grid-template-columns:260px minmax(550px,1fr) 290px;gap:11px;padding:11px 15px;min-height:0}.panel{background:#fff;border:1px solid var(--line);overflow:hidden;box-shadow:0 4px 14px #1c4a400f}.ph{height:46px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;background:#f7fbfa;border-bottom:1px solid var(--line);font-size:11px;font-weight:700}.ph small{font-size:8px;color:var(--bright);font-weight:400}
        .countyList{height:655px;padding:7px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#8fc9ba #edf5f3}.county{height:52px;margin-bottom:4px;padding:7px 9px;display:grid;grid-template-columns:34px 1fr auto;align-items:center;gap:8px;border:1px solid transparent;border-radius:5px}.county:hover,.county.active{background:#e7f5f1;border-color:#a8d8cb}.avatar{width:31px;height:31px;display:grid;place-items:center;border:1px solid #77bdaa;border-radius:50%;background:#e7f5f1;color:var(--green);font-weight:700}.county b{font-size:10px}.county small{display:block;color:#78918b;font-size:7px}.online{color:var(--bright);font-size:7px}
        .recordTabs{display:flex;gap:5px}.recordTabs button{padding:5px 9px;border:1px solid #b9d8d0;border-radius:4px;background:#fff;color:#55766f;font-size:8px;cursor:pointer}.recordTabs button.active{border-color:var(--green);background:var(--green);color:#fff}.localFilters{padding:7px 10px;display:grid;grid-template-columns:1fr 1fr auto;gap:7px;align-items:end;border-bottom:1px solid var(--line);background:#f7fbfa}.localFilters label{color:var(--muted);font-size:8px}.localFilters select{display:block;width:100%;height:28px;margin-top:3px;border:1px solid #bddbd3;background:#fff;color:var(--text);font-size:8px}.filterCount{padding-bottom:6px;color:var(--green);font-size:8px;white-space:nowrap}.inbox{height:650px;overflow-y:auto;padding:10px;scrollbar-width:thin;scrollbar-color:#8fc9ba #edf5f3}.notice{padding:10px 12px;margin-bottom:8px;border-radius:5px;background:#e6f5f0;color:var(--green);font-size:9px}.empty{padding:45px;text-align:center;color:var(--muted);font-size:10px}.card{padding:13px;margin-bottom:9px;border:1px solid var(--line);border-left:4px solid #a9cfc5;background:#fff}.card.newmsg{border-left-color:var(--bright);box-shadow:0 4px 13px #007f6615}.head{display:flex;justify-content:space-between}.head b{font-size:11px}.head span,.route{color:var(--muted);font-size:8px}.route{margin:6px 0}.body{padding:9px 10px;background:#f5f9f8;border-left:2px solid #9acbbf;font-size:9px;line-height:1.7}.meta{margin-top:7px;color:var(--muted);font-size:8px}.actions{display:flex;gap:6px;margin-top:9px}.actions button{height:30px;padding:0 11px;border:1px solid #b9d8d0;border-radius:5px;background:#fff;color:#286356;font-size:8px;cursor:pointer}.actions button.primary{border:0;background:var(--green);color:#fff}.actions button:hover{border-color:var(--bright)}
        .chain{margin:11px;padding:14px;border:1px solid var(--line);background:#f8fbfa}.node{display:flex;gap:9px;align-items:center}.ico{width:29px;height:29px;display:grid;place-items:center;border:1px solid #86c5b5;border-radius:50%;background:#e4f5f0;color:var(--green);font-size:8px}.node small{display:block;color:var(--muted);font-size:7px}.node b{font-size:9px}.flow{height:29px;margin-left:14px;border-left:1px dashed var(--bright);padding:9px;color:var(--muted);font-size:7px}.health{margin:11px;padding:12px;background:#e9f6f2;color:var(--green);font-size:8px;line-height:2}
        .back{display:none;position:fixed;z-index:20;inset:0;background:#123d3466;place-items:center}.back.show{display:grid}.modal{width:min(650px,calc(100vw - 30px));padding:20px;border-radius:10px;background:#fff;box-shadow:0 24px 70px #103f363d}.mh{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:12px}.close{border:0;background:none;font-size:22px}.field{margin-top:12px}.field label{display:block;margin-bottom:5px;color:var(--muted);font-size:9px}.field select,.field input,.field textarea{width:100%;padding:9px;border:1px solid #bddbd3;color:var(--text);background:#fff}.field textarea{height:110px;resize:vertical}.send{width:100%;height:38px;margin-top:13px;border:0;border-radius:6px;background:var(--green);color:#fff;cursor:pointer}
        .foot{height:28px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;background:#f7fbfa;border-top:1px solid var(--line);color:var(--muted);font-size:7px}
        /* 字体仅按业务层级定点放大，头像与节点图标保持原尺寸 */
        .brand b{font-size:22px}.brand small{font-size:10px}.title small{font-size:11px}.title b{font-size:19px}.stat span{font-size:11px}.stat b{font-size:21px}.stat em{font-size:10px}.ph{font-size:14px}.ph small{font-size:10px}.county b{font-size:13px}.county small,.online{font-size:10px}.head b{font-size:13px}.head span,.route,.meta{font-size:10px}.body{font-size:11px}.actions button{font-size:10px}.node b{font-size:12px}.node small,.flow,.health{font-size:9px}
        </style></head><body><div class="app">
        <header class="top"><div class="brand"><b>龙江电网 · 哈尔滨市虚拟配网调度中心</b><small>HARBIN VIRTUAL DISPATCH NETWORK · 当前账号：harbin_city（市级调度权限）</small></div><button class="logout" onclick="post({action:'logout'})">退出账号</button></header>
        <section class="bar"><div class="title"><small>市域态势</small><b>哈尔滨市调度中心视角</b></div><div class="stats"><div class="stat"><span>区县智能体</span><b>__COUNTY_ONLINE__</b><em>/ __COUNTY_TOTAL__ 在线</em></div><div class="stat"><span>待签收</span><b>__UNREAD__</b><em>实时接收</em></div><div class="stat"><span>已转发</span><b>__FORWARDED__</b><em>区县链路</em></div></div><button class="new" onclick="openModal()">＋ 新建操作票</button></section>
        <section class="work">
          <aside class="panel"><div class="ph"><span>区县调度</span><small>__COUNTY_ONLINE__ / __COUNTY_TOTAL__ 在线</small></div><div class="countyList" id="countyList"></div></aside>
          <main class="panel"><div class="ph"><span>调度操作票记录</span><div class="recordTabs"><button id="incomingTab" class="active" onclick="setRecordType('incoming')">省调接收</button><button id="outgoingTab" onclick="setRecordType('outgoing')">市调下发</button></div></div><div class="localFilters"><label>时间范围<select id="cityTimeFilter" onchange="renderInbox()"><option value="">全部时间</option><option value="today">今天</option><option value="7d">近7天</option><option value="30d">近30天</option></select></label><label>执行状态<select id="cityStatusFilter" onchange="renderInbox()"><option value="">全部状态</option><option>已送达</option><option>已签收</option><option>已转发</option><option>已执行</option></select></label><span class="filterCount" id="cityFilterCount"></span></div><div class="inbox" id="inbox"></div></main>
          <aside class="panel"><div class="ph"><span>当前链路</span><small>● 加密通信</small></div><div class="chain"><div class="node"><span class="ico">省</span><div><small>上级指令源</small><b>黑龙江省调度中心</b></div></div><div class="flow">省市专线</div><div class="node"><span class="ico">哈</span><div><small>当前接收</small><b>哈尔滨市调度中心</b></div></div><div class="flow">区县独立链路</div><div class="node"><span class="ico">区</span><div><small>转发目标</small><b id="chainCounty">南岗区智能体</b></div></div></div><div class="health">● 加密通信正常<br>● 自动接收已开启<br>● 语音服务可用</div></aside>
        </section><footer class="foot"><span>● 市级知识底座同步正常</span><span>通信延迟 26ms · STREAMLIT DEMO</span></footer></div>
        <div class="back" id="modal"><section class="modal"><div class="mh"><div><b>向下属区县新建操作票</b><small style="display:block;color:#708a84;margin-top:4px">发布地区按当前选中区县自动填充</small></div><button class="close" onclick="closeModal()">×</button></div><div class="field"><label>接收区县</label><select id="targetCounty"></select></div><div class="field"><label>调度任务</label><input id="taskTitle" value="区域配网运行方式调整"></div><div class="field"><label>操作步骤</label><textarea id="taskSteps">第一项，核对当前线路运行状态。
第二项，执行指定开关操作。
第三项，复核并向哈尔滨市调回令。</textarea></div><button class="send" onclick="sendDownstream()">确认并下发操作票</button></section></div>
        <script>
        const counties=__COUNTIES__,onlineCounties=new Set(__ONLINE_COUNTIES__),messages=__MESSAGES__;let selectedCounty="",recordType=__INITIAL_RECORD_TYPE__,speaking=-1;
        const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
        function post(data){window.parent.postMessage({type:"networkTarget",nonce:Date.now(),...data},"*")}
        function raiseFonts(){}
        function renderCounties(){document.getElementById("countyList").innerHTML=counties.map(c=>{const on=onlineCounties.has(c);return `<div class="county ${c===selectedCounty?"active":""}" onclick="selectCounty('${c}')"><span class="avatar">区</span><span><b>${c}</b><small>独立调度智能体</small></span><i class="online" style="color:${on?"#00a779":"#a0ada9"}">● ${on?"在线":"离线"}</i></div>`}).join("");raiseFonts()}
        function selectCounty(c){selectedCounty=selectedCounty===c?"":c;document.getElementById("chainCounty").textContent=selectedCounty?selectedCounty+"智能体":"目标区县智能体";renderCounties();renderInbox()}
        function setRecordType(type){recordType=type;document.getElementById("incomingTab").classList.toggle("active",type==="incoming");document.getElementById("outgoingTab").classList.toggle("active",type==="outgoing");renderInbox()}
        function filterCutoff(value){const now=Date.now();if(value==="7d")return now-7*86400000;if(value==="30d")return now-30*86400000;if(value==="today"){const p=Object.fromEntries(new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date()).map(x=>[x.type,x.value]));return Date.parse(`${p.year}-${p.month}-${p.day}T00:00:00+08:00`)}return 0}
        function renderInbox(){const box=document.getElementById("inbox"),timeValue=document.getElementById("cityTimeFilter").value,statusValue=document.getElementById("cityStatusFilter").value,cutoff=filterCutoff(timeValue),visible=messages.filter(m=>m.direction===recordType&&(!selectedCounty||m.county===selectedCounty)&&(!statusValue||m.status===statusValue)&&(!cutoff||m.createdAt>=cutoff)),newCount=visible.filter(m=>m.direction==="incoming"&&m.status==="已送达").length;document.getElementById("cityFilterCount").textContent=`${visible.length} 条`;box.innerHTML=(newCount?`<div class="notice">${selectedCounty||"全部区县"}有 ${newCount} 条新调度指令，请及时签收并转发。</div>`:"")+(visible.length?visible.map(m=>{const i=messages.indexOf(m),incoming=m.direction==="incoming",route=incoming?`${esc(m.sender)} → 哈尔滨市调度中心 → ${esc(m.county)}智能体`:`哈尔滨市调度中心 → ${esc(m.county)}调度智能体`;return `<article class="card ${incoming&&m.status==="已送达"?"newmsg":""}"><div class="head"><b>${esc(m.title)}</b><span>${m.time}</span></div><div class="route">${route}</div><div class="body">${esc(m.content)}</div><div class="meta">操作票号：${esc(m.ticket)}　·　状态：${esc(m.status)}${m.executedAt?`<br>执行时间：${esc(m.executedAt)}　·　操作账号：${esc(m.executedBy)}`:""}</div><div class="actions"><button onclick="speak(${i})">${speaking===i?"■ 停止":"▶ 试听"}</button>${incoming&&m.status==="已送达"?`<button class="primary" onclick="post({action:'ack',id:'${m.id}'})">签收指令</button>`:""}${incoming&&m.status==="已签收"?`<button class="primary" onclick="post({action:'forward',id:'${m.id}'})">转发至${esc(m.county)}</button>`:""}</div></article>`}).join(""):`<div class="empty">${selectedCounty?selectedCounty:"当前"}暂无${recordType==="incoming"?"省调接收":"市调下发"}操作票记录。</div>`);raiseFonts()}
        function speak(i){if(speaking===i){speechSynthesis.cancel();speaking=-1;renderInbox();return}speechSynthesis.cancel();speaking=i;renderInbox();const u=new SpeechSynthesisUtterance(messages[i].content);u.lang="zh-CN";u.rate=.88;u.onend=u.onerror=()=>{speaking=-1;renderInbox()};speechSynthesis.speak(u)}
        function openModal(){const target=selectedCounty||"南岗区";document.getElementById("targetCounty").innerHTML=counties.map(c=>`<option ${c===target?"selected":""}>${c}</option>`).join("");document.getElementById("modal").classList.add("show")}function closeModal(){document.getElementById("modal").classList.remove("show")}
        function sendDownstream(){const button=document.querySelector(".modal .send");if(button.disabled)return;button.disabled=true;button.textContent="正在下发…";const payload={action:"downstream",county:document.getElementById("targetCounty").value,title:document.getElementById("taskTitle").value,steps:document.getElementById("taskSteps").value};closeModal();post(payload)}
        renderCounties();setRecordType(recordType);raiseFonts();
        setInterval(()=>{if(speaking<0&&!document.getElementById("modal").classList.contains("show"))post({action:"refresh"})},5000);
        </script></body></html>
        """
    ).replace("哈尔滨市", city_name).replace("南岗区", counties[0]).replace(
        '<span class="ico">哈</span>', f'<span class="ico">{city_name[0]}</span>'
    ).replace(
        "HARBIN", CITY_SLUGS[city_name].upper()
    ).replace("harbin_city", username).replace(
        "__COUNTIES__", json.dumps(counties, ensure_ascii=False)
    ).replace(
        "__ONLINE_COUNTIES__", json.dumps(online_counties, ensure_ascii=False)
    ).replace(
        "__MESSAGES__", json.dumps(message_data, ensure_ascii=False)
    ).replace(
        "__INITIAL_RECORD_TYPE__",
        json.dumps(st.session_state.pop("city_initial_record_type", "incoming")),
    ).replace("__COUNTY_ONLINE__", str(len(online_counties))).replace(
        "__COUNTY_TOTAL__", str(len(counties))
    ).replace("__UNREAD__", str(unread)
    ).replace("__FORWARDED__", str(forwarded))
    result = network_component(html=html, height=930, key="harbin_dashboard", default=None)
    if isinstance(result, dict):
        nonce = result.get("nonce")
        if nonce != st.session_state.get("harbin_action_nonce"):
            st.session_state["harbin_action_nonce"] = nonce
            action = result.get("action")
            if action == "logout":
                for key in ("auth_role", "auth_name", "auth_county", "auth_city", "auth_username"):
                    st.session_state.pop(key, None)
                st.query_params.clear()
                st.rerun()
            elif action == "refresh":
                st.rerun()
            elif action in {"ack", "forward"}:
                message = next((row for row in rows if row["id"] == result.get("id")), None)
                if message is not None:
                    acknowledge_message(message["id"]) if action == "ack" else forward_message_to_county(message)
                    st.rerun()
            elif action == "downstream" and result.get("county") in counties:
                ticket = send_message(
                    result["county"], result.get("title") or "区域配网运行方式调整",
                    result.get("steps") or "核对线路状态并执行操作。",
                    sender=city_center, receiver=county_agent_id(city_name, result["county"]),
                    request_key=f"downstream:{nonce}",
                )
                st.session_state["city_initial_record_type"] = "outgoing"
                st.session_state["city_dispatch_success"] = (
                    f"操作票 {ticket} 已保存并下发至{result['county']}。"
                )
                st.rerun()


def render_county_workspace(county: str) -> None:
    touch_agent(f"{county}调度智能体", "county")
    @st.fragment(run_every="60s")
    def keep_county_online() -> None:
        touch_agent(f"{county}调度智能体", "county")
    keep_county_online()
    messages = load_messages_for(f"{county}调度智能体")
    st.markdown(
        f"""
        <div class="workspace-brand">
          <b>龙江电网 · {county}虚拟配网调度中心</b>
          <small>{county}县级调度账号　·　操作票接收与执行工作台</small>
          <a class="workspace-logout" href="/?logout=1" target="_self">退出账号</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
    total = len(messages)
    pending = sum(1 for row in messages if row["status"] == "已送达")
    st.markdown(
        f"""
        <div style="display:flex;height:82px;align-items:center;background:#fff;border-bottom:1px solid #d6e5e1">
          <div style="padding:0 28px"><small style="color:#68847d">区县视角</small><b style="display:block;font-size:19px">{county}调度中心</b></div>
          <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">接收指令</small><b style="display:block;color:#007f66;font-size:22px">{total}</b></div>
          <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">待执行</small><b style="display:block;color:#d58a18;font-size:22px">{pending}</b></div>
          <div style="padding:0 28px;border-left:1px solid #d6e5e1"><small style="color:#68847d">链路状态</small><b style="display:block;color:#00a779;font-size:15px">● 在线</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, center, right = st.columns([1.1, 3.1, 1.2], gap="small")
    with left:
        st.markdown("#### 上级组织")
        st.info("黑龙江省调度中心\n\n↓\n\n哈尔滨市调度中心")
    with right:
        st.markdown("#### 当前链路")
        st.success(f"哈尔滨市调度中心\n\n↓ 区县独立链路\n\n{county}调度智能体")
    with center:
        st.markdown("#### 市调下发操作票")
        if not messages:
            st.info("当前暂无下发至本区县的操作票。")
        for message in messages:
            with st.container(border=True):
                st.markdown(f"**{message['title']}**")
                st.caption(f"操作票号：{message['ticket_no']}　·　状态：{message['status']}")
                st.markdown(message["content"])
                if message["status"] == "已送达" and st.button(
                    "签收并进入执行", key=f"county-ack-{message['id']}", type="primary"
                ):
                    acknowledge_message(message["id"])
                    st.rerun()


def render_county_dashboard(county: str, city_name: str, username: str) -> None:
    agent_id = county_agent_id(city_name, county)
    touch_agent(agent_id, "county")
    rows = load_messages_for(agent_id)
    messages = [
        {
            "id": row["id"], "title": row["title"], "ticket": row["ticket_no"],
            "content": row["content"], "status": row["status"],
            "time": format_beijing_time(row["created_at"]),
            "createdAt": int(row["created_at"] * 1000),
            "executedAt": format_beijing_time(row["executed_at"]) if row["executed_at"] else "",
            "executedBy": row["executed_by"] or "",
        }
        for row in rows
    ]
    pending = sum(1 for row in rows if row["status"] == "已送达")
    executed = sum(1 for row in rows if row["status"] == "已执行")
    html = dedent(
        r"""
        <!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
        :root{--g:#007f66;--b:#00a779;--t:#193a33;--m:#708a84;--l:#d6e5e1;--bg:#eef5f3}*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--t);font-family:"Microsoft YaHei UI","PingFang SC",sans-serif;overflow:hidden}button{font:inherit}.app{height:930px;display:flex;flex-direction:column}.top{height:70px;padding:0 28px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(105deg,#006c58,#169b79);color:#fff}.brand b{font-size:20px;letter-spacing:2px}.brand small{display:block;margin-top:5px;color:#ccebe3;font-size:10px;letter-spacing:2px}.logout{padding:8px 15px;border:1px solid #ffffff88;border-radius:6px;color:#fff;background:#ffffff12;cursor:pointer}.bar{height:82px;padding:0 28px;display:flex;align-items:center;background:#fff;border-bottom:1px solid var(--l)}.title{min-width:230px}.title small{display:block;color:var(--g);font-size:10px;letter-spacing:2px}.title b{font-size:19px}.stats{display:flex;flex:1}.stat{min-width:145px;padding:0 25px;border-left:1px solid var(--l)}.stat span{display:block;color:var(--m);font-size:10px}.stat b{font-size:22px;color:var(--g)}.stat em{font-size:10px;color:var(--b);font-style:normal;margin-left:5px}.work{flex:1;display:grid;grid-template-columns:260px minmax(550px,1fr) 290px;gap:11px;padding:11px 15px;min-height:0}.panel{background:#fff;border:1px solid var(--l);overflow:hidden;box-shadow:0 4px 14px #1c4a400f}.ph{height:46px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;background:#f7fbfa;border-bottom:1px solid var(--l);font-size:13px;font-weight:700}.ph small{font-size:10px;color:var(--b);font-weight:400}
        .assetList{height:655px;padding:12px;overflow:hidden}.ico{width:31px;height:31px;display:grid;place-items:center;border:1px solid #77bdaa;border-radius:50%;background:#e7f5f1;color:var(--g);font-weight:700}.agentProfile{padding:18px 12px;border:1px solid #9ed2c4;border-radius:8px;background:linear-gradient(145deg,#e7f5f1,#fff);text-align:center}.agentAvatar{width:74px;height:74px;margin:0 auto 10px;display:grid;place-items:center;border:2px solid var(--b);border-radius:50%;background:linear-gradient(145deg,#009878,#006e59);color:#fff;font-size:20px;font-weight:700;box-shadow:0 8px 22px #007f6630}.agentProfile b{display:block;font-size:14px}.agentProfile small{display:block;margin-top:5px;color:var(--m);font-size:10px}.agentOnline{display:inline-block;margin-top:10px;padding:4px 10px;border-radius:12px;background:#dff4ed;color:var(--g);font-size:10px}.sectionTitle{margin:18px 2px 8px;color:var(--m);font-size:10px;letter-spacing:1px}.capability{height:43px;margin-bottom:6px;padding:0 10px;display:flex;align-items:center;justify-content:space-between;border:1px solid var(--l);border-radius:6px;background:#fafcfc;font-size:11px}.capability i{color:var(--b);font-size:9px;font-style:normal}.runtime{padding:11px;border-radius:6px;background:#edf7f4;color:#426e63;font-size:9px;line-height:2}.inbox{height:701px;overflow-y:auto;padding:10px}.notice{padding:10px 12px;margin-bottom:8px;border-radius:5px;background:#e6f5f0;color:var(--g);font-size:10px}.empty{padding:45px;text-align:center;color:var(--m);font-size:11px}.card{padding:13px;margin-bottom:9px;border:1px solid var(--l);border-left:4px solid #a9cfc5;background:#fff}.card.newmsg{border-left-color:var(--b)}.head{display:flex;justify-content:space-between}.head b{font-size:13px}.head span,.route,.meta{color:var(--m);font-size:10px}.route{margin:6px 0}.body{padding:10px;background:#f5f9f8;border-left:2px solid #9acbbf;font-size:11px;line-height:1.75}.meta{margin-top:7px}.actions{display:flex;gap:6px;margin-top:9px}.actions button{height:31px;padding:0 12px;border:1px solid #b9d8d0;border-radius:5px;background:#fff;color:#286356;font-size:10px;cursor:pointer}.actions .primary{border:0;background:var(--g);color:#fff}.chain{margin:11px;padding:14px;border:1px solid var(--l);background:#f8fbfa}.node{display:flex;gap:9px;align-items:center}.node small{display:block;color:var(--m);font-size:9px}.node b{font-size:12px}.flow{height:30px;margin-left:15px;border-left:1px dashed var(--b);padding:9px;color:var(--m);font-size:9px}.health{margin:11px;padding:12px;background:#e9f6f2;color:var(--g);font-size:9px;line-height:2}.foot{height:28px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;background:#f7fbfa;border-top:1px solid var(--l);color:var(--m);font-size:9px}
        .brand b{font-size:22px}.title small{font-size:11px}.title b{font-size:19px}.stat span{font-size:11px}.stat b{font-size:21px}.stat em{font-size:10px}.ph{font-size:14px}.ph small{font-size:10px}.countyFilters{height:51px;padding:6px 10px;display:grid;grid-template-columns:1fr 1fr auto;gap:7px;align-items:end;border-bottom:1px solid var(--l);background:#f7fbfa}.countyFilters label{color:var(--m);font-size:8px}.countyFilters select{display:block;width:100%;height:27px;margin-top:3px;border:1px solid #bddbd3;background:#fff;color:var(--t);font-size:8px}.countyFilterCount{padding-bottom:6px;color:var(--g);font-size:8px;white-space:nowrap}.countyFilters+.inbox{height:650px}
        </style></head><body><div class="app"><header class="top"><div class="brand"><b>龙江电网 · __COUNTY__虚拟配网调度中心</b><small>COUNTY VIRTUAL DISPATCH NETWORK · 当前账号：nangang_county（区县级执行权限）</small></div><button class="logout" onclick="post({action:'logout'})">退出账号</button></header>
        <section class="bar"><div class="title"><small>区县态势</small><b>__COUNTY__调度中心视角</b></div><div class="stats"><div class="stat"><span>区县智能体</span><b>1</b><em>在线</em></div><div class="stat"><span>待执行</span><b>__PENDING__</b><em>操作票</em></div><div class="stat"><span>已执行</span><b>__EXECUTED__</b><em>完成回令</em></div></div></section>
        <section class="work"><aside class="panel"><div class="ph"><span>区县智能体</span><small>1 / 1 在线</small></div><div class="assetList"><div class="agentProfile"><div class="agentAvatar">南岗</div><b>__COUNTY__调度智能体</b><small>账号：nangang_county</small><span class="agentOnline">● 当前在线</span></div><div class="sectionTitle">核心能力</div><div class="capability"><span>调度指令接收</span><i>正常</i></div><div class="capability"><span>操作票解析</span><i>正常</i></div><div class="capability"><span>AI 语音播报</span><i>可用</i></div><div class="capability"><span>执行回令</span><i>畅通</i></div><div class="sectionTitle">运行信息</div><div class="runtime">知识底座：已同步<br>在线判定窗口：30 秒<br>链路加密：已启用<br>权限级别：区县级执行</div></div></aside><main class="panel"><div class="ph"><span>市调下发操作票</span><small>历史记录筛选</small></div><div class="countyFilters"><label>时间范围<select id="countyTimeFilter" onchange="render()"><option value="">全部时间</option><option value="today">今天</option><option value="7d">近7天</option><option value="30d">近30天</option></select></label><label>执行状态<select id="countyStatusFilter" onchange="render()"><option value="">全部状态</option><option>已送达</option><option>已签收</option><option>已执行</option></select></label><span class="countyFilterCount" id="countyFilterCount"></span></div><div class="inbox" id="inbox"></div></main><aside class="panel"><div class="ph"><span>当前链路</span><small>● 加密通信</small></div><div class="chain"><div class="node"><span class="ico">省</span><div><small>上级源头</small><b>黑龙江省调度中心</b></div></div><div class="flow">省市专线</div><div class="node"><span class="ico">哈</span><div><small>市级转发</small><b>哈尔滨市调度中心</b></div></div><div class="flow">区县独立链路</div><div class="node"><span class="ico">区</span><div><small>当前接收</small><b>__COUNTY__调度智能体</b></div></div></div><div class="health">● 区县链路在线<br>● 操作票自动接收<br>● 语音服务可用<br>● 执行回令通道正常</div></aside></section><footer class="foot"><span>● __COUNTY__知识底座同步正常</span><span>北京时间 · STREAMLIT DEMO</span></footer></div>
        <script>const messages=__MESSAGES__;let speaking=-1,visibleMessages=[...messages];const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));function post(data){window.parent.postMessage({type:"networkTarget",nonce:Date.now(),...data},"*")}function cutoff(value){const now=Date.now();if(value==="7d")return now-7*86400000;if(value==="30d")return now-30*86400000;if(value==="today"){const p=Object.fromEntries(new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date()).map(x=>[x.type,x.value]));return Date.parse(`${p.year}-${p.month}-${p.day}T00:00:00+08:00`)}return 0}function render(){const box=document.getElementById("inbox"),timeValue=document.getElementById("countyTimeFilter").value,statusValue=document.getElementById("countyStatusFilter").value,start=cutoff(timeValue);visibleMessages=messages.filter(m=>(!statusValue||m.status===statusValue)&&(!start||m.createdAt>=start));document.getElementById("countyFilterCount").textContent=`${visibleMessages.length} 条`;const pending=visibleMessages.filter(m=>m.status==="已送达").length;box.innerHTML=(pending?`<div class="notice">筛选结果中有 ${pending} 条待执行操作票，请及时处理并回令。</div>`:"")+(visibleMessages.length?visibleMessages.map((m,i)=>`<article class="card ${m.status==="已送达"?"newmsg":""}"><div class="head"><b>${esc(m.title)}</b><span>${m.time}</span></div><div class="route">哈尔滨市调度中心 → __COUNTY__调度智能体</div><div class="body">${esc(m.content)}</div><div class="meta">操作票号：${esc(m.ticket)}　·　状态：${esc(m.status)}${m.executedAt?`<br>执行时间：${esc(m.executedAt)}　·　操作账号：${esc(m.executedBy)}`:""}</div><div class="actions"><button onclick="speak(${i})">${speaking===i?"■ 停止":"▶ 试听"}</button>${m.status==="已送达"?`<button class="primary" onclick="post({action:'ack',id:'${m.id}'})">签收操作票</button>`:""}${m.status==="已签收"?`<button class="primary" onclick="post({action:'execute',id:'${m.id}'})">执行完成并回令</button>`:""}</div></article>`).join(""):'<div class="empty">没有符合筛选条件的操作票。</div>')}function speak(i){if(speaking===i){speechSynthesis.cancel();speaking=-1;render();return}speechSynthesis.cancel();speaking=i;render();const u=new SpeechSynthesisUtterance(visibleMessages[i].content);u.lang="zh-CN";u.rate=.88;u.onend=u.onerror=()=>{speaking=-1;render()};speechSynthesis.speak(u)}render();setInterval(()=>{if(speaking<0)post({action:"refresh"})},5000);</script></body></html>
        """
    ).replace("哈尔滨市", city_name).replace("nangang_county", username).replace(
        '<div class="agentAvatar">南岗</div>',
        f'<div class="agentAvatar">{county.removesuffix("区").removesuffix("县").removesuffix("市")[:2]}</div>',
    ).replace("__COUNTY__", county).replace(
        "__MESSAGES__", json.dumps(messages, ensure_ascii=False)
    ).replace("__PENDING__", str(pending)).replace("__EXECUTED__", str(executed))
    result = network_component(html=html, height=930, key="county_dashboard", default=None)
    if isinstance(result, dict):
        nonce = result.get("nonce")
        if nonce != st.session_state.get("county_action_nonce"):
            st.session_state["county_action_nonce"] = nonce
            action = result.get("action")
            if action == "logout":
                for key in ("auth_role", "auth_name", "auth_county", "auth_city", "auth_username"):
                    st.session_state.pop(key, None)
                st.rerun()
            if action == "refresh":
                st.rerun()
            message = next((row for row in rows if row["id"] == result.get("id")), None)
            if message is not None and action in {"ack", "execute"}:
                acknowledge_message(message["id"]) if action == "ack" else execute_message(
                    message["id"],
                    st.session_state.get("auth_username") or "nangang_county",
                )
                st.rerun()


if st.query_params.get("logout") == "1":
    for key in ("auth_role", "auth_name", "auth_county", "auth_city", "auth_username"):
        st.session_state.pop(key, None)
    st.query_params.clear()
    st.rerun()

province_targets = {
    "哈尔滨市": {
        "base": 101,
        "counties": ["道里区", "南岗区", "道外区", "平房区", "松北区", "香坊区", "呼兰区", "阿城区", "双城区", "依兰县", "方正县", "宾县", "巴彦县", "木兰县", "通河县", "延寿县", "尚志市", "五常市"],
    },
    "齐齐哈尔市": {
        "base": 301,
        "counties": ["龙沙区", "建华区", "铁锋区", "昂昂溪区", "富拉尔基区", "碾子山区", "梅里斯区", "龙江县", "依安县", "泰来县", "甘南县", "富裕县", "克山县", "克东县", "拜泉县", "讷河市"],
    },
    "牡丹江市": {
        "base": 401,
        "counties": ["东安区", "阳明区", "爱民区", "西安区", "林口县", "绥芬河市", "海林市", "宁安市", "穆棱市", "东宁市"],
    },
    "佳木斯市": {
        "base": 501,
        "counties": ["向阳区", "前进区", "东风区", "郊区", "桦南县", "桦川县", "汤原县", "同江市", "富锦市", "抚远市"],
    },
    "大庆市": {
        "base": 601,
        "counties": ["萨尔图区", "龙凤区", "让胡路区", "红岗区", "大同区", "肇州县", "肇源县", "林甸县", "杜尔伯特县"],
    },
    "鸡西市": {
        "base": 701,
        "counties": ["鸡冠区", "恒山区", "滴道区", "梨树区", "城子河区", "麻山区", "鸡东县", "虎林市", "密山市"],
    },
    "双鸭山市": {
        "base": 801,
        "counties": ["尖山区", "岭东区", "四方台区", "宝山区", "集贤县", "友谊县", "宝清县", "饶河县"],
    },
    "伊春市": {
        "base": 901,
        "counties": ["伊美区", "乌翠区", "友好区", "金林区", "嘉荫县", "汤旺县", "丰林县", "大箐山县", "南岔县", "铁力市"],
    },
    "七台河市": {
        "base": 1001,
        "counties": ["新兴区", "桃山区", "茄子河区", "勃利县"],
    },
    "鹤岗市": {
        "base": 1101,
        "counties": ["向阳区", "工农区", "南山区", "兴安区", "东山区", "兴山区", "萝北县", "绥滨县"],
    },
    "黑河市": {
        "base": 1201,
        "counties": ["爱辉区", "逊克县", "孙吴县", "北安市", "五大连池市", "嫩江市"],
    },
    "绥化市": {
        "base": 1301,
        "counties": ["北林区", "望奎县", "兰西县", "青冈县", "庆安县", "明水县", "绥棱县", "安达市", "肇东市", "海伦市"],
    },
    "大兴安岭地区": {
        "base": 1401,
        "counties": ["加格达奇区", "松岭区", "新林区", "呼中区", "呼玛县", "塔河县", "漠河市"],
    },
}

county_lines = {
    "南岗区": "哈南甲线", "道里区": "哈里乙线", "道外区": "哈东甲线", "香坊区": "哈香乙线",
    "平房区": "哈平甲线", "松北区": "哈松乙线", "呼兰区": "哈呼甲线", "阿城区": "哈阿乙线",
    "龙沙区": "齐龙甲线", "建华区": "齐建乙线", "铁锋区": "齐铁甲线",
    "富拉尔基区": "齐富乙线", "昂昂溪区": "齐昂甲线", "梅里斯区": "齐梅乙线",
    "东安区": "牡东甲线", "西安区": "牡西乙线", "爱民区": "牡爱甲线",
    "阳明区": "牡阳乙线", "海林市": "牡海甲线", "宁安市": "牡宁乙线",
    "向阳区": "佳向甲线", "前进区": "佳前乙线", "东风区": "佳东甲线",
    "郊区": "佳郊乙线", "桦南县": "佳桦甲线", "汤原县": "佳汤乙线",
    "萨尔图区": "庆萨甲线", "龙凤区": "庆龙乙线", "让胡路区": "庆让甲线",
    "红岗区": "庆红乙线", "大同区": "庆同甲线", "肇州县": "庆肇乙线",
    "鸡冠区": "鸡冠甲线", "恒山区": "鸡恒乙线", "滴道区": "鸡滴甲线",
    "梨树区": "鸡梨乙线", "城子河区": "鸡城甲线", "麻山区": "鸡麻乙线",
    "尖山区": "双尖甲线", "岭东区": "双岭乙线", "四方台区": "双四甲线",
    "宝山区": "双宝乙线", "集贤县": "双集甲线", "友谊县": "双友乙线",
    "伊美区": "伊美甲线", "乌翠区": "伊乌乙线", "友好区": "伊友甲线",
    "嘉荫县": "伊嘉乙线", "汤旺县": "伊汤甲线", "丰林县": "伊丰乙线",
}

network_city = st.query_params.get("network_city")
network_county = st.query_params.get("network_county")
if network_city in province_targets:
    valid_counties = province_targets[network_city]["counties"]
    linked_county = network_county if network_county in valid_counties else valid_counties[0]
    st.session_state["province_target_city"] = network_city
    st.session_state[f"province_target_county_{network_city}"] = linked_county
    st.session_state["network_focus_city"] = network_city
    st.session_state["network_focus_county"] = linked_county
    del st.query_params["network_city"]
    if "network_county" in st.query_params:
        del st.query_params["network_county"]
    st.rerun()

pending_network_selection = st.session_state.pop("pending_network_selection", None)
if isinstance(pending_network_selection, dict):
    pending_city = pending_network_selection.get("city")
    pending_county = pending_network_selection.get("county")
    if (
        pending_city in province_targets
        and pending_county in province_targets[pending_city]["counties"]
    ):
        st.session_state["province_target_city"] = pending_city
        st.session_state[f"province_target_county_{pending_city}"] = pending_county
        st.session_state["network_focus_city"] = pending_city
        st.session_state["network_focus_county"] = pending_county

@st.dialog("新建操作票", width="large")
def render_operation_ticket_dialog() -> None:
    default_city = st.session_state.get("network_focus_city", "哈尔滨市")
    city_col, county_col = st.columns(2)
    with city_col:
        target_city = st.selectbox(
            "发布地市",
            list(province_targets),
            index=list(province_targets).index(default_city),
            key="province_target_city",
        )
    template = province_targets[target_city]
    default_county = st.session_state.get(
        "network_focus_county",
        "南岗区" if target_city == "哈尔滨市" else template["counties"][0],
    )
    if default_county not in template["counties"]:
        default_county = template["counties"][0]
    county_key = f"province_target_county_{target_city}"
    with county_col:
        target_county = st.selectbox(
            "发布区县节点",
            template["counties"],
            index=template["counties"].index(default_county),
            key=county_key,
        )
    county_index = template["counties"].index(target_county)
    city_prefix = {
        "哈尔滨市": "哈", "齐齐哈尔市": "齐", "牡丹江市": "牡", "佳木斯市": "佳",
        "大庆市": "庆", "鸡西市": "鸡", "双鸭山市": "双", "伊春市": "伊",
        "七台河市": "七", "鹤岗市": "鹤", "黑河市": "黑", "绥化市": "绥",
        "大兴安岭地区": "兴",
    }[target_city]
    county_short = target_county.replace("区", "").replace("县", "").replace("市", "")[:2]
    line_name = county_lines.get(
        target_county,
        f"{city_prefix}{county_short}{'甲线' if county_index % 2 == 0 else '乙线'}",
    )
    switch_no = template["base"] + county_index * 10
    operation_title = st.text_input(
        "操作任务",
        value=f"{line_name}由运行转检修",
        key=f"operation_title_{target_city}_{target_county}",
    )
    operation_steps = st.text_area(
        "操作票内容（可逐项修改）",
        value=(
            f"第一项，拉开{line_name} {switch_no} 开关。\n"
            f"第二项，拉开{line_name} {switch_no}1 刀闸。\n"
            f"第三项，拉开{line_name} {switch_no}2 刀闸。"
        ),
        height=130,
        key=f"operation_steps_{target_city}_{target_county}",
    )
    st.caption(
        f"当前网络目标：{target_city} → {target_county}　·　"
        "发布地区已根据网络选中节点自动填充"
    )
    if st.button("确认并下发操作票", type="primary", use_container_width=True):
        ticket = send_message(
            target_county,
            operation_title,
            operation_steps,
            receiver=f"{target_city}调度中心",
        )
        st.session_state["dispatch_success"] = (
            f"操作票 {ticket} 已下发至{target_city}调度中心。"
            f"{st.session_state.get('ark_last_review', {}).get('note', '')}"
        )
        st.session_state["open_operation_ticket_dialog"] = False
        st.rerun()


CITIES = [
    {"name": "哈尔滨市", "short": "哈", "load": "12.8 GW", "status": "正常",
     "counties": ["道里区", "南岗区", "道外区", "平房区", "松北区", "香坊区", "呼兰区", "阿城区", "双城区", "依兰县", "方正县", "宾县", "巴彦县", "木兰县", "通河县", "延寿县", "尚志市", "五常市"]},
    {"name": "齐齐哈尔市", "short": "齐", "load": "5.6 GW", "status": "正常",
     "counties": ["龙沙区", "建华区", "铁锋区", "昂昂溪区", "富拉尔基区", "碾子山区", "梅里斯区", "龙江县", "依安县", "泰来县", "甘南县", "富裕县", "克山县", "克东县", "拜泉县", "讷河市"]},
    {"name": "牡丹江市", "short": "牡", "load": "4.2 GW", "status": "正常",
     "counties": ["东安区", "阳明区", "爱民区", "西安区", "林口县", "绥芬河市", "海林市", "宁安市", "穆棱市", "东宁市"]},
    {"name": "佳木斯市", "short": "佳", "load": "3.9 GW", "status": "正常",
     "counties": ["向阳区", "前进区", "东风区", "郊区", "桦南县", "桦川县", "汤原县", "同江市", "富锦市", "抚远市"]},
    {"name": "大庆市", "short": "庆", "load": "6.1 GW", "status": "关注",
     "counties": ["萨尔图区", "龙凤区", "让胡路区", "红岗区", "大同区", "肇州县", "肇源县", "林甸县", "杜尔伯特县"]},
    {"name": "鸡西市", "short": "鸡", "load": "2.7 GW", "status": "正常",
     "counties": ["鸡冠区", "恒山区", "滴道区", "梨树区", "城子河区", "麻山区", "鸡东县", "虎林市", "密山市"]},
    {"name": "双鸭山市", "short": "双", "load": "2.3 GW", "status": "正常",
     "counties": ["尖山区", "岭东区", "四方台区", "宝山区", "集贤县", "友谊县", "宝清县", "饶河县"]},
    {"name": "伊春市", "short": "伊", "load": "1.8 GW", "status": "正常",
     "counties": ["伊美区", "乌翠区", "友好区", "金林区", "嘉荫县", "汤旺县", "丰林县", "大箐山县", "南岔县", "铁力市"]},
    {"name": "七台河市", "short": "七", "load": "1.5 GW", "status": "正常",
     "counties": ["新兴区", "桃山区", "茄子河区", "勃利县"]},
    {"name": "鹤岗市", "short": "鹤", "load": "1.7 GW", "status": "正常",
     "counties": ["向阳区", "工农区", "南山区", "兴安区", "东山区", "兴山区", "萝北县", "绥滨县"]},
    {"name": "黑河市", "short": "黑", "load": "1.9 GW", "status": "正常",
     "counties": ["爱辉区", "逊克县", "孙吴县", "北安市", "五大连池市", "嫩江市"]},
    {"name": "绥化市", "short": "绥", "load": "3.4 GW", "status": "正常",
     "counties": ["北林区", "望奎县", "兰西县", "青冈县", "庆安县", "明水县", "绥棱县", "安达市", "肇东市", "海伦市"]},
    {"name": "大兴安岭地区", "short": "兴", "load": "0.9 GW", "status": "正常",
     "counties": ["加格达奇区", "松岭区", "新林区", "呼中区", "呼玛县", "塔河县", "漠河市"]},
]


def region_slug(name: str) -> str:
    core = name
    for suffix in ("自治县", "地区", "市", "区", "县"):
        if core.endswith(suffix):
            core = core.removesuffix(suffix)
            break
    return "".join(lazy_pinyin(core)).replace(" ", "")


city_by_name = {city["name"]: city for city in CITIES}
CITY_SLUGS = {
    "哈尔滨市": "harbin",
    "齐齐哈尔市": "qiqihar",
    "牡丹江市": "mudanjiang",
    "佳木斯市": "jiamusi",
    "大庆市": "daqing",
    "鸡西市": "jixi",
    "双鸭山市": "shuangyashan",
    "伊春市": "yichun",
    "七台河市": "qitaihe",
    "鹤岗市": "hegang",
    "黑河市": "heihe",
    "绥化市": "suihua",
    "大兴安岭地区": "daxinganling",
}
county_occurrences: dict[str, int] = {}
for city in CITIES:
    for county in city["counties"]:
        county_occurrences[county] = county_occurrences.get(county, 0) + 1


def county_agent_id(city_name: str, county: str) -> str:
    if county_occurrences.get(county, 0) > 1:
        return f"{city_name}{county}调度智能体"
    return f"{county}调度智能体"


for city in CITIES:
    city_slug = CITY_SLUGS[city["name"]]
    city_username = f"{city_slug}_city"
    DEMO_ACCOUNTS[city_username] = {
        "password": "demo123",
        "role": "city",
        "name": f'{city["name"]}级调度账号',
        "city": city["name"],
    }
    for county in city["counties"]:
        county_slug = region_slug(county)
        if county_occurrences[county] > 1:
            county_slug = f"{city_slug}_{county_slug}"
        county_username = f"{county_slug}_county"
        DEMO_ACCOUNTS[county_username] = {
            "password": "demo123",
            "role": "county",
            "name": f"{county}级调度账号",
            "city": city["name"],
            "county": county,
        }

role = st.session_state.get("auth_role")
if role not in {"province", "city", "county"}:
    render_login()
    st.stop()
if role == "city":
    authenticated_city = st.session_state.get("auth_city") or "哈尔滨市"
    city_config = city_by_name[authenticated_city]
    render_city_dashboard(
        authenticated_city,
        city_config["counties"],
        st.session_state.get("auth_username") or f"{CITY_SLUGS[authenticated_city]}_city",
    )
    st.stop()
if role == "county":
    render_county_dashboard(
        st.session_state.get("auth_county") or "南岗区",
        st.session_state.get("auth_city") or "哈尔滨市",
        st.session_state.get("auth_username") or "nangang_county",
    )
    st.stop()

touch_agent("黑龙江省调度中心", "province")

st.markdown(
    """
    <div class="workspace-brand">
      <b>龙江电网 · 虚拟配网调度中心</b>
      <small>当前账号：hlj_province　·　省级调度权限　·　指令下发工作台</small>
      <a class="workspace-logout" href="/?logout=1" target="_self">退出账号</a>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.session_state.get("open_operation_ticket_dialog", False):
    render_operation_ticket_dialog()
if dispatch_success := st.session_state.pop("dispatch_success", None):
    st.toast(dispatch_success, icon="✅")

online_agents = load_online_agents()
for city in CITIES:
    city["online"] = f'{city["name"]}调度中心' in online_agents
online_city_count = sum(1 for city in CITIES if city["online"])

today_command_count, today_delivery_text = load_today_dispatch_stats()
recent_dispatches = [
    {
        "title": row["title"],
        "origin": "省级下发" if row["sender"] == "黑龙江省调度中心" else "市级自主下发",
        "method": "province" if row["sender"] == "黑龙江省调度中心" else "city",
        "city": (
            row["sender"].removesuffix("调度中心")
            if row["sender"] != "黑龙江省调度中心"
            else row["receiver"].removesuffix("调度中心")
        ),
        "county": row["target_county"],
        "route": f'{row["sender"]} → {row["receiver"]}',
        "time": format_beijing_time(row["created_at"], "%m-%d %H:%M"),
        "createdAt": int(row["created_at"] * 1000),
        "content": row["content"],
        "status": row["status"],
        "executedAt": format_beijing_time(row["executed_at"]) if row["executed_at"] else "",
        "executedBy": row["executed_by"] or "",
    }
    for row in load_messages()
]
focused_city_name = st.session_state.get("network_focus_city")
focused_city_index = next(
    (index for index, city in enumerate(CITIES) if city["name"] == focused_city_name),
    0,
)
focused_county_name = st.session_state.get(
    "network_focus_county",
    "南岗区" if focused_city_index == 0 else CITIES[focused_city_index]["counties"][0],
)
network_is_focused = focused_city_name is not None

html = dedent(
    r"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
    :root{--bg:#07111f;--panel:#0b1f33;--line:rgba(84,167,204,.18);--cyan:#39d7ee;--blue:#2f7cf6;--text:#eaf8ff;--muted:#7892a9}
    *{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:"Microsoft YaHei UI","PingFang SC",sans-serif;overflow:hidden}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 52% 48%,rgba(0,125,196,.15),transparent 43%),linear-gradient(rgba(43,124,164,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(43,124,164,.045) 1px,transparent 1px);background-size:auto,30px 30px,30px 30px}
    button{font:inherit;color:inherit}.app{height:862px;display:flex;flex-direction:column;position:relative}
    .top{height:68px;padding:0 27px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:rgba(5,16,29,.93)}
    .brand{display:flex;align-items:center;gap:13px}.logo{width:38px;height:38px;display:grid;place-items:center;border-radius:7px 15px;background:linear-gradient(145deg,#148fd1,#2ad5d6);font-size:25px;box-shadow:0 0 22px #168cc866}.brand b{font-size:18px;letter-spacing:2px}.brand small{display:block;margin-top:4px;color:#56738d;font-size:8px;letter-spacing:2.4px}
    .online{font-size:11px;color:#8ea8bd}.dot{display:inline-block;width:7px;height:7px;margin-right:8px;border-radius:50%;background:#39e6a7;box-shadow:0 0 10px #39e6a7}.clock{margin-left:24px;font:13px Consolas;color:#bdd6e5}
    .bar{height:82px;padding:0 27px;display:flex;align-items:center;gap:36px;border-bottom:1px solid var(--line);background:rgba(8,22,38,.83)}
    .title{min-width:210px}.title span{display:block;color:var(--cyan);font-size:9px;letter-spacing:3px;margin-bottom:5px}.title b{font-size:17px}
    .stats{display:flex;flex:1}.stat{min-width:135px;padding:0 25px;border-left:1px solid var(--line)}.stat span{display:block;font-size:9px;color:var(--muted)}.stat b{font:21px Consolas}.stat em{font-size:8px;color:#4fd9ad;font-style:normal;margin-left:6px}
    .new{border:0;border-radius:5px;background:linear-gradient(100deg,#1679c7,#25bed1);padding:12px 18px;cursor:pointer;box-shadow:0 8px 25px #087ca33a}
    .work{flex:1;display:grid;grid-template-columns:260px minmax(560px,1fr) 290px;gap:11px;padding:11px 15px;min-height:0}
    .panel{background:linear-gradient(145deg,rgba(13,31,51,.94),rgba(7,20,35,.83));border:1px solid var(--line);overflow:hidden}.ph{height:46px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);font-size:12px;font-weight:700}.ph small{font-size:8px;color:#4bcda4;font-weight:400}
    .cities{padding:7px;max-height:570px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#8fc9ba #edf5f3}.cityrow{width:100%;border:1px solid transparent;background:transparent;border-radius:4px;padding:8px;display:grid;grid-template-columns:35px 1fr auto;gap:8px;align-items:center;text-align:left;cursor:pointer}.cityrow:hover{background:#14314a}.cityrow.active{background:linear-gradient(90deg,rgba(22,121,185,.32),rgba(20,72,105,.18));border-color:#259bc255;box-shadow:inset 3px 0 var(--cyan)}
    .avatar{width:31px;height:31px;display:grid;place-items:center;border:1px solid #327693;border-radius:50%;background:#143147;color:#9beafa;font-weight:700}.ci b{font-size:11px}.ci small{display:block;margin-top:3px;font-size:8px;color:#617e95}.load{font-size:8px;color:#7994a7;text-align:right}.load i{display:block;margin-top:3px;color:#46d6a3;font-style:normal}.load i.warn{color:#ffbb57}
    .all{width:calc(100% - 14px);margin:2px 7px;padding:9px;border:1px solid var(--line);background:#0e2a4055;color:#58cce7;font-size:9px}
    .network{position:relative;border:1px solid var(--line);background:radial-gradient(circle,rgba(17,62,89,.32),rgba(4,15,27,.3) 58%,rgba(4,13,24,.7));overflow:hidden}.nt{position:absolute;z-index:9;top:13px;left:17px;right:17px;display:flex;justify-content:space-between;color:#7190a6;font-size:8px}.nt>b{color:#b8d6e7;font-size:11px}.legend i{display:inline-block;width:6px;height:6px;margin:0 4px 0 9px;border-radius:50%;background:#24b3e3}.legend i:first-child{background:white;box-shadow:0 0 8px var(--cyan)}.legend i:last-of-type{border:1px solid #557d91;background:transparent}
    .scene{position:absolute;inset:43px 19px 15px}.orbit{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%;pointer-events:none}.outer{width:91%;height:76%;border:1px dashed #3b728666}.middle{width:57%;height:49%;border:1px solid #278eb54d}.inner{width:29%;aspect-ratio:1;border:1px solid #35d3e63d}
    .sweep{position:absolute;width:52%;aspect-ratio:1;left:50%;top:50%;transform-origin:0 0;background:conic-gradient(from 10deg,transparent 0 315deg,rgba(38,184,211,.08) 345deg,transparent 360deg);animation:sweep 12s linear infinite}@keyframes sweep{to{transform:rotate(360deg)}}
    .line{position:absolute;height:1px;transform-origin:left center;background:linear-gradient(90deg,#24cde988,#24cde915);z-index:0}.line.hot{height:2px;background:linear-gradient(90deg,#b5f6ff,#23cde7);box-shadow:0 0 7px #28d9f0;animation:glow 1.3s ease-in-out infinite alternate}@keyframes glow{from{opacity:.45}to{opacity:1}}
    .province{position:absolute;z-index:5;left:50%;top:50%;transform:translate(-50%,-50%);width:125px;height:125px;border:1px solid #55e5f6;border-radius:50%;background:radial-gradient(circle at 35% 30%,#154d67,#071a2c 67%);box-shadow:0 0 22px #29d4e052,inset 0 0 25px #36d9e51c;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;cursor:pointer}.picon{width:44px;height:44px;display:grid;place-items:center;border-radius:50%;background:linear-gradient(145deg,#38d3df,#167bc6);box-shadow:0 0 18px #2acbd6aa;font-size:12px;font-weight:900}.province b{font-size:10px}.province small{font-size:7px;color:#72a0b5}.ring{position:absolute;inset:-10px;border:1px solid #37bdd555;border-radius:50%;animation:pulse 2s ease-out infinite}@keyframes pulse{0%{transform:scale(.9);opacity:1}100%{transform:scale(1.18);opacity:0}}
    .cnode{position:absolute;z-index:4;transform:translate(-50%,-50%);width:53px;height:53px;border:1px solid #2b6c8a;border-radius:50%;background:#0c2639;display:grid;place-items:center;padding:5px;cursor:pointer;color:inherit}.cnode span{width:25px;height:25px;display:grid;place-items:center;border-radius:50%;background:#133e56;color:#8fcfe3;font-size:10px}.cnode small{font-size:7px;color:#688da2}.cnode.active{border-color:var(--cyan);box-shadow:0 0 18px #29cce766;transform:translate(-50%,-50%) scale(1.12)}.cnode.active span{color:white;background:linear-gradient(145deg,#258fba,#21c9d4)}
    .knode{position:absolute;z-index:3;transform:translate(-50%,-50%);width:17px;height:17px;border:0;background:transparent;padding:0;cursor:pointer;color:inherit}.knode>span{display:block;width:6px;height:6px;margin:auto;border:1px solid #466c80;border-radius:50%;background:#102b3b}.knode small{position:absolute;left:12px;top:-2px;width:max-content;font-size:7px;color:#72a4b9}.knode.group>span{border-color:#32cfe3;background:#24a4bf;box-shadow:0 0 6px #2ed9eb}.knode.selected>span{width:9px;height:9px;background:#fff;border:2px solid #28d7e8;box-shadow:0 0 11px #fff}.knode.selected small{color:#fff;font-weight:700}
    .olabel{position:absolute;padding:3px 7px;border:1px solid #24546b;border-radius:10px;background:#071827dd;color:#577b91;font-size:7px;letter-spacing:1px}.citylabel{left:50%;top:16%;transform:translateX(-50%)}.countylabel{left:50%;bottom:0;transform:translateX(-50%)}
    .route{margin:10px;padding:13px;border:1px solid #21506a;background:#0b2438aa}.rnode{display:flex;align-items:center;gap:10px}.mini{width:30px;height:30px;display:grid;place-items:center;border:1px solid #319cc0;border-radius:50%;background:#143c53;font-size:9px;font-weight:700}.rnode small{display:block;font-size:7px;color:#5d7b90}.rnode b{font-size:10px}.flow{height:32px;margin-left:15px;border-left:1px dashed #28bed4;position:relative}.flow:after{content:"";position:absolute;width:4px;height:4px;border-radius:50%;background:#fff;left:-2.5px;animation:down 1.4s linear infinite;box-shadow:0 0 6px var(--cyan)}@keyframes down{from{top:1px}to{top:28px}}.flow em{position:absolute;left:9px;top:10px;font-size:7px;color:#3d7288;font-style:normal}
    .sect{padding:6px 11px;display:flex;justify-content:space-between;font-size:10px;font-weight:700}.msg{margin:7px 9px;padding:10px;border:1px solid #1e4a62;background:#0d2638a8}.msg.flash{border-color:#30cfe4;box-shadow:0 0 18px #27bed125}.meta{display:flex;justify-content:space-between;font-size:9px}.meta span,.msg p{font-size:7px;color:#65869a}.audio{width:100%;height:29px;border:0;background:#12354a;display:flex;align-items:center;gap:8px;cursor:pointer}.play{width:18px;height:18px;display:grid;place-items:center;border-radius:50%;background:#27acc4;font-size:7px}.wave{flex:1;display:flex;align-items:center;gap:3px}.wave b{width:2px;height:5px;background:#48bbce;animation:wave .8s ease-in-out infinite alternate}.wave b:nth-child(2n){height:12px}.wave b:nth-child(3n){height:8px}@keyframes wave{to{transform:scaleY(.35)}}.audio em{font-size:7px;color:#7698aa;font-style:normal}.delivery{margin-top:7px;text-align:right;font-size:7px;color:#50d7a5}
    .auditFilters{margin:0 9px 7px;padding:8px;border:1px solid #d6e5e1;background:#f7fbfa}.filterToggle{width:100%;height:29px;border:1px solid #acd4ca;border-radius:4px;background:#edf7f4;color:#17604f;font-size:9px;cursor:pointer}.filterGrid{display:none;grid-template-columns:1fr 1fr;gap:6px;margin-top:7px}.filterGrid.show{display:grid}.filterGrid label{display:block;color:#68847d;font-size:7px}.filterGrid select{width:100%;height:27px;margin-top:3px;border:1px solid #bddbd3;background:#fff;color:#264b43;font-size:8px}.filterSummary{margin-top:6px;color:#008f70;font-size:8px;text-align:right}
    #recentMessages{max-height:300px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#8fc9ba #edf5f3}
    .foot{height:28px;padding:0 20px;border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;color:#49677b;font-size:7px}
    .back{display:none;position:fixed;z-index:20;inset:0;background:rgba(2,9,17,.84);backdrop-filter:blur(5px);place-items:center}.back.show{display:grid}.modal{width:min(650px,calc(100vw - 30px));padding:19px;border:1px solid #2a7896;background:linear-gradient(145deg,#102e45,#081b2d);box-shadow:0 24px 80px #000a}.mh{display:flex;justify-content:space-between;padding-bottom:13px;border-bottom:1px solid var(--line)}.mh b{font-size:16px}.mh small{display:block;margin-top:5px;color:#7190a6;font-size:8px}.close{border:0;background:transparent;font-size:24px;cursor:pointer}.steps{height:53px;display:flex;align-items:center;justify-content:center;gap:7px;color:#7592a6;font-size:8px}.steps b{width:20px;height:20px;display:grid;place-items:center;border-radius:50%;background:#1c8fb4;color:#fff}.steps i{width:35px;border-top:1px dashed #386980}
    .ticket{border:1px solid #2d6177;background:#091f30;padding:12px}.tickethead{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:10px;font-size:8px}.tickethead span{color:#67869a}.ticket ol{margin:11px 0 0;padding:0;list-style:none}.ticket li{padding:7px;margin-top:5px;background:#102c3e;font-size:9px}.ticket li em{display:inline-grid;place-items:center;width:20px;height:20px;margin-right:9px;border-radius:50%;background:#1b6982;color:#8fe4f0;font-size:7px;font-style:normal}.target{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:9px;align-items:center;padding:12px 0;font-size:8px}.target span{color:#68869a}.target b{padding:8px;border:1px solid #24536a;background:#0a2234;font-weight:400}.actions{display:flex;justify-content:flex-end;gap:8px}.actions button{padding:10px 15px;border:1px solid #2b6076;background:#102c3e;cursor:pointer;font-size:9px}.actions .send{border:0;background:linear-gradient(100deg,#1679c7,#25bed1)}
    /* 国家电网业务大屏视觉：品牌绿、清洁白、少量金色提示 */
    :root{--bg:#eef5f3;--panel:#ffffff;--line:#d6e5e1;--cyan:#00a779;--blue:#007f66;--text:#193a33;--muted:#68847d}
    html,body{background:#eef5f3;color:#193a33}
    body:before{background:linear-gradient(rgba(0,127,102,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(0,127,102,.025) 1px,transparent 1px);background-size:28px 28px}
    .top{background:linear-gradient(105deg,#006c58,#009b77);border:0;box-shadow:0 5px 18px rgba(0,81,66,.18)}
    .brand b,.online,.clock{color:#fff}.brand small{color:#ccebe3}.logo{background:#fff;color:#00856b;box-shadow:none}
    .bar{background:#fff;border-bottom:1px solid #d6e5e1}.title span{color:#008d70}.title b{color:#173f36}
    .stat{border-left-color:#d9e8e4}.stat b{color:#006f5a}.stat span{color:#708a84}.stat em{color:#008f70}
    .new{min-width:142px;height:40px;padding:0 18px;border:1px solid #00745e;border-radius:8px;background:linear-gradient(105deg,#00745e,#00a779);color:#fff;font-size:11px;font-weight:700;letter-spacing:.5px;box-shadow:0 5px 14px rgba(0,127,102,.18);transition:transform .2s ease,box-shadow .2s ease}.new:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(0,127,102,.25)}.new:active{transform:translateY(0)}
    .panel,.network{background:#fff;border-color:#d6e5e1;box-shadow:0 4px 14px rgba(28,74,64,.06)}
    .ph{background:#f7fbfa;border-bottom-color:#d6e5e1;color:#21493f}.ph small,.secure{color:#008f70!important}
    .cityrow{color:#264b43}.cityrow:hover{background:#edf8f5}.cityrow.active{background:#e4f5f0;border-color:#9dd8c8;box-shadow:inset 4px 0 #008f70}
    .avatar{border-color:#77bdaa;background:#e7f5f1;color:#007b63}.ci small,.load{color:#78918b}.all{background:#f1f8f6;border-color:#cfe3de;color:#007f66}
    .network{background:radial-gradient(circle,#edf8f5 0,#fff 56%,#f6faf9 100%)}.nt>b{color:#21493f}.nt,.legend{color:#6f8982}
    .outer{border-color:#96cbbd}.middle{border-color:#87c4b4}.inner{border-color:#95d6c5}
    .line{background:linear-gradient(90deg,#00a779aa,#00a77922)}.line.hot{background:linear-gradient(90deg,#007f66,#22bd92);box-shadow:0 0 6px rgba(0,167,121,.42)}
    .province{border-color:#00a779;background:radial-gradient(circle at 35% 30%,#23b98e,#00735d 70%);box-shadow:0 8px 28px rgba(0,112,88,.25);color:#fff}
    .province small{color:#d8f2eb}.picon{background:#fff;color:#007f66;box-shadow:none}.ring{border-color:#32bb97}
    .cnode{border-color:#9dcfc2;background:#fff}.cnode span{background:#e4f5f0;color:#007b63}.cnode small{color:#547b71}.cnode.active{border-color:#00a779;box-shadow:0 5px 16px rgba(0,143,112,.2)}.cnode.active span{background:#008f70}
    .knode>span{border-color:#7fbcae;background:#fff}.knode.group>span{background:#00a779;border-color:#00856b;box-shadow:0 0 5px #44c7a5}.knode.selected>span{background:#f4b63d;border-color:#fff;box-shadow:0 0 9px #dc9d28}
    .knode small,.knode.selected small{color:#466f65}.olabel{background:#fff;border-color:#b9d8d0;color:#65837c}
    .scene .orbit,.scene .province,.scene .cnode,.scene .knode,.scene .line{transition:left .55s ease,top .55s ease,opacity .4s ease,transform .55s ease,width .55s ease,height .55s ease}
    .focusHint{position:absolute;z-index:8;top:1px;left:50%;transform:translateX(-50%);padding:5px 13px;border-radius:16px;background:#e7f5f1;border:1px solid #b9dcd3;color:#267061;font-size:8px;opacity:0;transition:.35s;pointer-events:none}
    .scene.focused .focusHint{opacity:1}.scene.focused .citylabel,.scene.focused .countylabel{display:none}.scene.focused .outer{width:74%;height:72%;opacity:.22}.scene.focused .middle{width:60%;height:58%;opacity:.7}.scene.focused .inner{width:31%;opacity:.85}
    .scene.focused .province{width:82px;height:82px;box-shadow:0 5px 18px rgba(0,112,88,.2)}.scene.focused .province .picon{width:34px;height:34px}.scene.focused .province b{font-size:8px}.scene.focused .province small{display:none}.scene.focused .province:after{content:"返回省级总览";position:absolute;top:88px;width:max-content;padding:4px 9px;border:1px solid #b7d9d0;border-radius:12px;background:#fff;color:#007f66;font-size:8px}
    .scene.focused .cnode.dimmed{opacity:.28;transform:translate(-50%,-50%) scale(.72)}.scene.focused .cnode.focusCenter{width:76px;height:76px;border:2px solid #00a779;box-shadow:0 9px 25px rgba(0,127,102,.24);transform:translate(-50%,-50%) scale(1)}.scene.focused .cnode.focusCenter span{width:36px;height:36px;font-size:13px;background:#008f70;color:#fff}.scene.focused .cnode.focusCenter small{font-size:9px;font-weight:700;color:#245e51}
    .scene.focused .knode.focusCounty{width:48px;height:28px;z-index:6}.scene.focused .knode.focusCounty>span{width:10px;height:10px;background:#fff;border:2px solid #00a779;box-shadow:0 2px 8px rgba(0,127,102,.18)}.scene.focused .knode.focusCounty small{left:25px;top:5px;padding:2px 5px;border-radius:8px;background:rgba(255,255,255,.9);color:#285f52;font-size:9px;font-weight:700;white-space:nowrap}.scene.focused .knode.focusCounty.selected>span{background:#f4b63d;border-color:#fff}
    .scene.focused .cnode.peer{opacity:.52;border-style:dashed;background:#f3f6f5;filter:grayscale(1)}.scene.focused .cnode.peer span{background:#e9eeec;color:#81908c}.scene.focused .cnode.peer small{color:#879590}
    .hierarchyTag{position:absolute;z-index:7;transform:translate(-50%,-50%);padding:4px 10px;border-radius:13px;font-size:8px;font-weight:700;letter-spacing:1px;pointer-events:none}.hierarchyTag.superior{left:16%;top:35%;background:#e8f2ff;border:1px solid #a9c6e8;color:#366a9d}.hierarchyTag.current{left:54%;top:39%;background:#007f66;color:#fff;box-shadow:0 4px 12px rgba(0,127,102,.2)}.hierarchyTag.subordinate{left:54%;top:10%;background:#e8f7f2;border:1px solid #8fcfbd;color:#00745e}.hierarchyTag.peerTag{right:2%;bottom:2%;transform:none;background:#f0f2f1;border:1px dashed #aebbb7;color:#75827e}
    .line.subordinateLine{height:2px;background:linear-gradient(90deg,#00a779,#61c9ac);box-shadow:none}.line.superiorLine{height:3px;background:linear-gradient(90deg,#4c86bc,#00a779);box-shadow:0 0 6px rgba(48,121,155,.22)}.scene.focused .middle{background:rgba(0,167,121,.025)}.scene.focused .outer{background:rgba(106,126,120,.018)}
    .route{border-color:#d6e5e1;background:#f7fbfa}.mini{border-color:#82c4b3;background:#e3f4ef;color:#00745e}.rnode small,.flow em{color:#708b84}.flow{border-color:#4ab697}
    .sect{color:#254b42}.msg{border-color:#d7e5e2;background:#fff;box-shadow:0 2px 8px rgba(35,77,68,.05);cursor:pointer;transition:.2s}.msg:hover{border-color:#00a779;transform:translateY(-1px);box-shadow:0 7px 18px rgba(0,127,102,.11)}
    .meta span,.msg p{color:#718a84}.audio{background:#e8f4f1}.play{background:#008f70;color:#fff}.wave b{background:#00a779}.delivery{color:#008b6c}
    .rightPanel,.right{background:#fff}.foot{background:#f7fbfa;border-color:#d6e5e1;color:#68847d}
    .back{background:rgba(9,45,37,.48)}.modal{border-color:#8dcbbd;background:#fff;color:#193a33;box-shadow:0 24px 70px rgba(16,64,54,.24);border-radius:10px}
    .ticket{border-color:#cfe2dd;background:#f7fbfa}.ticket li{background:#edf6f3}.tickethead span,.target span,.mh small{color:#6f8982}.ticket li em,.steps b{background:#008f70;color:#fff}.target b{border-color:#cfe2dd;background:#f7fbfa}
    .actions button{border-color:#aad6ca;background:#edf7f4;color:#17604f}.actions .send{background:linear-gradient(105deg,#007f66,#00a779);color:#fff}
    .editfield{margin:12px 0}.editfield label{display:block;margin-bottom:6px;color:#54766e;font-size:9px}.editfield input,.ticket input{width:100%;padding:9px;border:1px solid #bddbd3;background:#fff;color:#193a33;outline:none}.ticket input:focus,.editfield input:focus{border-color:#00a779;box-shadow:0 0 0 2px rgba(0,167,121,.1)}
    .recordbody{padding:14px;background:#f6faf9;border:1px solid #d6e5e1;line-height:1.8;font-size:11px}.recordstatus{display:inline-block;margin-top:12px;padding:5px 10px;border-radius:15px;background:#def3ed;color:#007f66;font-size:9px}
    /* 定点增加两号，避免父子元素继承造成图标重复放大 */
    .title span{font-size:11px}.title b{font-size:19px}.stat span{font-size:11px}.stat em{font-size:10px}.ph{font-size:14px}.ph small{font-size:10px}.ci b{font-size:13px}.ci small,.load{font-size:10px}.nt>b{font-size:13px}.nt,.legend{font-size:10px}.cnode small{font-size:9px}.knode small{font-size:9px}.sect{font-size:12px}.meta{font-size:11px}.meta span,.msg p,.delivery{font-size:9px}.rnode b{font-size:12px}.rnode small,.flow em{font-size:9px}
    @media(max-width:1050px){.work{grid-template-columns:220px 1fr}.right{display:none}.stat{min-width:105px;padding:0 13px}}@media(max-width:720px){.app{height:900px}.work{grid-template-columns:1fr}.left{display:none}.bar{height:auto;padding:10px;flex-wrap:wrap}.stats{order:3;width:100%}.stat{flex:1;min-width:0;padding:0 8px}.scene{inset:43px 2px 14px}.knode small{display:none}.brand b{font-size:13px}.brand small,.clock{display:none}}
    </style>
    </head>
    <body>
    <div class="app">
      <section class="bar"><div class="title"><span>全域态势</span><b>省级调度中心视角</b></div><div class="stats"><div class="stat"><span>地市智能体</span><b>__CITY_ONLINE__</b><em>/ 13 在线</em></div><div class="stat"><span>区县节点</span><b>125</b><em>已配置</em></div><div class="stat"><span>今日指令</span><b>__TODAY_COUNT__</b><em>__TODAY_STATUS__</em></div></div><button class="new" onclick="requestOperationTicket()">＋ 新建操作票</button></section>
      <section class="work">
        <aside class="panel left"><div class="ph"><span>地市调度</span><small>__CITY_ONLINE__ / 13 在线</small></div><div class="cities" id="cities"></div></aside>
        <div class="network"><div class="nt"><b>智能体通信网络</b><div class="legend"><i></i>省级 <i></i>地市 <i></i>区 / 县</div></div><div class="scene" id="scene"><div class="focusHint" id="focusHint">地市聚焦视图 · 点击左侧省级节点返回全省总览</div><div class="sweep"></div><div class="orbit outer"></div><div class="orbit middle"></div><div class="orbit inner"></div><div class="olabel citylabel">地市协同轨道</div><div class="olabel countylabel">区 / 县独立轨道 · 125 节点</div><div class="province" onclick="selectProvince()" title="返回省级总览"><span class="ring"></span><span class="picon">龙江</span><b>省级调度智能体</b><small>全局态势 · 指令中枢</small></div></div></div>
        <aside class="panel right"><div class="ph"><span>当前链路</span><small>● 加密通信</small></div><div class="route" id="route"></div><div class="sect"><span>全量调度指令</span><span style="color:#008f70;font-size:8px">支持组合筛选</span></div><div class="auditFilters"><button class="filterToggle" onclick="toggleFilters()">⌕ 筛选历史指令</button><div class="filterGrid" id="filterGrid"><label>下发方式<select id="methodFilter" onchange="applyFilters()"><option value="">全部方式</option><option value="province">省级下发</option><option value="city">市级自主下发</option></select></label><label>执行地市<select id="cityFilter" onchange="cityFilterChanged()"><option value="">全部地市</option></select></label><label>执行区县<select id="countyFilter" onchange="applyFilters()"><option value="">全部区县</option></select></label><label>时间范围<select id="timeFilter" onchange="applyFilters()"><option value="">全部时间</option><option value="today">今天</option><option value="7d">近7天</option><option value="30d">近30天</option></select></label><label>执行状态<select id="statusFilter" onchange="applyFilters()"><option value="">全部状态</option><option>已送达</option><option>已签收</option><option>已转发</option><option>已执行</option></select></label><label>筛选操作<button class="filterToggle" style="margin-top:3px" onclick="resetFilters()">重置条件</button></label></div><div class="filterSummary" id="filterSummary"></div></div><div id="recentMessages"></div></aside>
      </section>
      <footer class="foot"><span><i class="dot"></i>数据更新时间：<span id="footclock"></span></span><span>省级知识底座同步正常　·　通信延迟 32ms　·　运行环境 STREAMLIT DEMO</span></footer>
    </div>
    <div class="back" id="back" onclick="if(event.target===this)closeModal()"><section class="modal"><div class="mh"><div><b>新建调度指令</b><small>操作内容可编辑，确认后生成语音并下发</small></div><button class="close" onclick="closeModal()">×</button></div><div class="steps"><b>1</b><span>编辑操作票</span><i></i><b>2</b><span>确认接收节点</span><i></i><b>3</b><span>语音下发</span></div><div class="editfield"><label>调度任务名称</label><input id="taskName" value="哈西甲乙线由运行转检修"></div><div class="ticket"><div class="tickethead"><span>操作单位</span><b>哈尔滨供电公司</b><span>票号</span><b>HLJ-2026-0725-018</b></div><ol><li><em>01</em><input id="step1" value="拉开哈西甲乙线 101 开关"></li><li><em>02</em><input id="step2" value="拉开哈西甲乙线 1011 刀闸"></li><li><em>03</em><input id="step3" value="拉开哈西甲乙线 1012 刀闸"></li></ol></div><div class="target"><span>下发至</span><b id="targetcity">哈尔滨市调度中心</b><span>目标节点</span><b id="targetcounty">南岗区</b></div><div class="actions"><button onclick="speakEdited()">试听 AI 语音</button><button class="send" onclick="sendTicket()">生成语音并下发　→</button></div></section></div>
    <div class="back" id="recordBack" onclick="if(event.target===this)closeRecord()"><section class="modal"><div class="mh"><div><b id="recordTitle">调度指令详情</b><small id="recordRoute"></small></div><button class="close" onclick="closeRecord()">×</button></div><div class="recordbody" id="recordContent"></div><span class="recordstatus" id="recordStatus"></span><div class="actions" style="margin-top:16px"><button onclick="speakRecord()">播放指令语音</button><button class="send" onclick="closeRecord()">关闭</button></div></section></div>
    <script>
    const cities=__CITIES__;
    const recentMessages=__RECENT_MESSAGES__;
    const ticketTemplates=[
      {line:"哈西甲乙线",switchNo:"101",blade1:"1011",blade2:"1012"},
      {line:"齐南甲线",switchNo:"301",blade1:"3011",blade2:"3012"},
      {line:"牡东乙线",switchNo:"401",blade1:"4011",blade2:"4012"},
      {line:"佳东甲线",switchNo:"501",blade1:"5011",blade2:"5012"},
      {line:"庆北乙线",switchNo:"601",blade1:"6011",blade2:"6012"},
      {line:"鸡冠甲线",switchNo:"701",blade1:"7011",blade2:"7012"},
      {line:"双宝乙线",switchNo:"801",blade1:"8011",blade2:"8012"},
      {line:"伊美甲线",switchNo:"901",blade1:"9011",blade2:"9012"}
    ];
    let active=__ACTIVE_INDEX__,selected=__SELECTED_COUNTY__,focused=__NETWORK_FOCUSED__;
    const autoRefreshEnabled=__AUTO_REFRESH_ENABLED__;
    function syncParentTarget(city,county){window.parent.postMessage({type:"networkTarget",city,county,nonce:Date.now()},"*")}
    function requestOperationTicket(){window.parent.postMessage({type:"networkTarget",action:"openTicket",city:cities[active].name,county:selected,nonce:Date.now()},"*")}
    function raiseFonts(){}
    function polar(i,n,r){const a=i/n*Math.PI*2-Math.PI/2;return{x:50+Math.cos(a)*r,y:50+Math.sin(a)*r,a}}
    function line(x,y,len,a,hot,kind){const e=document.createElement("div");e.className="line "+(hot?"hot ":"")+kind;e.style.cssText=`left:${x}%;top:${y}%;width:${len}%;transform:rotate(${a}rad)`;return e}
    function render(){
      const list=document.getElementById("cities");list.innerHTML=cities.map((c,i)=>`<button class="cityrow ${i===active?"active":""}" onclick="selectCity(${i})"><span class="avatar">${c.short}</span><span class="ci"><b>${c.name}</b><small>${c.counties.length} 个区县节点</small></span><span class="load"><i class="${c.online?"":"warn"}">${c.online?"● 在线":"● 离线"}</i></span></button>`).join("");
      const scene=document.getElementById("scene");scene.querySelectorAll(".dynamic").forEach(e=>e.remove());
      scene.classList.toggle("focused",focused);
      const province=scene.querySelector(".province");province.style.left=focused?"16%":"50%";province.style.top="50%";
      const center={x:54,y:50};
      if(focused){
        [["上级｜省级调度","superior"],["当前｜地市调度","current"],["下属区县节点","subordinate"],["灰色虚线节点：同级地市","peerTag"]].forEach(([textName,kind])=>{const tag=document.createElement("div");tag.className=`hierarchyTag ${kind} dynamic`;tag.textContent=textName;scene.appendChild(tag)})
      }
      cities.forEach((c,i)=>{
        const base=polar(i,cities.length,focused?43:28);
        const p=focused?(i===active?center:{x:54+(base.x-50),y:50+(base.y-50)}):base;
        if(!focused||i===active){const sx=focused?16:50,sy=50,dx=p.x-sx,dy=p.y-sy;scene.appendChild(line(sx,sy,Math.sqrt(dx*dx+dy*dy),Math.atan2(dy,dx),i===active,focused?"dynamic superiorLine":"dynamic"))}
        const n=document.createElement("button");n.className=`cnode dynamic ${i===active?"active":""} ${focused&&i===active?"focusCenter":""} ${focused&&i!==active?"dimmed peer":""}`;n.style.cssText=`left:${p.x}%;top:${p.y}%`;n.innerHTML=`<span>${c.short}</span><small>${c.name.replace("市","")}${focused&&i!==active?" · 同级":""}</small>`;n.onclick=()=>selectCity(i,true);scene.appendChild(n)
      });
      const total=cities.reduce((s,c)=>s+c.counties.length,0);let k=0;
      cities.forEach((c,ci)=>c.counties.forEach((name,countyIndex)=>{
        let p;
        if(focused){
          if(ci!==active){k++;return}
          const split=Math.ceil(c.counties.length/2),outer=countyIndex>=split,ri=outer?countyIndex-split:countyIndex,rn=outer?c.counties.length-split:split,r=outer?36:23,rp=polar(ri,rn,r);
          p={x:center.x+(rp.x-50),y:center.y+(rp.y-50)}
          const dx=p.x-center.x,dy=p.y-center.y;scene.appendChild(line(center.x,center.y,Math.sqrt(dx*dx+dy*dy),Math.atan2(dy,dx),name===selected,"dynamic subordinateLine"));
        }else p=polar(k,total,46);
        k++;
        const n=document.createElement("button");n.className=`knode dynamic ${ci===active?"group":""} ${ci===active&&name===selected?"selected":""} ${focused?"focusCounty":""}`;n.style.cssText=`left:${p.x}%;top:${p.y}%`;n.innerHTML=`<span></span>${focused?`<small>${name}</small>`:""}`;n.onclick=()=>{active=ci;selected=name;focused=true;render();syncParentTarget(cities[ci].name,name)};scene.appendChild(n)
      }));
      const c=cities[active];document.getElementById("route").innerHTML=`<div class="rnode"><span class="mini" style="background:#1d91b9">省</span><div><small>指令发起</small><b>黑龙江省调度中心</b></div></div><div class="flow"><em>专线传输</em></div><div class="rnode"><span class="mini">${c.short}</span><div><small>当前接收</small><b>${c.name}调度中心</b></div></div><div class="flow"><em>辖区独立链路</em></div><div class="rnode"><span class="mini">区</span><div><small>目标节点</small><b>${selected}智能体</b></div></div>`;
      document.getElementById("targetcity").textContent=c.name+"调度中心";document.getElementById("targetcounty").textContent=selected;raiseFonts();
    }
    function selectCity(i,syncForm=false){active=i;selected=cities[i].counties[0];focused=true;render();if(syncForm)syncParentTarget(cities[i].name,selected)}
    function selectProvince(){focused=false;render()}
    function escapeHtml(value){return String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]))}
    let speakingIndex=-1,filteredMessages=[...recentMessages];
    function toggleFilters(){document.getElementById("filterGrid").classList.toggle("show")}
    function fillCountyFilter(){
      const city=document.getElementById("cityFilter").value,current=document.getElementById("countyFilter").value;
      const names=city?(cities.find(c=>c.name===city)?.counties||[]):[...new Set(cities.flatMap(c=>c.counties))];
      document.getElementById("countyFilter").innerHTML='<option value="">全部区县</option>'+names.map(n=>`<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
      if(names.includes(current))document.getElementById("countyFilter").value=current
    }
    function cityFilterChanged(){fillCountyFilter();applyFilters()}
    function beijingTodayStart(){
      const parts=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
      const value=Object.fromEntries(parts.map(p=>[p.type,p.value]));
      return Date.parse(`${value.year}-${value.month}-${value.day}T00:00:00+08:00`)
    }
    function applyFilters(){
      const method=document.getElementById("methodFilter").value,city=document.getElementById("cityFilter").value,county=document.getElementById("countyFilter").value,timeRange=document.getElementById("timeFilter").value,status=document.getElementById("statusFilter").value;
      const now=Date.now(),cutoff=timeRange==="today"?beijingTodayStart():timeRange==="7d"?now-7*86400000:timeRange==="30d"?now-30*86400000:0;
      filteredMessages=recentMessages.filter(m=>(!method||m.method===method)&&(!city||m.city===city)&&(!county||m.county===county)&&(!status||m.status===status)&&(!cutoff||m.createdAt>=cutoff));
      speakingIndex=-1;document.getElementById("filterSummary").textContent=`找到 ${filteredMessages.length} 条记录`;renderRecent()
    }
    function resetFilters(){["methodFilter","cityFilter","countyFilter","timeFilter","statusFilter"].forEach(id=>document.getElementById(id).value="");fillCountyFilter();applyFilters()}
    function initFilters(){
      document.getElementById("cityFilter").innerHTML='<option value="">全部地市</option>'+cities.map(c=>`<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("");
      fillCountyFilter();applyFilters()
    }
    function renderRecent(){
      const box=document.getElementById("recentMessages");
      if(!filteredMessages.length){box.innerHTML='<div style="padding:22px 12px;color:#78918b;font-size:9px;text-align:center">没有符合筛选条件的调度指令</div>';return}
      box.innerHTML=filteredMessages.map((m,i)=>`<div class="msg" data-index="${i}"><div class="meta"><b>${escapeHtml(m.title)}</b><span>${escapeHtml(m.time)}</span></div><p><strong style="color:${m.origin==="省级下发"?"#087f68":"#b07819"}">【${escapeHtml(m.origin)}】</strong> ${escapeHtml(m.route)}</p><button class="audio" data-audio="${i}"><span class="play">${speakingIndex===i?"■":"▶"}</span><i class="wave"><b></b><b></b><b></b><b></b><b></b><b></b></i><em>${speakingIndex===i?"停止播放":"试听语音"}</em></button><div class="delivery">✓ ${escapeHtml(m.status)}${m.executedAt?`<br>执行：${escapeHtml(m.executedAt)}<br>账号：${escapeHtml(m.executedBy)}`:""}</div></div>`).join("");
      box.querySelectorAll(".msg").forEach(card=>card.onclick=()=>{const m=filteredMessages[Number(card.dataset.index)];openRecord(m.title,m.route,m.status,m.content)});
      box.querySelectorAll(".audio").forEach(btn=>btn.onclick=e=>{e.stopPropagation();toggleMessageSpeech(Number(btn.dataset.audio))});raiseFonts();
    }
    function toggleMessageSpeech(index){
      if(!("speechSynthesis" in window))return;
      if(speakingIndex===index){speechSynthesis.cancel();speakingIndex=-1;renderRecent();return}
      speechSynthesis.cancel();speakingIndex=index;renderRecent();
      const u=new SpeechSynthesisUtterance(filteredMessages[index].content);u.lang="zh-CN";u.rate=.88;
      u.onend=u.onerror=()=>{if(speakingIndex===index){speakingIndex=-1;renderRecent()}};
      speechSynthesis.speak(u);
    }
    function syncTicketToTarget(){
      const t=ticketTemplates[active];
      document.getElementById("taskName").value=`${t.line}由运行转检修`;
      document.getElementById("step1").value=`拉开${t.line} ${t.switchNo} 开关`;
      document.getElementById("step2").value=`拉开${t.line} ${t.blade1} 刀闸`;
      document.getElementById("step3").value=`拉开${t.line} ${t.blade2} 刀闸`;
      document.getElementById("targetcity").textContent=cities[active].name+"调度中心";
      document.getElementById("targetcounty").textContent=selected;
    }
    function openModal(){syncTicketToTarget();document.getElementById("back").classList.add("show")}function closeModal(){document.getElementById("back").classList.remove("show")}
    let currentRecord="";
    function openRecord(title,route,status,content){currentRecord=content;document.getElementById("recordTitle").textContent=title;document.getElementById("recordRoute").textContent=route;document.getElementById("recordStatus").textContent=status;document.getElementById("recordContent").textContent=content;document.getElementById("recordBack").classList.add("show")}
    function closeRecord(){document.getElementById("recordBack").classList.remove("show")}
    function speakRecord(){if(!("speechSynthesis" in window))return;const u=new SpeechSynthesisUtterance(currentRecord);u.lang="zh-CN";u.rate=.88;speechSynthesis.cancel();speechSynthesis.speak(u)}
    function editedText(){return document.getElementById("taskName").value+"。"+document.getElementById("step1").value+"。"+document.getElementById("step2").value+"。"+document.getElementById("step3").value+"。操作完成后立即回令。"}
    function speakEdited(){if(!("speechSynthesis" in window))return;const u=new SpeechSynthesisUtterance(editedText());u.lang="zh-CN";u.rate=.88;speechSynthesis.cancel();speechSynthesis.speak(u)}
    function speakText(){if(!("speechSynthesis" in window))return;window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance("哈尔滨市调度员，请执行以下操作票任务。哈西甲乙线，由运行转检修。依次拉开一零一开关、一零一一刀闸、一零一二刀闸。操作完成后立即回令。");u.lang="zh-CN";u.rate=.88;document.getElementById("play").textContent="■";u.onend=()=>document.getElementById("play").textContent="▶";speechSynthesis.speak(u)}
    function sendTicket(){const title=document.getElementById("taskName").value;const content=editedText();const route=`省调 → ${cities[active].name.replace("市","")}市调`;closeModal();const m=document.getElementById("message");m.classList.add("flash");m.querySelector(".meta b").textContent=title;document.getElementById("msgroute").textContent=route;document.getElementById("msgtime").textContent="刚刚";m.onclick=()=>openRecord(title,route,"已送达",content);setTimeout(speakEdited,250)}
    setInterval(()=>{const t=new Date().toLocaleTimeString("zh-CN",{hour12:false,timeZone:"Asia/Shanghai"});const clock=document.getElementById("clock");if(clock)clock.textContent=t;document.getElementById("footclock").textContent=t},1000);if(autoRefreshEnabled)setInterval(()=>{if(speakingIndex<0&&!document.getElementById("back").classList.contains("show")&&!document.getElementById("recordBack").classList.contains("show"))window.parent.postMessage({type:"networkTarget",action:"refresh",nonce:Date.now()},"*")},5000);render();initFilters();raiseFonts();
    </script>
    </body></html>
    """
).replace("__CITIES__", json.dumps(CITIES, ensure_ascii=False)).replace(
    "__RECENT_MESSAGES__", json.dumps(recent_dispatches, ensure_ascii=False)
).replace(
    "__TODAY_COUNT__", str(today_command_count)
).replace("__TODAY_STATUS__", today_delivery_text).replace(
    "__CITY_ONLINE__", str(online_city_count)
).replace(
    "__ACTIVE_INDEX__", str(focused_city_index)
).replace(
    "__SELECTED_COUNTY__", json.dumps(focused_county_name, ensure_ascii=False)
).replace(
    "__NETWORK_FOCUSED__", "true" if network_is_focused else "false"
).replace(
    "__AUTO_REFRESH_ENABLED__",
    "false" if st.session_state.get("open_operation_ticket_dialog", False) else "true",
)

network_selection = network_component(
    html=html,
    height=862,
    key="province_network_topology",
    default=None,
)
if isinstance(network_selection, dict):
    selection_nonce = network_selection.get("nonce")
    if selection_nonce != st.session_state.get("processed_network_selection"):
        st.session_state["processed_network_selection"] = selection_nonce
        if network_selection.get("action") == "openTicket":
            st.session_state["open_operation_ticket_dialog"] = True
            st.rerun()
        if network_selection.get("action") == "refresh":
            st.rerun()
        selected_city = network_selection.get("city")
        selected_county = network_selection.get("county")
        if (
            selected_city in province_targets
            and selected_county in province_targets[selected_city]["counties"]
        ):
            st.session_state["open_operation_ticket_dialog"] = False
            st.session_state["pending_network_selection"] = {
                "city": selected_city,
                "county": selected_county,
            }
            st.rerun()
