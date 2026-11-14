"""
STUN 通知守卫 v1.0.0

接收 lucky 的 STUN 全局 Webhook，先刷新企业微信应用可信 IP 并用探针验证，
验证通过后再把这条通知推给自己；验证不过则降级走企微群机器人。

为什么不是简单转发：
  企微「应用消息」要求请求源 IP 在「企业可信 IP」白名单内，而白名单由定时任务刷新
  （本机 updatewechatipmodern 是 */30）。公网 IP 变化的窗口内消息会被企微以 60020 拒绝，
  中间的转发层通常把异常吞掉、发送端也不重试，于是这条通知彻底丢失。

探针原理（零打扰）：
  给不存在的 userid 发一条 text 消息 —— 60020 = 源 IP 仍未进白名单；
  其它（81013 用户不存在）= 已通过 IP 校验，即白名单生效。

白名单只按「本机实测出口 IP」刷新，绝不使用事件里的地址：
  lucky 面板点「测试」时送的是假地址（实测 192.168.31.1:6666），
  直接拿它刷白名单会把自己锁死。
"""

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel

from app import schemas
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase

IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
WECOM = "https://qyapi.weixin.qq.com/cgi-bin"
IP_SOURCES = ("https://ip.3322.net", "https://ipv4.icanhazip.com", "https://api-ipv4.ip.sb/ip")


class NotifyRequest(BaseModel):
    title: str = ""
    text: str = ""


