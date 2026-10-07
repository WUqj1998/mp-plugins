"""
动态企微可信IP 插件 v5.1.0
基于MP CookieCloudHelper自动获取Cookie，删除二维码登录功能
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests
from apscheduler.triggers.cron import CronTrigger

from app.helper.cookiecloud import CookieCloudHelper
from app.log import logger
from app.plugins import _PluginBase


class UpdateWeChatIp(_PluginBase):
    # 插件基本信息
    plugin_name = "动态企微可信IP"
    plugin_desc = "修改企微应用可信IP，自动从CookieCloud获取Cookie"
    plugin_icon = "Wecom_A.png"
    plugin_version = "5.1.5"
    plugin_author = "书小白"
    author_url = "https://github.com/thshu/MoviePilot-Plugins"
    plugin_config_prefix = "UpdateWeChatIp_"
    plugin_order = 50
    auth_level = 1

    # 运行时状态
    _enabled = False
    _se = None
    _wwrtx_sid = None
    _party_cache_data = None
    _app_id = ""
    _ip = None
    _is_login = False
    _cron = "*/30 * * * *"

    _UpdateLogKey = 'UpdateLog'
    _MaxLogEntries = 100  # 日志最大条数
    
    # Cookie获取方式: 'manual'=手动输入, 'cookiecloud'=CookieCloud服务器
    _run_now = False  # 立即运行触发器（不保存）
    
    # Cookie获取方式: 'manual'=手动输入, 'cookiecloud'=CookieCloud服务器
    _run_now = False  # 立即运行触发器（不保存）

    # IP检测服务列表（已去重）
    _ip_urls = [
        "https://myip.ipip.net",
        "https://ip.3322.net",
        "https://ddns.oray.com/checkip",
        "https://4.ipw.cn",
    ]
    _ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'

    # 请求头
    _headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        'Accept-Encoding': "gzip, deflate, br, zstd",
        'pragma': "no-cache",
        'cache-control': "no-cache",
        'x-requested-with': "XMLHttpRequest",
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': "?0",
        'sec-fetch-site': "same-origin",
        'sec-fetch-mode': "cors",
        'sec-fetch-dest': "empty",
        'referer': "https://work.weixin.qq.com/wework_admin/frame",
        'accept-language': "zh-CN,zh;q=0.9",
    }

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        config = config or {}
        self._enabled = bool(config.get("_enabled"))
        self._app_id = config.get("_app_id", "")
        self._cron = config.get("_cron", "")
        self._cookie_source = config.get("_cookie_source", "cookiecloud")
        
        # 根据配置获取Cookie（初始化时优先使用缓存）
        if self._cookie_source == "manual":
            # 手动输入模式 - 解析完整Cookie
            raw_cookie = config.get("_wwrtx_cookie", "")
            if raw_cookie:
                self._wwrtx_sid = self._parse_cookie_string(raw_cookie)
                if self._wwrtx_sid:
                    logger.info(f"✅ 使用手动输入的Cookie (长度: {len(self._wwrtx_sid)})")
                else:
                    logger.warning("⚠️ Cookie解析失败，未找到wwrtx.sid")
            else:
                logger.warning("⚠️ 手动模式但未输入Cookie")
        else:
            # CookieCloud模式 - 初始化时使用缓存，由check方法定期更新
            raw_cookie = config.get("_wwrtx_cookie", "")
            if raw_cookie:
                self._wwrtx_sid = self._parse_cookie_string(raw_cookie)
                if self._wwrtx_sid:
                    logger.info(f"✅ 使用缓存Cookie (长度: {len(self._wwrtx_sid)})")
                else:
                    logger.warning("⚠️ 缓存Cookie解析失败")
            else:
                logger.info("ℹ️ 无缓存Cookie，将在定时任务时获取")

        # 初始化Session
        self._ensure_session()
        
        # 检查是否需要立即运行
        if config and config.get("_run_now"):
            logger.info("🚀 检测到立即运行请求，开始执行...")
            try:
                self.check()
                logger.info("✅ 立即运行完成")
            except Exception as e:
                logger.error(f"❌ 立即运行失败: {e}", exc_info=True)
            finally:
                # 重置触发器（不清除数据库，只清除本次触发状态）
                pass

        # 检查登录状态
        self._party_cache()
        if self._is_login:
            party_name = self._get_party_name()
            logger.info(f"✅ 登录成功，企业名称: {party_name}")
        else:
            logger.warning("❌ 登录状态检查失败，请检查sid是否有效")

    def _ensure_session(self):
        """确保Session已初始化"""
        if not hasattr(self, '_se') or self._se is None:
            self._se = requests.Session()
        if self._wwrtx_sid:
            self._se.cookies.set('wwrtx.sid', self._wwrtx_sid)
        return self._se

    def _parse_cookie_string(self, cookie_str: str) -> str:
        """从完整Cookie字符串中提取wwrtx.sid"""
        if not cookie_str:
            return ""
        try:
            # 查找wwrtx.sid
            match = re.search(r'wwrtx\.sid=([^;\s]+)', cookie_str)
            if match:
                return match.group(1)
            logger.warning("Cookie中未找到wwrtx.sid")
            return ""
        except Exception as e:
            logger.error(f"Cookie解析异常: {e}")
            return ""

    def _get_cookie_from_cookiecloud(self) -> str:
        """从MP的CookieCloudHelper获取企业微信cookie"""
        try:
            helper = CookieCloudHelper()
            cookies, error = helper.download()

            if error:
                logger.warning(f"CookieCloud下载失败: {error}")
                return None

            if not cookies:
                logger.warning("CookieCloud返回数据为空")
                return None

            # 查找企业微信域名
            ww_domain = '.work.weixin.qq.com'
            alt_domain = 'work.weixin.qq.com'

            # 尝试带点和不带点的域名
            for domain in [ww_domain, alt_domain]:
                if domain in cookies:
                    cookie_str = cookies[domain]
                    # 从cookie字符串中提取wwrtx.sid
                    match = re.search(r'wwrtx\.sid=([^;]+)', cookie_str)
                    if match:
                        sid = match.group(1)
                        logger.info(f"✅ 从CookieCloud成功获取wwrtx.sid (长度: {len(sid)})")
                        return sid

            logger.warning("CookieCloud中未找到企业微信cookie")
            logger.debug(f"可用域名: {list(cookies.keys())[:10]}")
            return None

        except Exception as e:
            logger.error(f"CookieCloud请求异常: {e}")
            return None

    def get_state(self) -> bool:
        """返回插件当前是否启用"""
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时服务"""
        if self._enabled and self._cron:
            # 获取最新配置
            from app.db.systemconfig_oper import SystemConfigOper
            db = SystemConfigOper()
            config = db.get("plugin.UpdateWeChatIp") or {}
            
            return [
                {
                    "id": self.__class__.__name__,
                    "name": f"{self.__class__.__name__}_{self.plugin_name}服务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.check,
                    "kwargs": {"config": config}
                },
            ]
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """注册API"""
        return [
            {
                "path": "/UpdateIP",
                "endpoint": self.UpdateIp,
                "methods": ["GET"],
                "auth": "apikey",
                "summary": "更新企业微信IP白名单",
                "description": "更新企业微信IP白名单,需要传递查询参数,参数名为:ip"
            },
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回配置表单"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': '_enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': '_cron',
                                            'label': '检测周期',
                                            'placeholder': '*/10 * * * *'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': '_cookie_source',
                                            'label': 'Cookie获取方式',
                                            'items': [
                                                {'title': '手动输入', 'value': 'manual'},
                                                {'title': 'CookieCloud服务器', 'value': 'cookiecloud'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': '_wwrtx_cookie',
                                            'label': '完整Cookie',
                                            'placeholder': '手动输入时粘贴完整Cookie字符串，留空则从CookieCloud获取',
                                            'show-label': 'true',
                                            'v-show': '_cookie_source === "manual"'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': '_app_id',
                                            'label': '应用ID',
                                            'placeholder': '输入应用ID,多个使用英文逗号隔开'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': '_run_now',
                                            'label': '立即运行一次',
                                            'hint': '保存配置后自动执行一次完整流程，执行后自动重置'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "_enabled": False,
            "_wwrtx_cookie": "",
            "_cookie_source": "cookiecloud",
            "_app_id": "5629500525638859",
            "_cron": '*/10 * * * *'
        }

    def get_page(self) -> List[dict]:
        """返回详情页"""
        # 确保Session已初始化
        self._ensure_session()
        
        # 刷新登录状态
        self._party_cache()
        
        # 获取并排序更新日志
        raw_data = self.get_data(self._UpdateLogKey) or []
        update_log: List[UpdateLogDto] = [UpdateLogDto.from_dict(i) for i in raw_data]
        data_list = sorted(update_log, key=lambda x: x.UpdateTime, reverse=True)
        
        # 限制日志数量
        if len(data_list) > self._MaxLogEntries:
            data_list = data_list[:self._MaxLogEntries]
            self.save_data(self._UpdateLogKey, [i.to_dict() for i in data_list])
        
        update_log_trs = [
            {
                "component": "tr",
                "props": {"class": "text-sm"},
                "content": [
                    {
                        "component": "td",
                        "props": {"style": {"color": "red"}} if not data.status else {},
                        "text": "成功" if data.status else "失败",
                    },
                    {"component": "td", "text": data.app_id},
                    {"component": "td", "text": data.ip},
                    {"component": "td", "text": data.result},
                    {"component": "td",
                     "text": data.UpdateTime.strftime('%Y-%m-%d %H:%M:%S') if data.UpdateTime else ""},
                ],
            }
            for data in data_list
        ]

        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            # 顶部状态标题
                            {
                                "component": "div",
                                "props": {
                                    "style": {
                                        "display": "flex",
                                        "justifyContent": "center",
                                        "alignItems": "center",
                                        "flexDirection": "column",
                                        "gap": "10px",
                                        "marginBottom": "20px",
                                    }
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "有效" if self._is_login else "失效",
                                        "props": {
                                            "style": {
                                                "fontSize": "22px",
                                                "fontWeight": "bold",
                                                "color": "#ffffff",
                                                "backgroundColor": "#9B50FF",
                                                "padding": "8px 16px",
                                                "borderRadius": "5px",
                                                "textAlign": "center",
                                                "display": "inline-block",
                                            }
                                        },
                                    }
                                ],
                            },
                            # 日志表格
                            {
                                "component": "VTable",
                                "props": {"hover": True},
                                "content": [
                                    {
                                        "component": "thead",
                                        "props": {"class": "text-no-wrap"},
                                        "content": [
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "状态",
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "appId",
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "更新IP",
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "返回值",
                                            },
                                            {
                                                "component": "th",
                                                "props": {"class": "text-start ps-4"},
                                                "text": "更新时间",
                                            },
                                        ],
                                    },
                                    {
                                        "component": "tbody",
                                        "content": update_log_trs,
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }
        ]

    def stop_service(self):
        """停止服务"""
        pass

    def _party_cache(self) -> bool:
        """检查登录状态（不获取Cookie，使用已缓存的sid）"""
        if not self._wwrtx_sid:
            return False

        self._ensure_session()
        url = "https://work.weixin.qq.com/wework_admin/contacts/party/cache"
        params = {
            'lang': "zh_CN",
            'f': "json",
            'ajax': "1",
            'timeZoneInfo[zone_offset]': "+8",
        }
        try:
            res = self._se.post(url, params=params, headers=self._headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                # 判断登录状态：有errCode=0 或 返回data包含party_list
                if data.get('errCode') == 0 or (data.get('data') and data['data'].get('party_list')):
                    self._party_cache_data = data.get('data', data)
                    self._is_login = True
                    return True
                else:
                    self._party_cache_data = data
            else:
                logger.error(f"获取企业微信部门缓存失败，HTTP状态码：{res.status_code}")
        except Exception as e:
            logger.error(f"获取企业微信部门缓存异常: {e}")

        self._is_login = False
        return False

    def _get_party_name(self) -> str:
        """安全获取企业名称"""
        if not self._party_cache_data:
            return "未知"
        party_list = self._party_cache_data.get("party_list", {}).get("list")
        if not party_list:
            return "未知"
        return party_list[0].get("name", "未知")

    def _save_ip_config(self):
        """保存IP配置到企业微信（实际API调用）"""
        _update_log = []
        url = 'https://work.weixin.qq.com/wework_admin/apps/saveIpConfig?lang=zh_CN&f=json&ajax=1'
        
        for appId in self._app_id.split(','):
            appId = appId.strip()
            if not appId:
                continue
            
            data = {
                'app_id': appId,
                'ipList[]': self._ip
            }
            res = self._se.post(url, data=data, headers=self._headers)
            
            success = 'err' not in res.text
            status_msg = "成功" if success else "失败"
            
            if success:
                logger.info(f'{appId}更新白名单成功，更新IP为：{self._ip}')
            else:
                logger.error(f'{appId}更新IP白名单失败，返回值：{self._filter_sensitive(res.text[:100])}')

            # 解析API返回值为JSON
            try:
                api_result = res.json()
                result_str = str(api_result)
            except:
                result_str = res.text[:200] if res.text else status_msg
            
            _update_log.append(UpdateLogDto(
                status=success,
                ip=self._ip,
                app_id=appId,
                result=result_str
            ))

        # 保存日志
        update_log = [UpdateLogDto.from_dict(i) for i in self.get_data(self._UpdateLogKey) or []]
        all_logs = update_log + _update_log
        
        # 清理旧日志
        if len(all_logs) > self._MaxLogEntries:
            all_logs = sorted(all_logs, key=lambda x: x.UpdateTime, reverse=True)[:self._MaxLogEntries]
        
        self.save_data(self._UpdateLogKey, [i.to_dict() for i in all_logs])

    def UpdateIp(self, ip: str):
        """手动触发IP更新"""
        self._ip = ip
        self._save_ip_config()

    def get_ip_from_url(self) -> str:
        """获取公网IP"""
        for url in self._ip_urls:
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    ip_address = re.search(self._ip_pattern, response.text)
                    if ip_address:
                        return ip_address.group()
            except Exception as e:
                if 'Read timed out' not in str(e):
                    logger.warning(f"{url} 获取IP失败, Error: {e}")
        return "获取IP失败"

    def _get_corp_app_v2(self) -> dict:
        """获取企业应用配置"""
        if not self._app_id:
            logger.error("未配置应用ID")
            return {}
        
        app_id = self._app_id.split(",")[0].strip()
        url = f'https://work.weixin.qq.com/wework_admin/apps/getCorpAppV2?lang=zh_CN&f=json&ajax=1&app_id={app_id}'
        try:
            res = self._se.get(url, timeout=10)
            if res.status_code == 200:
                return res.json().get('data', {})
            else:
                logger.error(f"获取企业应用配置失败，HTTP状态码：{res.status_code}")
        except Exception as e:
            logger.error(f"获取企业应用配置异常: {e}")
        return {}

    def check(self, config: dict = None):
        """定时检测"""
        # 获取最新配置
        if not config:
            from app.db.systemconfig_oper import SystemConfigOper
            db = SystemConfigOper()
            config = db.get("plugin.UpdateWeChatIp") or {}
        
        if not self._enabled:
            logger.debug("插件未开启")
            return

        try:
            # 获取Cookie（优先使用缓存，失效时才从CookieCloud获取）
            logger.info("开始执行IP检测...")
            
            # 先尝试使用数据库缓存的Cookie
            cached_cookie = config.get("_wwrtx_cookie", "") if config else ""
            if cached_cookie:
                self._wwrtx_sid = self._parse_cookie_string(cached_cookie)
                if self._wwrtx_sid:
                    logger.info(f"✅ 使用缓存Cookie (长度: {len(self._wwrtx_sid)})")
            
            # 如果缓存Cookie无效，尝试从CookieCloud获取
            if not self._wwrtx_sid:
                logger.info("缓存Cookie失效，尝试从CookieCloud获取...")
                self._wwrtx_sid = self._get_cookie_from_cookiecloud()
                if self._wwrtx_sid:
                    # 保存新Cookie到数据库
                    from app.db.systemconfig_oper import SystemConfigOper
                    db = SystemConfigOper()
                    new_config = db.get("plugin.UpdateWeChatIp") or {}
                    new_config["_wwrtx_cookie"] = f"wwrtx.sid={self._wwrtx_sid}"
                    db.set("plugin.UpdateWeChatIp", new_config)
                    logger.info("✅ CookieCloud获取成功，已更新缓存")
                else:
                    logger.error("❌ Cookie获取失败")
                    return
            else:
                logger.info(f"Cookie有效，长度: {len(self._wwrtx_sid)}")

            # 检查登录状态
            logger.info("检查登录状态...")
            self._party_cache()
            if not self._is_login:
                logger.error("未登录")
                self.post_message(
                    title="企业微信登录状态失效",
                    text='企业微信登录已过期，请检查CookieCloud中的cookie'
                )
                return
            logger.info("登录状态检查通过")

            # 获取当前公网IP
            logger.info("获取公网IP...")
            self._ip = self.get_ip_from_url()
            if not self._ip or self._ip == "获取IP失败":
                logger.error("获取当前公网IP失败，跳过本次检测")
                return
            logger.info(f"公网IP获取成功: {self._ip}")

            # 获取应用配置并比较IP
            # 从数据库读取缓存
            from app.db.systemconfig_oper import SystemConfigOper
            db = SystemConfigOper()
            cached_config = db.get("plugin.UpdateWeChatIp") or {}
            cached_ip = cached_config.get("_last_checked_ip")
            cached_app_config = cached_config.get("_last_app_config")
            
            # 如果IP没变且已有缓存配置，跳过API调用
            if cached_ip == self._ip and cached_app_config is not None:
                logger.info(f"IP未变化 ({self._ip})，使用缓存配置")
                app_config = cached_app_config
            else:
                logger.info("获取应用配置...")
                app_config = self._get_corp_app_v2()
                # 保存缓存到数据库
                cached_config["_last_checked_ip"] = self._ip
                cached_config["_last_app_config"] = app_config
                db.set("plugin.UpdateWeChatIp", cached_config)
            
            app_config_ips = app_config.get('app', {}).get('white_ip_list', {}).get('ip', [])
            logger.info(f"当前白名单IP: {app_config_ips}")

            ip_updated = False
            if self._ip not in app_config_ips:
                logger.info(f"IP {self._ip} 不在白名单中，准备更新...")
                try:
                    self._save_ip_config()
                    ip_updated = True
                    logger.info("✅ IP更新成功")
                except Exception as e:
                    logger.error(f"❌ IP更新失败: {e}")
            else:
                logger.info(f"IP {self._ip} 已在白名单中，无需更新")
                ip_updated = True  # 已在白名单中，算成功

        except Exception as e:
            logger.error(f"check方法执行异常: {e}", exc_info=True)
        
        # 记录执行日志
        try:
            _update_log = []
            if hasattr(self, '_ip') and self._ip and self._is_login:
                result_text = f"IP检测完成,当前IP:{self._ip}"
                if not hasattr(self, '_wwrtx_sid') or not self._wwrtx_sid:
                    result_text += ",Cookie失效"
                elif not ip_updated:
                    result_text += ",IP更新失败"
                _update_log.append(UpdateLogDto(
                    status=ip_updated,
                    ip=self._ip,
                    app_id=config.get('_app_id', 'N/A') if config else 'N/A',
                    result=result_text
                ))
            
            update_log = [UpdateLogDto.from_dict(i) for i in self.get_data(self._UpdateLogKey) or []]
            all_logs = update_log + _update_log
            
            if len(all_logs) > self._MaxLogEntries:
                all_logs = sorted(all_logs, key=lambda x: x.UpdateTime, reverse=True)[:self._MaxLogEntries]
            
            self.save_data(self._UpdateLogKey, [i.to_dict() for i in all_logs])
        except Exception as e:
            logger.error(f"保存执行日志失败: {e}")
        
        finally:
            # 清除立即运行触发器
            self._clear_run_now_trigger()

    def _clear_run_now_trigger(self):
        """清除立即运行触发器"""
        from app.db.systemconfig_oper import SystemConfigOper
        db = SystemConfigOper()
        config = db.get("plugin.UpdateWeChatIp") or {}
        if config.get("_run_now"):
            config["_run_now"] = False
            db.set("plugin.UpdateWeChatIp", config)
            logger.debug("已清除立即运行触发器")

    def _filter_sensitive(self, text: str) -> str:
        """过滤敏感信息"""
        text = re.sub(r'wwrtx\.sid=[^;\\s]+', 'wwrtx.sid=***', text)
        text = re.sub(r'wwrtx\.vst=[^;\\s]+', 'wwrtx.vst=***', text)
        return text

    def _truncate_str(self, text: str, max_len: int = 50) -> str:
        """截断字符串"""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."


@dataclass
class UpdateLogDto:
    """更新日志数据类"""
    status: bool
    ip: str
    app_id: str
    result: str
    UpdateTime: datetime = None

    def __post_init__(self):
        if self.UpdateTime is None:
            self.UpdateTime = datetime.now()

    def to_dict(self):
        return {
            "status": self.status,
            "ip": self.ip,
            "app_id": self.app_id,
            "result": self.result,
            "UpdateTime": self.UpdateTime.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict):
        kwargs = dict(data)
        kwargs['UpdateTime'] = datetime.fromisoformat(kwargs.pop('UpdateTime'))
        return cls(**kwargs)
