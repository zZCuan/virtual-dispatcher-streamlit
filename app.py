"""龙江电网虚拟配网调度中心——Streamlit 演示版。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from textwrap import dedent

import streamlit as st
import streamlit.components.v1 as components


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
    .stButton>button[kind="primary"] {background:linear-gradient(105deg,#007f66,#00a779);border:0}
    .stTextInput input,.stTextArea textarea,[data-baseweb="select"]>div {
      background:#fff!important;border-color:#bddbd3!important;color:#193a33!important
    }
    iframe {display:block}
    </style>
    """,
    unsafe_allow_html=True,
)

DB_PATH = Path("/tmp/virtual_dispatcher_messages.db")


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
            acknowledged_at REAL
        )
        """
    )
    connection.commit()
    return connection


def send_message(
    target_county: str,
    title: str,
    steps: str,
    sender: str = "黑龙江省调度中心",
    receiver: str = "哈尔滨市调度中心",
) -> str:
    ticket_no = f"HLJ-{time.strftime('%Y%m%d')}-{int(time.time()) % 10000:04d}"
    content = (
        f"{receiver}调度员，请执行以下操作票任务。{title}。"
        f"{steps.replace(chr(10), '；')}。"
        "操作完成后立即回令。"
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
                sender,
                receiver,
                title,
                ticket_no,
                target_county,
                content,
                "已送达",
            ),
        )
        connection.commit()
    return ticket_no


def acknowledge_message(message_id: str) -> None:
    with connect_db() as connection:
        connection.execute(
            "UPDATE dispatch_messages SET status='已签收', acknowledged_at=? WHERE id=?",
            (time.time(), message_id),
        )
        connection.commit()


def load_messages() -> list[sqlite3.Row]:
    connection = connect_db()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM dispatch_messages ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    connection.close()
    return rows


def load_messages_for(receiver: str) -> list[sqlite3.Row]:
    connection = connect_db()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT * FROM dispatch_messages
        WHERE receiver=?
        ORDER BY created_at DESC LIMIT 20
        """,
        (receiver,),
    ).fetchall()
    connection.close()
    return rows