class stunnotifyguard(_PluginBase):
    # 插件基本信息
    plugin_name = "STUN 通知守卫"
    plugin_desc = "接收 lucky STUN 事件：先刷企微可信 IP 并探针验证，通过后再推送；失败降级群机器人"
    plugin_icon = "route.png"
    plugin_version = "1.0.0"
    plugin_author = "WUqj1998"
    author_url = "https://github.com/wuqj1998/mp-plugins"
    plugin_config_prefix = "stunnotifyguard_"
    plugin_order = 98
    plugin_label = "本地"
    auth_level = 1

    # 运行时状态
    _enabled = False
    _corpid = ""
    _secret = ""
    _agentid = 0
    _fallback_webhook = ""
    _probe_max_sec = 30
    _probe_interval = 2
    _dedup_window = 600

    _lock = threading.Lock()
    _token: Optional[str] = None
    _token_exp = 0.0
    _ip_cache: Tuple[float, str] = (0.0, "")
    _last_key = ""
    _last_ts = 0.0
    _records: List[Dict[str, Any]] = []

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = bool(config.get("enabled"))
            self._corpid = (config.get("corpid") or "").strip()
            self._secret = (config.get("secret") or "").strip()
            try:
                self._agentid = int(config.get("agentid") or 0)
            except (TypeError, ValueError):
                self._agentid = 0
            self._fallback_webhook = (config.get("fallback_webhook") or "").strip()
            try:
                self._probe_max_sec = int(config.get("probe_max_sec") or 30)
            except (TypeError, ValueError):
                self._probe_max_sec = 30
            try:
                self._dedup_window = int(config.get("dedup_window") or 600)
            except (TypeError, ValueError):
                self._dedup_window = 600
        self._records = self.get_data("records") or []

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/notify",
            "endpoint": self.notify,
            "methods": ["POST"],
            "summary": "lucky STUN 事件入口",
            "description": "接收 {title, text}，先刷新企微可信 IP 并探针验证，再推送；需 apikey",
        }]

    # ------------------------------------------------------------------ 入口
    def notify(self, apikey: str, request: NotifyRequest) -> schemas.Response:
        if apikey != settings.API_TOKEN:
            logger.warning("stunnotifyguard: apikey 校验失败")
            return schemas.Response(success=False, message="API令牌错误!")
        if not self._enabled:
            return schemas.Response(success=False, message="插件未启用")

        title = (request.title or "").strip() or "STUN"
        text = (request.text or "").strip()

        key = f"{title}|{text}"
        now = time.time()
        with self._lock:
            dup = key == self._last_key and now - self._last_ts < self._dedup_window
            if not dup:
                self._last_key, self._last_ts = key, now
        if dup:
            logger.info(f"stunnotifyguard: 重复事件跳过 {key}")
            return schemas.Response(success=True, message="重复事件，已跳过")

        # lucky 侧等待很短，编排放后台线程，立即回执
        threading.Thread(target=self._handle, args=(title, text), daemon=True).start()
        return schemas.Response(success=True, message="已接受")

    # ------------------------------------------------------------------ 编排
    def _handle(self, title: str, text: str) -> None:
        m = IP_RE.search(text)
        event_ip = m.group(1) if m else ""
        real_ip = self._public_ip()
        logger.info(f"stunnotifyguard: 事件 {title!r} {text!r} | 事件IP={event_ip or '?'} 实测出口IP={real_ip or '?'}")

        if event_ip and real_ip and event_ip != real_ip:
            logger.warning(f"stunnotifyguard: 事件 IP {event_ip} 与实测出口 IP {real_ip} 不一致"
                           f"（多为面板手动测试），白名单按实测出口 IP 刷新")

        target_ip = real_ip or event_ip
        refreshed = False
        if target_ip:
            refreshed = self._refresh_whitelist(target_ip)

        ok, detail = self._wait_whitelist()
        delivered = False
        if ok:
            delivered = self._send(title, text)
            mode = "企微应用"
        else:
            logger.error(f"stunnotifyguard: 白名单验证未通过（{self._probe_max_sec}s，最后={detail}），降级群机器人")
            delivered = self._fallback(title, text + "\n(企微应用白名单未生效，已降级发送)")
            mode = "群机器人降级"

        self._record({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": title,
            "text": text,
            "event_ip": event_ip,
            "real_ip": real_ip,
            "whitelist": "ok" if ok else f"fail({detail})",
            "refreshed": refreshed,
            "delivered": delivered,
            "mode": mode,
        })

    def _public_ip(self) -> str:
        ts, val = self._ip_cache
        if val and time.time() - ts < 30:
            return val
        for url in IP_SOURCES:
            try:
                r = requests.get(url, timeout=5, headers={"User-Agent": "curl/8"})
                if r.status_code == 200:
                    m = IP_RE.search(r.text)
                    if m:
                        self._ip_cache = (time.time(), m.group(1))
                        return m.group(1)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"stunnotifyguard: 探测出口 IP 失败 {url} {exc}")
        return val

    def _refresh_whitelist(self, ip: str) -> bool:
        """优先复用同进程里已加载的 updatewechatipmodern 插件实例，失败回退 HTTP 接口。"""
        try:
            from app.runtime.extensions.plugin.manager import PluginManager
            manager = PluginManager.get_existing_instance()
            inst = (manager.running_plugins or {}).get("updatewechatipmodern") if manager else None
            if inst is not None:
                inst.UpdateIp(ip)
                logger.info(f"stunnotifyguard: 已通过插件实例刷新白名单 {ip}")
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"stunnotifyguard: 插件实例刷新白名单失败：{exc}")

        try:
            r = requests.get(
                "http://127.0.0.1:3000/api/v1/plugin/updatewechatipmodern/UpdateIP",
                params={"apikey": settings.API_TOKEN, "ip": ip}, timeout=30)
            logger.info(f"stunnotifyguard: HTTP 刷新白名单 {ip} -> {r.status_code}")
            return r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.error(f"stunnotifyguard: HTTP 刷新白名单失败：{exc}")
            return False

    # ------------------------------------------------------------------ 探针
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        r = requests.get(f"{WECOM}/gettoken",
                         params={"corpid": self._corpid, "corpsecret": self._secret}, timeout=10)
        data = r.json()
        if data.get("errcode") != 0 or not data.get("access_token"):
            raise RuntimeError(f"gettoken 失败 {data.get('errcode')} {data.get('errmsg')}")
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 7200))
        return self._token

    def _probe(self) -> Tuple[bool, str]:
        token = self._access_token()
        r = requests.post(f"{WECOM}/message/send", params={"access_token": token}, timeout=10, json={
            "touser": "__ipcheck__", "msgtype": "text", "agentid": self._agentid,
            "text": {"content": "ip whitelist probe"},
        })
        code = r.json().get("errcode")
        if code == 60020:
            return False, "60020"
        if code in (40014, 42001):
            self._token = None
            return False, f"token {code}"
        return True, str(code)

    def _wait_whitelist(self) -> Tuple[bool, str]:
        deadline = time.time() + self._probe_max_sec
        detail = "not-probed"
        while time.time() < deadline:
            try:
                ok, detail = self._probe()
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"probe-error {exc}"
            if ok:
                logger.info(f"stunnotifyguard: 白名单验证通过（探针 errcode={detail}）")
                return True, detail
            time.sleep(self._probe_interval)
        return False, detail

    # ------------------------------------------------------------------ 发送
    def _send(self, title: str, text: str) -> bool:
        try:
            token = self._access_token()
            r = requests.post(f"{WECOM}/message/send", params={"access_token": token}, timeout=10, json={
                "touser": "@all", "msgtype": "text", "agentid": self._agentid,
                "text": {"content": f"{title}\n{text}"},
            })
            data = r.json()
            if data.get("errcode") == 0:
                logger.info(f"stunnotifyguard: 已推送 {title}")
                return True
            logger.error(f"stunnotifyguard: 推送失败 {data.get('errcode')} {data.get('errmsg')}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"stunnotifyguard: 推送异常 {exc}")
        return False

    def _fallback(self, title: str, text: str) -> bool:
        if not self._fallback_webhook:
            logger.error("stunnotifyguard: 未配置降级群机器人，无法兜底")
            return False
        try:
            r = requests.post(self._fallback_webhook, timeout=10,
                              json={"msgtype": "text", "text": {"content": f"{title}\n{text}"}})
            ok = r.json().get("errcode") == 0
            logger.info(f"stunnotifyguard: 降级群机器人 {'成功' if ok else '失败'}")
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.error(f"stunnotifyguard: 降级群机器人异常 {exc}")
            return False

    # ------------------------------------------------------------------ 记录
    def _record(self, item: Dict[str, Any]) -> None:
        try:
            self._records = ([item] + (self._records or []))[:30]
            self.save_data("records", self._records)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"stunnotifyguard: 保存记录失败 {exc}")

    # ------------------------------------------------------------------ UI
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "enabled", "label": "启用插件", "color": "primary"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "agentid", "label": "应用 AgentId", "type": "number"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "probe_max_sec", "label": "白名单探针超时（秒）",
                                              "type": "number", "placeholder": "30"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "corpid", "label": "企业 ID（CorpID）"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "secret", "label": "应用 Secret", "type": "password"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "fallback_webhook",
                                              "label": "降级群机器人 Webhook（白名单验证失败时用）",
                                              "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [{
                                    "component": "VAlert",
                                    "props": {"type": "info", "variant": "tonal", "density": "compact"},
                                    "content": [{
                                        "component": "span",
                                        "text": "把 lucky 的 STUN 全局 Webhook 指向 "
                                                "/api/v1/plugin/stunnotifyguard/notify?apikey=<API_TOKEN>；"
                                                "白名单始终按本机实测出口 IP 刷新，不使用事件里的地址。",
                                    }],
                                }],
                            },
                        ],
                    },
                ],
            },
        ], {
            "enabled": False,
            "corpid": "",
            "secret": "",
            "agentid": 0,
            "fallback_webhook": "",
            "probe_max_sec": 30,
            "dedup_window": 600,
        }

    def get_page(self) -> List[dict]:
        rows = []
        for item in (self._records or [])[:15]:
            rows.append({
                "component": "VRow",
                "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 3},
                     "content": [{"component": "span", "text": str(item.get("time", ""))}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3},
                     "content": [{"component": "span", "text": str(item.get("real_ip", ""))}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3},
                     "content": [{"component": "span", "text": f"{item.get('whitelist','')} / {item.get('mode','')}"}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3},
                     "content": [{"component": "span", "text": str(item.get("title", ""))}]},
                ],
            })
        if not rows:
            return [{"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
                     "text": "暂无记录。lucky 的 STUN 地址变化时会在这里留下一条。"}]
        return [{
            "component": "VCard",
            "props": {"variant": "tonal", "title": "最近处理记录"},
            "content": rows,
        }]

    def stop_service(self):
        pass