def render_login() -> None:
    st.markdown(
        """
        <div style="max-width:880px;margin:11vh auto 32px;padding:36px 42px;
        border:1px solid rgba(57,215,238,.35);border-radius:12px;
        background:linear-gradient(145deg,rgba(13,38,60,.96),rgba(7,20,35,.96));
        box-shadow:0 28px 90px rgba(0,0,0,.35)">
          <div style="color:#39d7ee;font-size:12px;letter-spacing:4px">VIRTUAL DISPATCH NETWORK</div>
          <h1 style="margin:10px 0 8px;font-size:30px">龙江电网 · 虚拟配网调度中心</h1>
          <p style="color:#7892a9;margin:0">请选择调度身份进入独立工作台。建议分别在两个浏览器窗口中登录。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### 省级调度账号")
        st.caption("全局态势监控 · 操作票生成 · 指令下发")
        if st.button("进入黑龙江省级调度中心 →", use_container_width=True, type="primary"):
            st.query_params["role"] = "province"
            st.rerun()
    with right:
        st.markdown("### 地市级调度账号")
        st.caption("指令接收 · 操作票签收 · AI 语音播报")
        if st.button("进入哈尔滨市级调度中心 →", use_container_width=True):
            st.query_params["role"] = "harbin"
            st.rerun()
    st.info("双窗口演示：复制当前地址打开第二个窗口，两个窗口分别选择不同账号。")


def render_harbin_workspace() -> None:
    top_left, top_right = st.columns([5, 1])
    with top_left:
        st.markdown("# 哈尔滨市调度中心")
        st.caption("HARBIN VIRTUAL DISPATCH AGENT · 指令接收工作台")
    with top_right:
        if st.button("退出账号", use_container_width=True):
            st.query_params.clear()
            st.rerun()

    st.markdown(
        """
        <div style="display:flex;gap:14px;margin:6px 0 18px">
          <div style="flex:1;padding:14px 18px;border:1px solid rgba(57,215,238,.22);
          background:#0b1f33;border-radius:6px"><small style="color:#7892a9">上级通信</small>
          <b style="display:block;margin-top:5px">黑龙江省调度中心</b></div>
          <div style="flex:1;padding:14px 18px;border:1px solid rgba(57,215,238,.22);
          background:#0b1f33;border-radius:6px"><small style="color:#7892a9">链路状态</small>
          <b style="display:block;margin-top:5px;color:#48dba7">● 专线在线 · 自动接收</b></div>
          <div style="flex:1;padding:14px 18px;border:1px solid rgba(57,215,238,.22);
          background:#0b1f33;border-radius:6px"><small style="color:#7892a9">当前账号</small>
          <b style="display:block;margin-top:5px">哈尔滨市级调度员</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    counties = ["南岗区", "道里区", "道外区", "香坊区", "平房区", "松北区", "呼兰区", "阿城区"]
    network_nodes = "".join(
        f"""
        <div style="display:flex;flex-direction:column;align-items:center;gap:7px">
          <div style="width:48px;height:48px;border:1px solid #39d7ee;border-radius:50%;
          display:grid;place-items:center;background:#113149;color:#dffaff;
          box-shadow:0 0 14px rgba(57,215,238,.24)">区</div>
          <small style="color:#9fc4d5">{county}</small>
        </div>
        """
        for county in counties
    )
    st.markdown(
        f"""
        <div style="padding:20px;margin:4px 0 18px;border:1px solid rgba(57,215,238,.24);
        background:radial-gradient(circle at center,rgba(23,93,122,.28),#081827 65%);border-radius:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:18px">
            <b>哈尔滨市 · 区县智能体网络</b>
            <span style="color:#48dba7;font-size:12px">● 8 / 8 在线 · 独立链路</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:center;gap:18px;flex-wrap:wrap">
            <div style="display:flex;flex-direction:column;align-items:center;gap:7px;margin-right:10px">
              <div style="width:76px;height:76px;border:2px solid #5ce4f2;border-radius:50%;
              display:grid;place-items:center;background:#146080;color:white;font-weight:700;
              box-shadow:0 0 24px rgba(57,215,238,.38)">哈尔滨</div>
              <small style="color:#9fc4d5">市级调度智能体</small>
            </div>
            <div style="width:38px;border-top:1px dashed #39d7ee"></div>
            {network_nodes}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("向下属区县新建调度指令", expanded=True):
        county_col, title_col = st.columns([1, 2])
        with county_col:
            downstream_county = st.selectbox("接收区县", counties, key="downstream_county")
        with title_col:
            downstream_title = st.text_input(
                "调度任务",
                value="区域配网运行方式调整",
                key="downstream_title",
            )
        downstream_steps = st.text_area(
            "操作步骤（可修改）",
            value="第一项，核对当前线路运行状态。\n第二项，执行指定开关操作。\n第三项，复核并向哈尔滨市调回令。",
            height=110,
            key="downstream_steps",
        )
        if st.button(
            f"下发至{downstream_county}智能体",
            type="primary",
            use_container_width=True,
            key="send_downstream",
        ):
            ticket = send_message(
                downstream_county,
                downstream_title,
                downstream_steps,
                sender="哈尔滨市调度中心",
                receiver=f"{downstream_county}调度智能体",
            )
            st.success(f"操作票 {ticket} 已下发至{downstream_county}调度智能体。")

    @st.fragment(run_every="2s")
    def inbox() -> None:
        messages = load_messages_for("哈尔滨市调度中心")
        st.markdown("### 省调下发指令")
        if not messages:
            st.info("正在监听省级调度中心，暂无待接收指令。")
            return

        unread = sum(1 for item in messages if item["status"] == "已送达")
        if unread:
            st.success(f"收到 {unread} 条新调度指令，请及时签收。")

        for index, message in enumerate(messages):
            is_new = message["status"] == "已送达"
            border = "#39d7ee" if is_new else "rgba(84,167,204,.22)"
            created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(message["created_at"]))
            st.markdown(
                f"""
                <div style="padding:18px 20px;margin:10px 0 4px;border:1px solid {border};
                border-left:4px solid {border};border-radius:6px;background:#0b1f33">
                  <div style="display:flex;justify-content:space-between;gap:16px">
                    <b style="font-size:17px">{message['title']}</b>
                    <span style="color:#7892a9;font-size:12px">{created}</span>
                  </div>
                  <div style="margin:8px 0;color:#7892a9;font-size:13px">
                    {message['sender']} → {message['receiver']} · 目标节点：{message['target_county']}
                  </div>
                  <div style="padding:11px 13px;background:#071827;border-radius:4px;font-size:13px">
                    操作票号：{message['ticket_no']}<br>{message['content']}
                  </div>
                  <div style="margin-top:9px;color:#48dba7;font-size:12px">状态：{message['status']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            action_left, action_right, _ = st.columns([1, 1, 4])
            with action_left:
                if is_new and st.button("签收指令", key=f"ack-{message['id']}", type="primary"):
                    acknowledge_message(message["id"])
                    st.rerun()
            with action_right:
                if st.button("播放语音", key=f"voice-{message['id']}"):
                    voice_text = json.dumps(message["content"], ensure_ascii=False)
                    components.html(
                        f"""
                        <script>
                        const utterance = new SpeechSynthesisUtterance({voice_text});
                        utterance.lang = "zh-CN";
                        utterance.rate = 0.88;
                        window.speechSynthesis.cancel();
                        window.speechSynthesis.speak(utterance);
                        </script>
                        <div style="font:13px sans-serif;color:#48dba7">正在播放 AI 调度语音…</div>
                        """,
                        height=32,
                    )
            if index == 0:
                st.caption("页面每 2 秒自动同步一次，省级窗口下发后无需手动刷新。")

    inbox()


role = st.query_params.get("role", "")
if role not in {"province", "harbin"}:
    render_login()
    st.stop()
if role == "harbin":
    render_harbin_workspace()
    st.stop()

province_title, province_logout = st.columns([6, 1])
with province_title:
    st.markdown("### 黑龙江省级调度账号 · 指令下发工作台")
with province_logout:
    if st.button("退出账号", use_container_width=True):
        st.query_params.clear()
        st.rerun()

province_targets = {
    "哈尔滨市": {
        "line": "哈西甲乙线", "switch": "101", "blade1": "1011", "blade2": "1012",
        "counties": ["南岗区", "道里区", "道外区", "香坊区", "平房区", "松北区", "呼兰区", "阿城区"],
    },
    "齐齐哈尔市": {
        "line": "齐南甲线", "switch": "301", "blade1": "3011", "blade2": "3012",
        "counties": ["龙沙区", "建华区", "铁锋区", "富拉尔基区", "昂昂溪区", "梅里斯区"],
    },
    "牡丹江市": {
        "line": "牡东乙线", "switch": "401", "blade1": "4011", "blade2": "4012",
        "counties": ["东安区", "西安区", "爱民区", "阳明区", "海林市", "宁安市"],
    },
    "佳木斯市": {
        "line": "佳东甲线", "switch": "501", "blade1": "5011", "blade2": "5012",
        "counties": ["向阳区", "前进区", "东风区", "郊区", "桦南县", "汤原县"],
    },
    "大庆市": {
        "line": "庆北乙线", "switch": "601", "blade1": "6011", "blade2": "6012",
        "counties": ["萨尔图区", "龙凤区", "让胡路区", "红岗区", "大同区", "肇州县"],
    },
    "鸡西市": {
        "line": "鸡冠甲线", "switch": "701", "blade1": "7011", "blade2": "7012",
        "counties": ["鸡冠区", "恒山区", "滴道区", "梨树区", "城子河区", "麻山区"],
    },
    "双鸭山市": {
        "line": "双宝乙线", "switch": "801", "blade1": "8011", "blade2": "8012",
        "counties": ["尖山区", "岭东区", "四方台区", "宝山区", "集贤县", "友谊县"],
    },
    "伊春市": {
        "line": "伊美甲线", "switch": "901", "blade1": "9011", "blade2": "9012",
        "counties": ["伊美区", "乌翠区", "友好区", "嘉荫县", "汤旺县", "丰林县"],
    },
}

with st.expander("新建调度指令", expanded=True):
    city_col, county_col = st.columns(2)
    with city_col:
        target_city = st.selectbox("接收地市", list(province_targets), key="province_target_city")
    template = province_targets[target_city]
    with county_col:
        target_county = st.selectbox(
            "关联区县节点",
            template["counties"],
            key=f"province_target_county_{target_city}",
        )

    operation_title = st.text_input(
        "调度任务",
        value=f"{template['line']}由运行转检修",
        key=f"operation_title_{target_city}",
    )
    operation_steps = st.text_area(
        "操作票内容（可逐项修改）",
        value=(
            f"第一项，拉开{template['line']} {template['switch']} 开关。\n"
            f"第二项，拉开{template['line']} {template['blade1']} 刀闸。\n"
            f"第三项，拉开{template['line']} {template['blade2']} 刀闸。"
        ),
        height=120,
        key=f"operation_steps_{target_city}",
    )
    st.caption(
        f"接收方：{target_city}调度中心　·　关联节点：{target_county}　·　"
        "操作票是调度指令的结构化正文，不再设置第二个重复入口"
    )
    if st.button("确认并下发调度指令", type="primary", use_container_width=True):
        ticket = send_message(
            target_county,
            operation_title,
            operation_steps,
            receiver=f"{target_city}调度中心",
        )
        st.success(f"调度指令 {ticket} 已下发至{target_city}调度中心，等待对方签收。")

CITIES = [
    {"name": "哈尔滨市", "short": "哈", "load": "12.8 GW", "status": "正常",
     "counties": ["南岗区", "道里区", "道外区", "香坊区", "平房区", "松北区", "呼兰区", "阿城区"]},
    {"name": "齐齐哈尔市", "short": "齐", "load": "5.6 GW", "status": "正常",
     "counties": ["龙沙区", "建华区", "铁锋区", "富拉尔基区", "昂昂溪区", "梅里斯区"]},
    {"name": "牡丹江市", "short": "牡", "load": "4.2 GW", "status": "正常",
     "counties": ["东安区", "西安区", "爱民区", "阳明区", "海林市", "宁安市"]},
    {"name": "佳木斯市", "short": "佳", "load": "3.9 GW", "status": "正常",
     "counties": ["向阳区", "前进区", "东风区", "郊区", "桦南县", "汤原县"]},
    {"name": "大庆市", "short": "庆", "load": "6.1 GW", "status": "关注",
     "counties": ["萨尔图区", "龙凤区", "让胡路区", "红岗区", "大同区", "肇州县"]},
    {"name": "鸡西市", "short": "鸡", "load": "2.7 GW", "status": "正常",
     "counties": ["鸡冠区", "恒山区", "滴道区", "梨树区", "城子河区", "麻山区"]},
    {"name": "双鸭山市", "short": "双", "load": "2.3 GW", "status": "正常",
     "counties": ["尖山区", "岭东区", "四方台区", "宝山区", "集贤县", "友谊县"]},
    {"name": "伊春市", "short": "伊", "load": "1.8 GW", "status": "正常",
     "counties": ["伊美区", "乌翠区", "友好区", "嘉荫县", "汤旺县", "丰林县"]},
]

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
    button{font:inherit;color:inherit}.app{height:930px;display:flex;flex-direction:column;position:relative}
    .top{height:68px;padding:0 27px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:rgba(5,16,29,.93)}
    .brand{display:flex;align-items:center;gap:13px}.logo{width:38px;height:38px;display:grid;place-items:center;border-radius:7px 15px;background:linear-gradient(145deg,#148fd1,#2ad5d6);font-size:25px;box-shadow:0 0 22px #168cc866}.brand b{font-size:18px;letter-spacing:2px}.brand small{display:block;margin-top:4px;color:#56738d;font-size:8px;letter-spacing:2.4px}
    .online{font-size:11px;color:#8ea8bd}.dot{display:inline-block;width:7px;height:7px;margin-right:8px;border-radius:50%;background:#39e6a7;box-shadow:0 0 10px #39e6a7}.clock{margin-left:24px;font:13px Consolas;color:#bdd6e5}
    .bar{height:82px;padding:0 27px;display:flex;align-items:center;gap:36px;border-bottom:1px solid var(--line);background:rgba(8,22,38,.83)}
    .title{min-width:210px}.title span{display:block;color:var(--cyan);font-size:9px;letter-spacing:3px;margin-bottom:5px}.title b{font-size:17px}
    .stats{display:flex;flex:1}.stat{min-width:135px;padding:0 25px;border-left:1px solid var(--line)}.stat span{display:block;font-size:9px;color:var(--muted)}.stat b{font:21px Consolas}.stat em{font-size:8px;color:#4fd9ad;font-style:normal;margin-left:6px}
    .new{border:0;border-radius:5px;background:linear-gradient(100deg,#1679c7,#25bed1);padding:12px 18px;cursor:pointer;box-shadow:0 8px 25px #087ca33a}
    .work{flex:1;display:grid;grid-template-columns:260px minmax(560px,1fr) 290px;gap:11px;padding:11px 15px;min-height:0}
    .panel{background:linear-gradient(145deg,rgba(13,31,51,.94),rgba(7,20,35,.83));border:1px solid var(--line);overflow:hidden}.ph{height:46px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);font-size:12px;font-weight:700}.ph small{font-size:8px;color:#4bcda4;font-weight:400}
    .cities{padding:7px}.cityrow{width:100%;border:1px solid transparent;background:transparent;border-radius:4px;padding:8px;display:grid;grid-template-columns:35px 1fr auto;gap:8px;align-items:center;text-align:left;cursor:pointer}.cityrow:hover{background:#14314a}.cityrow.active{background:linear-gradient(90deg,rgba(22,121,185,.32),rgba(20,72,105,.18));border-color:#259bc255;box-shadow:inset 3px 0 var(--cyan)}
    .avatar{width:31px;height:31px;display:grid;place-items:center;border:1px solid #327693;border-radius:50%;background:#143147;color:#9beafa;font-weight:700}.ci b{font-size:11px}.ci small{display:block;margin-top:3px;font-size:8px;color:#617e95}.load{font-size:8px;color:#7994a7;text-align:right}.load i{display:block;margin-top:3px;color:#46d6a3;font-style:normal}.load i.warn{color:#ffbb57}
    .all{width:calc(100% - 14px);margin:2px 7px;padding:9px;border:1px solid var(--line);background:#0e2a4055;color:#58cce7;font-size:9px}
    .network{position:relative;border:1px solid var(--line);background:radial-gradient(circle,rgba(17,62,89,.32),rgba(4,15,27,.3) 58%,rgba(4,13,24,.7));overflow:hidden}.nt{position:absolute;z-index:9;top:13px;left:17px;right:17px;display:flex;justify-content:space-between;color:#7190a6;font-size:8px}.nt>b{color:#b8d6e7;font-size:11px}.legend i{display:inline-block;width:6px;height:6px;margin:0 4px 0 9px;border-radius:50%;background:#24b3e3}.legend i:first-child{background:white;box-shadow:0 0 8px var(--cyan)}.legend i:last-of-type{border:1px solid #557d91;background:transparent}
    .scene{position:absolute;inset:43px 19px 15px}.orbit{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%;pointer-events:none}.outer{width:91%;height:76%;border:1px dashed #3b728666}.middle{width:57%;height:49%;border:1px solid #278eb54d}.inner{width:29%;aspect-ratio:1;border:1px solid #35d3e63d}
    .sweep{position:absolute;width:52%;aspect-ratio:1;left:50%;top:50%;transform-origin:0 0;background:conic-gradient(from 10deg,transparent 0 315deg,rgba(38,184,211,.08) 345deg,transparent 360deg);animation:sweep 12s linear infinite}@keyframes sweep{to{transform:rotate(360deg)}}
    .line{position:absolute;height:1px;transform-origin:left center;background:linear-gradient(90deg,#24cde988,#24cde915);z-index:0}.line.hot{height:2px;background:linear-gradient(90deg,#b5f6ff,#23cde7);box-shadow:0 0 7px #28d9f0;animation:glow 1.3s ease-in-out infinite alternate}@keyframes glow{from{opacity:.45}to{opacity:1}}
    .province{position:absolute;z-index:5;left:50%;top:50%;transform:translate(-50%,-50%);width:125px;height:125px;border:1px solid #55e5f6;border-radius:50%;background:radial-gradient(circle at 35% 30%,#154d67,#071a2c 67%);box-shadow:0 0 22px #29d4e052,inset 0 0 25px #36d9e51c;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;cursor:pointer}.picon{width:44px;height:44px;display:grid;place-items:center;border-radius:50%;background:linear-gradient(145deg,#38d3df,#167bc6);box-shadow:0 0 18px #2acbd6aa;font-size:12px;font-weight:900}.province b{font-size:10px}.province small{font-size:7px;color:#72a0b5}.ring{position:absolute;inset:-10px;border:1px solid #37bdd555;border-radius:50%;animation:pulse 2s ease-out infinite}@keyframes pulse{0%{transform:scale(.9);opacity:1}100%{transform:scale(1.18);opacity:0}}
    .cnode{position:absolute;z-index:4;transform:translate(-50%,-50%);width:53px;height:53px;border:1px solid #2b6c8a;border-radius:50%;background:#0c2639;display:grid;place-items:center;padding:5px;cursor:pointer}.cnode span{width:25px;height:25px;display:grid;place-items:center;border-radius:50%;background:#133e56;color:#8fcfe3;font-size:10px}.cnode small{font-size:7px;color:#688da2}.cnode.active{border-color:var(--cyan);box-shadow:0 0 18px #29cce766;transform:translate(-50%,-50%) scale(1.12)}.cnode.active span{color:white;background:linear-gradient(145deg,#258fba,#21c9d4)}
    .knode{position:absolute;z-index:3;transform:translate(-50%,-50%);width:17px;height:17px;border:0;background:transparent;padding:0;cursor:pointer}.knode>span{display:block;width:6px;height:6px;margin:auto;border:1px solid #466c80;border-radius:50%;background:#102b3b}.knode small{position:absolute;left:12px;top:-2px;width:max-content;font-size:7px;color:#72a4b9}.knode.group>span{border-color:#32cfe3;background:#24a4bf;box-shadow:0 0 6px #2ed9eb}.knode.selected>span{width:9px;height:9px;background:#fff;border:2px solid #28d7e8;box-shadow:0 0 11px #fff}.knode.selected small{color:#fff;font-weight:700}
    .olabel{position:absolute;padding:3px 7px;border:1px solid #24546b;border-radius:10px;background:#071827dd;color:#577b91;font-size:7px;letter-spacing:1px}.citylabel{left:50%;top:16%;transform:translateX(-50%)}.countylabel{left:50%;bottom:0;transform:translateX(-50%)}
    .route{margin:10px;padding:13px;border:1px solid #21506a;background:#0b2438aa}.rnode{display:flex;align-items:center;gap:10px}.mini{width:30px;height:30px;display:grid;place-items:center;border:1px solid #319cc0;border-radius:50%;background:#143c53;font-size:9px;font-weight:700}.rnode small{display:block;font-size:7px;color:#5d7b90}.rnode b{font-size:10px}.flow{height:32px;margin-left:15px;border-left:1px dashed #28bed4;position:relative}.flow:after{content:"";position:absolute;width:4px;height:4px;border-radius:50%;background:#fff;left:-2.5px;animation:down 1.4s linear infinite;box-shadow:0 0 6px var(--cyan)}@keyframes down{from{top:1px}to{top:28px}}.flow em{position:absolute;left:9px;top:10px;font-size:7px;color:#3d7288;font-style:normal}
    .sect{padding:6px 11px;display:flex;justify-content:space-between;font-size:10px;font-weight:700}.msg{margin:7px 9px;padding:10px;border:1px solid #1e4a62;background:#0d2638a8}.msg.flash{border-color:#30cfe4;box-shadow:0 0 18px #27bed125}.meta{display:flex;justify-content:space-between;font-size:9px}.meta span,.msg p{font-size:7px;color:#65869a}.audio{width:100%;height:29px;border:0;background:#12354a;display:flex;align-items:center;gap:8px;cursor:pointer}.play{width:18px;height:18px;display:grid;place-items:center;border-radius:50%;background:#27acc4;font-size:7px}.wave{flex:1;display:flex;align-items:center;gap:3px}.wave b{width:2px;height:5px;background:#48bbce;animation:wave .8s ease-in-out infinite alternate}.wave b:nth-child(2n){height:12px}.wave b:nth-child(3n){height:8px}@keyframes wave{to{transform:scaleY(.35)}}.audio em{font-size:7px;color:#7698aa;font-style:normal}.delivery{margin-top:7px;text-align:right;font-size:7px;color:#50d7a5}
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
    .new{background:linear-gradient(105deg,#007f66,#00a779);box-shadow:0 7px 18px rgba(0,127,102,.2);font-weight:700}
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
    .route{border-color:#d6e5e1;background:#f7fbfa}.mini{border-color:#82c4b3;background:#e3f4ef;color:#00745e}.rnode small,.flow em{color:#708b84}.flow{border-color:#4ab697}
    .sect{color:#254b42}.msg{border-color:#d7e5e2;background:#fff;box-shadow:0 2px 8px rgba(35,77,68,.05);cursor:pointer;transition:.2s}.msg:hover{border-color:#00a779;transform:translateY(-1px);box-shadow:0 7px 18px rgba(0,127,102,.11)}
    .meta span,.msg p{color:#718a84}.audio{background:#e8f4f1}.play{background:#008f70;color:#fff}.wave b{background:#00a779}.delivery{color:#008b6c}
    .rightPanel,.right{background:#fff}.foot{background:#f7fbfa;border-color:#d6e5e1;color:#68847d}
    .back{background:rgba(9,45,37,.48)}.modal{border-color:#8dcbbd;background:#fff;color:#193a33;box-shadow:0 24px 70px rgba(16,64,54,.24);border-radius:10px}
    .ticket{border-color:#cfe2dd;background:#f7fbfa}.ticket li{background:#edf6f3}.tickethead span,.target span,.mh small{color:#6f8982}.ticket li em,.steps b{background:#008f70;color:#fff}.target b{border-color:#cfe2dd;background:#f7fbfa}
    .actions button{border-color:#aad6ca;background:#edf7f4;color:#17604f}.actions .send{background:linear-gradient(105deg,#007f66,#00a779);color:#fff}
    .editfield{margin:12px 0}.editfield label{display:block;margin-bottom:6px;color:#54766e;font-size:9px}.editfield input,.ticket input{width:100%;padding:9px;border:1px solid #bddbd3;background:#fff;color:#193a33;outline:none}.ticket input:focus,.editfield input:focus{border-color:#00a779;box-shadow:0 0 0 2px rgba(0,167,121,.1)}
    .recordbody{padding:14px;background:#f6faf9;border:1px solid #d6e5e1;line-height:1.8;font-size:11px}.recordstatus{display:inline-block;margin-top:12px;padding:5px 10px;border-radius:15px;background:#def3ed;color:#007f66;font-size:9px}
    @media(max-width:1050px){.work{grid-template-columns:220px 1fr}.right{display:none}.stat{min-width:105px;padding:0 13px}}@media(max-width:720px){.app{height:900px}.work{grid-template-columns:1fr}.left{display:none}.bar{height:auto;padding:10px;flex-wrap:wrap}.stats{order:3;width:100%}.stat{flex:1;min-width:0;padding:0 8px}.scene{inset:43px 2px 14px}.knode small{display:none}.brand b{font-size:13px}.brand small,.clock{display:none}}
    </style>
    </head>
    <body>
    <div class="app">
      <header class="top"><div class="brand"><div class="logo">⌁</div><div><b>龙江电网 · 虚拟配网调度中心</b><small>HEILONGJIANG VIRTUAL DISPATCH NETWORK</small></div></div><div class="online"><i class="dot"></i>全域智能体在线 <span class="clock" id="clock">14:26:08</span></div></header>
      <section class="bar"><div class="title"><span>全域态势</span><b>省级调度中心视角</b></div><div class="stats"><div class="stat"><span>地市智能体</span><b>13</b><em>在线</em></div><div class="stat"><span>区县节点</span><b>125</b><em>独立运行</em></div><div class="stat"><span>今日指令</span><b>28</b><em>100% 送达</em></div></div><div class="new" style="cursor:default">调度态势总览</div></section>
      <section class="work">
        <aside class="panel left"><div class="ph"><span>地市调度</span><small>13 / 13 在线</small></div><div class="cities" id="cities"></div><button class="all">查看全部 13 个地市　→</button></aside>
        <div class="network"><div class="nt"><b>智能体通信网络</b><div class="legend"><i></i>省级 <i></i>地市 <i></i>区 / 县</div></div><div class="scene" id="scene"><div class="sweep"></div><div class="orbit outer"></div><div class="orbit middle"></div><div class="orbit inner"></div><div class="olabel citylabel">地市协同轨道</div><div class="olabel countylabel">区 / 县独立轨道 · 125 节点</div><div class="province"><span class="ring"></span><span class="picon">龙江</span><b>省级调度智能体</b><small>全局态势 · 指令中枢</small></div></div></div>
        <aside class="panel right"><div class="ph"><span>当前链路</span><small>● 加密通信</small></div><div class="route" id="route"></div><div class="sect"><span>最近调度指令</span><span style="color:#008f70;font-size:8px">点击卡片查看</span></div><div class="msg" id="message" onclick="openRecord('哈西甲乙线转检修','省调 → 哈尔滨市调','已送达 · 已签收','拉开哈西甲乙线 101 开关；拉开 1011 刀闸；拉开 1012 刀闸；操作完成后立即回令。')"><div class="meta"><b>哈西甲乙线转检修</b><span id="msgtime">14:18</span></div><p id="msgroute">省调 → 哈尔滨市调</p><button class="audio" onclick="event.stopPropagation();speakText()"><span class="play" id="play">▶</span><i class="wave"><b></b><b></b><b></b><b></b><b></b><b></b></i><em>00:18</em></button><div class="delivery">✓ 已送达　✓ 已签收</div></div><div class="msg" onclick="openRecord('北部断面负荷调整','省调 → 齐齐哈尔市调','已执行','调整北部断面负荷分配，核对潮流状态，执行完成后向省调回令。')"><div class="meta"><b>北部断面负荷调整</b><span>13:42</span></div><p>省调 → 齐齐哈尔市调</p><div class="delivery">✓ 已执行</div></div></aside>
      </section>
      <footer class="foot"><span><i class="dot"></i>数据更新时间：<span id="footclock"></span></span><span>省级知识底座同步正常　·　通信延迟 32ms　·　运行环境 STREAMLIT DEMO</span></footer>
    </div>
    <div class="back" id="back" onclick="if(event.target===this)closeModal()"><section class="modal"><div class="mh"><div><b>新建调度指令</b><small>操作内容可编辑，确认后生成语音并下发</small></div><button class="close" onclick="closeModal()">×</button></div><div class="steps"><b>1</b><span>编辑操作票</span><i></i><b>2</b><span>确认接收节点</span><i></i><b>3</b><span>语音下发</span></div><div class="editfield"><label>调度任务名称</label><input id="taskName" value="哈西甲乙线由运行转检修"></div><div class="ticket"><div class="tickethead"><span>操作单位</span><b>哈尔滨供电公司</b><span>票号</span><b>HLJ-2026-0725-018</b></div><ol><li><em>01</em><input id="step1" value="拉开哈西甲乙线 101 开关"></li><li><em>02</em><input id="step2" value="拉开哈西甲乙线 1011 刀闸"></li><li><em>03</em><input id="step3" value="拉开哈西甲乙线 1012 刀闸"></li></ol></div><div class="target"><span>下发至</span><b id="targetcity">哈尔滨市调度中心</b><span>目标节点</span><b id="targetcounty">南岗区</b></div><div class="actions"><button onclick="speakEdited()">试听 AI 语音</button><button class="send" onclick="sendTicket()">生成语音并下发　→</button></div></section></div>
    <div class="back" id="recordBack" onclick="if(event.target===this)closeRecord()"><section class="modal"><div class="mh"><div><b id="recordTitle">调度指令详情</b><small id="recordRoute"></small></div><button class="close" onclick="closeRecord()">×</button></div><div class="recordbody" id="recordContent"></div><span class="recordstatus" id="recordStatus"></span><div class="actions" style="margin-top:16px"><button onclick="speakRecord()">播放指令语音</button><button class="send" onclick="closeRecord()">关闭</button></div></section></div>
    <script>
    const cities=__CITIES__;
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
    let active=0,selected="南岗区";
    function polar(i,n,r){const a=i/n*Math.PI*2-Math.PI/2;return{x:50+Math.cos(a)*r,y:50+Math.sin(a)*r,a}}
    function line(x,y,len,a,hot,kind){const e=document.createElement("div");e.className="line "+(hot?"hot ":"")+kind;e.style.cssText=`left:${x}%;top:${y}%;width:${len}%;transform:rotate(${a}rad)`;return e}
    function render(){
      const list=document.getElementById("cities");list.innerHTML=cities.map((c,i)=>`<button class="cityrow ${i===active?"active":""}" onclick="selectCity(${i})"><span class="avatar">${c.short}</span><span class="ci"><b>${c.name}</b><small>${c.counties.length} 个区县节点</small></span><span class="load">${c.load}<i class="${c.status==="关注"?"warn":""}">${c.status}</i></span></button>`).join("");
      const scene=document.getElementById("scene");scene.querySelectorAll(".dynamic").forEach(e=>e.remove());
      cities.forEach((c,i)=>{const p=polar(i,cities.length,28);const l=line(50,50,28,p.a,i===active,"dynamic");scene.appendChild(l);const n=document.createElement("button");n.className=`cnode dynamic ${i===active?"active":""}`;n.style.cssText=`left:${p.x}%;top:${p.y}%`;n.innerHTML=`<span>${c.short}</span><small>${c.name.replace("市","")}</small>`;n.onclick=()=>selectCity(i);scene.appendChild(n)});
      const total=cities.reduce((s,c)=>s+c.counties.length,0);let k=0;
      cities.forEach((c,ci)=>c.counties.forEach(name=>{const p=polar(k++,total,46);const cp=polar(ci,cities.length,28);if(ci===active){const dx=p.x-cp.x,dy=p.y-cp.y;scene.appendChild(line(cp.x,cp.y,Math.sqrt(dx*dx+dy*dy),Math.atan2(dy,dx),true,"dynamic"))}const n=document.createElement("button");n.className=`knode dynamic ${ci===active?"group":""} ${ci===active&&name===selected?"selected":""}`;n.style.cssText=`left:${p.x}%;top:${p.y}%`;n.innerHTML=`<span></span>${ci===active?`<small>${name}</small>`:""}`;n.onclick=()=>{active=ci;selected=name;render()};scene.appendChild(n)}));
      const c=cities[active];document.getElementById("route").innerHTML=`<div class="rnode"><span class="mini" style="background:#1d91b9">省</span><div><small>指令发起</small><b>黑龙江省调度中心</b></div></div><div class="flow"><em>专线传输</em></div><div class="rnode"><span class="mini">${c.short}</span><div><small>当前接收</small><b>${c.name}调度中心</b></div></div><div class="flow"><em>辖区独立链路</em></div><div class="rnode"><span class="mini">区</span><div><small>目标节点</small><b>${selected}智能体</b></div></div>`;
      document.getElementById("targetcity").textContent=c.name+"调度中心";document.getElementById("targetcounty").textContent=selected;
    }
    function selectCity(i){active=i;selected=cities[i].counties[0];render()}
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
    setInterval(()=>{const t=new Date().toLocaleTimeString("zh-CN",{hour12:false});document.getElementById("clock").textContent=t;document.getElementById("footclock").textContent=t},1000);render();
    </script>
    </body></html>
    """
).replace("__CITIES__", json.dumps(CITIES, ensure_ascii=False))

components.html(html, height=930, scrolling=False)
