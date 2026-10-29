import warnings
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, Any, List, Dict, Tuple

import pytz
from app.helper.sites import SitesHelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.site import SiteChain
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.db.models.site import Site
from app.db.models.siteuserdata import SiteUserData
from app.schemas import SiteUserData as SSiteUserData
from app.db.site_oper import SiteOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.string import StringUtils

warnings.filterwarnings("ignore", category=FutureWarning)

lock = Lock()


def _row_from_snapshot(domain: str, item: dict) -> SiteUserData:
    """
    把持久化快照项还原为站点数据行（仅用于缺数据时兜底，不入库）
    """
    row = SiteUserData()
    row.domain = domain
    row.name = item.get("name") or domain
    row.upload = float(item.get("upload") or 0)
    row.download = float(item.get("download") or 0)
    row.updated_day = item.get("day") or ""
    row.updated_time = item.get("time") or ""
    row.err_msg = ""
    return row


def _calc_inc(today_row: SiteUserData, baseline_row: Optional[SiteUserData]) -> Tuple[int, int, int]:
    """
    计算今日增量：返回 (上传增量, 下载增量, 基线缺口天数)
    没有基线时增量记 0（首次出现的站点不应该把历史累计当今日增量）；
    站点累计值回退时按 0 处理，不写负数。
    """
    if today_row is None or baseline_row is None:
        return 0, 0, 0
    try:
        up_now = float(today_row.upload or 0)
        dn_now = float(today_row.download or 0)
        up_base = float(baseline_row.upload or 0)
        dn_base = float(baseline_row.download or 0)
    except (TypeError, ValueError):
        return 0, 0, 0
    up = int(up_now - up_base)
    dn = int(dn_now - dn_base)
    if up < 0:
        up = 0
    if dn < 0:
        dn = 0
    gap = 0
    try:
        day_now = datetime.strptime((today_row.updated_day or "")[:10], "%Y-%m-%d")
        day_base = datetime.strptime((baseline_row.updated_day or "")[:10], "%Y-%m-%d")
        gap = max((day_now - day_base).days - 1, 0)
    except ValueError:
        gap = 0
    return up, dn, gap


class sitedailystatisticmodern(_PluginBase):
    # 插件名称
    plugin_name = "站点每日数据统计(本地版)"
    # 插件描述
    plugin_desc = "站点统计数据图表及当天累计站点数据通知"
    # 插件图标
    plugin_icon = "Collabora_A.png"
    # 插件版本
    plugin_version = "4.2"
    # 插件作者
    plugin_author = "Xiang（本地定制 · AI 修改）"
    # 作者主页
    author_url = "https://github.com/xiangt920"
    # 插件配置项ID前缀
    plugin_config_prefix = "sitedailystatisticmodern_"
    # 加载顺序
    plugin_order = 99
    plugin_label = "本地"
    # 可使用的用户级别
    auth_level = 2

    # 配置属性
    siteoper = None
    siteshelper = None
    sitechain = None
    _enabled: bool = False
    _onlyonce: bool = False
    _dashboard_type: str = "today"
    _notify_type = ""
    _focus_sites: list = []
    _inc_baseline_enabled: bool = True
    _scheduler = None

    # 站点累计值持久化快照的 plugindata key
    SNAPSHOT_KEY = "site_totals"

    def init_plugin(self, config: dict = None):
        self.siteoper = SiteOper()
        self.siteshelper = SitesHelper()
        self.sitechain = SiteChain()

        # 停止现有任务
        self.stop_service()

        # 配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._dashboard_type = config.get("dashboard_type") or "today"
            self._notify_type = config.get("notify_type") or ""
            self._focus_sites = config.get("focus_sites") or []
            baseline_enabled = config.get("inc_baseline_enabled")
            self._inc_baseline_enabled = True if baseline_enabled is None else bool(baseline_enabled)

        if self._onlyonce:
            config["onlyonce"] = False
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(self.sitechain.refresh_userdatas, "date",
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="站点数据统计服务")
            self._scheduler.print_jobs()
            self._scheduler.start()
            self.update_config(config=config)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        return [{
            "path": "/refresh_daily_by_domain",
            "endpoint": self.refresh_by_domain,
            "methods": ["GET"],
            "summary": "刷新站点今日数据",
            "description": "刷新对应域名的站点今日数据",
        }]

    def get_service(self) -> List[Dict[str, Any]]:
        ret_jobs = []
        # 添加一个每日0点的统计任务，仅在该任务中保存数据
        ret_jobs.append({
            "id": "sitedailystatisticmodern00",
                "name": "站点每日数据统计服务",
                "trigger": CronTrigger.from_crontab('59 23 * * *'),
                "func": self.refresh_all_sites,
                "kwargs": {}
        })
        return ret_jobs

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 关注站点可选项（与 SiteUserData.name 对齐，故取站点名做 value）
        site_options = [{"title": site.name, "value": site.name}
                        for site in SiteOper().list_order_by_pri()]
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'dashboard_type',
                                            'label': '仪表板组件',
                                            'items': [
                                                {'title': '今日数据', 'value': 'today'},
                                                {'title': '汇总数据', 'value': 'total'},
                                                {'title': '所有数据', 'value': 'all'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'notify_type',
                                            'label': '数据刷新时发送通知',
                                            'items': [
                                                {'title': '不发送', 'value': ''},
                                                {'title': '今日增量数据', 'value': 'inc'},
                                                {'title': '累计全量数据', 'value': 'all'}
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'inc_baseline_enabled',
                                            'label': '增量基线持久化',
                                            'hint': '站点采集失败或某天缺数据时，用上次有效累计值做基线，避免恢复后把历史累计当成当日增量',
                                            'persistent-hint': True,
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
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'focus_sites',
                                            'label': '关注站点（仅推送这些站点的数据，留空则全部推送）',
                                            'items': site_options
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "dashboard_type": 'today',
            "focus_sites": [],
            "inc_baseline_enabled": True
        }

    @eventmanager.register(EventType.SiteRefreshed)
    def send_msg(self, event: Event):
        """
        站点数据刷新事件时发送消息
        """
        if not self._notify_type:
            return
        if event.event_data.get('site_id') != "*":
            return
        # 获取统计数据（今日行、基线行、持久化快照）
        ctx = self.__get_data()
        today_data = ctx["today_data"]
        baseline_map = ctx["baseline_map"]
        snapshot = ctx["snapshot"]
        today_date = ctx["today"]
        # 消息内容
        messages = {}
        # 总上传
        incUploads = 0
        # 总下载
        incDownloads = 0

        for rand, row in enumerate(today_data):
            site = row.name
            # 关注站点过滤：配置了则只推送列表内的站点
            if self._focus_sites and site not in self._focus_sites:
                continue
            upload = int(float(row.upload or 0))
            download = int(float(row.download or 0))
            baseline = baseline_map.get(row.domain)
            gap = 0
            if self._notify_type == "inc":
                upload, download, gap = _calc_inc(row, baseline)

            note = ""
            if gap > 0 and baseline is not None:
                note = f"（基线 {baseline.updated_day}，补 {gap} 天）"
            elif row.updated_day and row.updated_day != today_date:
                note = f"（{row.updated_day}）"

            if upload > 0 or download > 0:
                incUploads += upload
                incDownloads += download
                messages[upload + (rand / 1000)] = (
                        f"【{site}】{note}\n"
                        + f"上传量：{StringUtils.str_filesize(upload)}\n"
                        + f"下载量：{StringUtils.str_filesize(download)}\n"
                        + "————————————"
                )

        # 今日采集失败的站点：有历史快照但今天没有数据行，显式提示而不是静默消失
        stale_sites = []
        if self._notify_type == "inc":
            today_domains = {row.domain for row in today_data}
            for domain, item in (snapshot or {}).items():
                if domain in today_domains:
                    continue
                last_day = item.get("day") or ""
                try:
                    missing_days = (datetime.strptime(today_date, "%Y-%m-%d")
                                    - datetime.strptime(last_day[:10], "%Y-%m-%d")).days
                except ValueError:
                    continue
                # 只提示 7 天内有有效数据的站点，避免历史残留长期占位
                if missing_days > 7:
                    continue
                name = item.get("name") or domain
                if self._focus_sites and name not in self._focus_sites:
                    continue
                stale_sites.append(f"{name}（上次 {last_day}）")

        if incDownloads or incUploads or stale_sites:
            sorted_messages = [messages[key] for key in sorted(messages.keys(), reverse=True)]
            sorted_messages.insert(0, f"【汇总】\n"
                                      f"总上传：{StringUtils.str_filesize(incUploads)}\n"
                                      f"总下载：{StringUtils.str_filesize(incDownloads)}\n"
                                      f"————————————")
            if stale_sites:
                sorted_messages.append("⚠️ 今日未采集成功：" + "、".join(stale_sites))
            self.post_message(mtype=NotificationType.SiteMessage,
                              title="站点数据统计", text="\n".join(sorted_messages))

    def _load_snapshot(self) -> Dict[str, dict]:
        """
        读取站点累计值持久化快照（存 MP plugindata，容器重启/插件重载不丢）
        """
        try:
            data = self.get_data(self.SNAPSHOT_KEY)
        except Exception as e:
            logger.error(f"读取站点累计快照失败：{str(e)}")
            return {}
        return data if isinstance(data, dict) else {}

    def _update_snapshot(self, rows: List[SiteUserData]) -> Dict[str, dict]:
        """
        把本次采集到的有效累计值写入快照。
        采集失败（err_msg 非空）、累计值为 0、日期比快照旧的场景一律不覆盖，
        保证快照里始终是「最近一次成功获取到的累计值」。
        """
        snapshot = self._load_snapshot()
        changed = False
        for row in rows or []:
            if row is None or not getattr(row, "domain", None):
                continue
            if (getattr(row, "err_msg", "") or "").strip():
                continue
            try:
                upload = float(row.upload or 0)
                download = float(row.download or 0)
            except (TypeError, ValueError):
                continue
            if upload <= 0:
                continue
            day = row.updated_day or datetime.now().strftime("%Y-%m-%d")
            old = snapshot.get(row.domain) or {}
            if (old.get("day") or "") > day:
                continue
            item = {
                "day": day,
                "time": row.updated_time or "",
                "name": row.name,
                "upload": int(upload),
                "download": int(download),
            }
            if (old.get("day") == item["day"]
                    and old.get("upload") == item["upload"]
                    and old.get("download") == item["download"]):
                continue
            snapshot[row.domain] = item
            changed = True
        if changed:
            try:
                self.save_data(self.SNAPSHOT_KEY, snapshot)
            except Exception as e:
                logger.error(f"保存站点累计快照失败：{str(e)}")
        return snapshot

    def __get_data(self) -> Dict[str, Any]:
        """
        获取统计上下文：今日各站点最新行、每个站点的增量基线行、持久化快照。
        基线 = 该站点「今日之前最近一条有效数据」，DB 里没有时用持久化快照兜底，
        因此某天采集失败也不会让今日增量退化成站点历史累计。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        data_list: List[SiteUserData] = self.siteoper.get_userdata() or []
        if not data_list:
            return {"today": today, "today_data": [], "baseline_data": [],
                    "baseline_map": {}, "snapshot": self._load_snapshot()}
        # 每个「站点+日期」只保留 updated_time 最新的一条
        latest: Dict[str, SiteUserData] = {}
        for data in data_list:
            key = f"{data.domain}_{data.updated_day}"
            prev = latest.get(key)
            if prev is None or (data.updated_time or "") >= (prev.updated_time or ""):
                latest[key] = data
        rows = list(latest.values())
        # 今日数据
        today_data = [row for row in rows if (row.updated_day or "") == today]
        today_data.sort(key=lambda x: float(x.upload or 0), reverse=True)
        # 先留存「上次成功采集」的快照：DB 里没有有效旧行时用它兜底当基线
        prev_snapshot = self._load_snapshot()
        # 再落盘快照：今日有效数据 → 持久化
        snapshot = self._update_snapshot(today_data)

        if self._inc_baseline_enabled:
            # 基线：每个站点今日之前最近一条有效数据（跨多天缺失也能补齐）
            baseline_map: Dict[str, SiteUserData] = {}
            for row in rows:
                if not row.domain or (row.updated_day or "") >= today:
                    continue
                # 采集失败时 MP 会写入 upload=0 的脏行（err_msg 还可能为空），
                # 这种行绝不能当基线，否则今日增量会退化成站点历史累计
                try:
                    if float(row.upload or 0) <= 0:
                        continue
                except (TypeError, ValueError):
                    continue
                prev = baseline_map.get(row.domain)
                if prev is None or ((row.updated_day or ""), (row.updated_time or "")) > \
                        ((prev.updated_day or ""), (prev.updated_time or "")):
                    baseline_map[row.domain] = row
            # DB 里没有更早有效行的站点，用持久化快照兜底
            for domain, item in (prev_snapshot or {}).items():
                if domain in baseline_map:
                    continue
                if (item.get("day") or "") >= today:
                    continue
                baseline_map[domain] = _row_from_snapshot(domain, item)
        else:
            # 关闭持久化基线时保持旧行为：只用昨天
            yestoday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            baseline_map = {row.domain: row for row in rows if (row.updated_day or "") == yestoday}

        return {
            "today": today,
            "today_data": today_data,
            "baseline_data": list(baseline_map.values()),
            "baseline_map": baseline_map,
            "snapshot": snapshot,
        }

    @staticmethod
    def __get_total_elements(today: str, stattistic_data: List[SiteUserData], baseline_sites_data: List[SiteUserData],
                             dashboard: str = "today") -> List[dict]:
        """
        获取统计元素
        """

        def __gb(value: int) -> float:
            """
            转换为GB，保留1位小数
            """
            if not value:
                return 0
            return round(float(value) / 1024 / 1024 / 1024, 1)

        if dashboard in ['total', 'all']:
            # 总上传量
            total_upload = sum([data.upload for data in stattistic_data if data.upload])
            # 总下载量
            total_download = sum([data.download for data in stattistic_data if data.download])
            # 总做种数
            total_seed = sum([data.seeding for data in stattistic_data if data.seeding])
            # 总做种体积
            total_seed_size = sum([data.seeding_size for data in stattistic_data if data.seeding_size])

            total_elements = [
                # 总上传量
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6,
                        'md': 3
                    },
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {
                                        'class': 'd-flex align-center',
                                    },
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {
                                                        'src': './plugin_icon/upload.png'
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {
                                                        'class': 'text-caption'
                                                    },
                                                    'text': '总上传量'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {
                                                        'class': 'd-flex align-center flex-wrap'
                                                    },
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {
                                                                'class': 'text-h6'
                                                            },
                                                            'text': StringUtils.str_filesize(total_upload)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                    ]
                },
                # 总下载量
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6,
                        'md': 3,
                    },
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {
                                        'class': 'd-flex align-center',
                                    },
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {
                                                        'src': './plugin_icon/download.png'
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {
                                                        'class': 'text-caption'
                                                    },
                                                    'text': '总下载量'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {
                                                        'class': 'd-flex align-center flex-wrap'
                                                    },
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {
                                                                'class': 'text-h6'
                                                            },
                                                            'text': StringUtils.str_filesize(total_download)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                    ]
                },
                # 总做种数
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6,
                        'md': 3
                    },
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {
                                        'class': 'd-flex align-center',
                                    },
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {
                                                        'src': './plugin_icon/seed.png'
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {
                                                        'class': 'text-caption'
                                                    },
                                                    'text': '总做种数'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {
                                                        'class': 'd-flex align-center flex-wrap'
                                                    },
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {
                                                                'class': 'text-h6'
                                                            },
                                                            'text': f'{"{:,}".format(total_seed)}'
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                    ]
                },
                # 总做种体积
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6,
                        'md': 3
                    },
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal',
                            },
                            'content': [
                                {
                                    'component': 'VCardText',
                                    'props': {
                                        'class': 'd-flex align-center',
                                    },
                                    'content': [
                                        {
                                            'component': 'VAvatar',
                                            'props': {
                                                'rounded': True,
                                                'variant': 'text',
                                                'class': 'me-3'
                                            },
                                            'content': [
                                                {
                                                    'component': 'VImg',
                                                    'props': {
                                                        'src': './plugin_icon/database.png'
                                                    }
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'div',
                                            'content': [
                                                {
                                                    'component': 'span',
                                                    'props': {
                                                        'class': 'text-caption'
                                                    },
                                                    'text': '总做种体积'
                                                },
                                                {
                                                    'component': 'div',
                                                    'props': {
                                                        'class': 'd-flex align-center flex-wrap'
                                                    },
                                                    'content': [
                                                        {
                                                            'component': 'span',
                                                            'props': {
                                                                'class': 'text-h6'
                                                            },
                                                            'text': StringUtils.str_filesize(total_seed_size)
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        else:
            total_elements = []

        if dashboard in ["today", "all"]:
            # 计算增量数据集（基线 = 各站点今日之前最近一条有效数据）
            baseline_map = {base.domain: base for base in (baseline_sites_data or []) if base.domain}
            inc_data = {}
            for data in stattistic_data:
                inc_up, inc_dn, _ = _calc_inc(data, baseline_map.get(data.domain))
                if inc_up > 0 or inc_dn > 0:
                    inc_data[data.name] = {"upload": inc_up, "download": inc_dn}
            # 今日上传
            uploads = {k: v for k, v in inc_data.items() if v.get("upload") if v.get("upload") > 0}
            # 今日上传站点
            upload_sites = [site for site in uploads.keys()]
            # 今日上传数据
            upload_datas = [__gb(data.get("upload")) for data in uploads.values()]
            # 今日上传总量
            today_upload = round(sum(upload_datas), 2)
            # 今日下载
            downloads = {k: v for k, v in inc_data.items() if v.get("download") if v.get("download") > 0}
            # 今日下载站点
            download_sites = [site for site in downloads.keys()]
            # 今日下载数据
            download_datas = [__gb(data.get("download")) for data in downloads.values()]
            # 今日下载总量
            today_download = round(sum(download_datas), 2)
            # 今日上传下载元素
            today_elements = [
                # 上传量图表
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 12,
                        'md': 6
                    },
                    'content': [
                        {
                            'component': 'VApexChart',
                            'props': {
                                'height': 300,
                                'options': {
                                    'chart': {
                                        'type': 'pie',
                                    },
                                    'labels': upload_sites,
                                    'title': {
                                        'text': f'今日上传（{today}）共 {today_upload} GB'
                                    },
                                    'legend': {
                                        'show': True
                                    },
                                    'plotOptions': {
                                        'pie': {
                                            'expandOnClick': False
                                        }
                                    },
                                    'noData': {
                                        'text': '暂无数据'
                                    }
                                },
                                'series': upload_datas
                            }
                        }
                    ]
                },
                # 下载量图表
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 12,
                        'md': 6
                    },
                    'content': [
                        {
                            'component': 'VApexChart',
                            'props': {
                                'height': 300,
                                'options': {
                                    'chart': {
                                        'type': 'pie',
                                    },
                                    'labels': download_sites,
                                    'title': {
                                        'text': f'今日下载（{today}）共 {today_download} GB'
                                    },
                                    'legend': {
                                        'show': True
                                    },
                                    'plotOptions': {
                                        'pie': {
                                            'expandOnClick': False
                                        }
                                    },
                                    'noData': {
                                        'text': '暂无数据'
                                    }
                                },
                                'series': download_datas
                            }
                        }
                    ]
                }
            ]
        else:
            today_elements = []
        # 合并返回
        return total_elements + today_elements

    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """
        获取插件仪表盘页面，需要返回：1、仪表板col配置字典；2、仪表板页面元素配置json（含数据）；3、全局配置（自动刷新等）
        1、col配置参考：
        {
            "cols": 12, "md": 6
        }
        2、页面配置使用Vuetify组件拼装，参考：https://vuetifyjs.com/
        3、全局配置参考：
        {
            "refresh": 10 // 自动刷新时间，单位秒
        }
        """
        # 列配置
        cols = {
            "cols": 12
        }
        # 全局配置
        attrs = {}
        # 获取数据
        ctx = self.__get_data()
        today = ctx["today"]
        stattistic_data = ctx["today_data"]
        baseline_sites_data = ctx["baseline_data"]
        # 汇总
        # 站点统计
        elements = [
            {
                'component': 'VRow',
                'content': self.__get_total_elements(
                    today=today,
                    stattistic_data=stattistic_data,
                    baseline_sites_data=baseline_sites_data,
                    dashboard=self._dashboard_type
                )
            }
        ]
        return cols, attrs, elements

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """

        def format_bonus(bonus):
            try:
                return f'{float(bonus):,.1f}'
            except ValueError:
                return '0.0'

        # 获取数据
        ctx = self.__get_data()
        today = ctx["today"]
        stattistic_data = ctx["today_data"]
        baseline_sites_data = ctx["baseline_data"]
        if not stattistic_data:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]

        # 站点统计
        site_totals = self.__get_total_elements(
            today=today,
            stattistic_data=stattistic_data,
            baseline_sites_data=baseline_sites_data,
            dashboard='all'
        )

        # 站点数据明细
        site_trs = [
            {
                'component': 'tr',
                'props': {
                    'class': 'text-sm'
                },
                'content': [
                    {
                        'component': 'td',
                        'props': {
                            'class': 'whitespace-nowrap break-keep text-high-emphasis'
                        },
                        'text': data.name
                    },
                    {
                        'component': 'td',
                        'text': data.username
                    },
                    {
                        'component': 'td',
                        'text': data.user_level
                    },
                    {
                        'component': 'td',
                        'props': {
                            'class': 'text-success'
                        },
                        'text': StringUtils.str_filesize(data.upload)
                    },
                    {
                        'component': 'td',
                        'props': {
                            'class': 'text-error'
                        },
                        'text': StringUtils.str_filesize(data.download)
                    },
                    {
                        'component': 'td',
                        'text': data.ratio
                    },
                    {
                        'component': 'td',
                        'text': format_bonus(data.bonus or 0)
                    },
                    {
                        'component': 'td',
                        'text': data.seeding
                    },
                    {
                        'component': 'td',
                        'text': StringUtils.str_filesize(data.seeding_size)
                    }
                ]
            } for data in stattistic_data
        ]

        # 拼装页面
        return [
            {
                'component': 'VRow',
                'content': site_totals + [
                    # 各站点数据明细
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                        },
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '站点'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '用户名'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '用户等级'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '上传量'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '下载量'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '分享率'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '魔力值'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '做种数'
                                            },
                                            {
                                                'component': 'th',
                                                'props': {
                                                    'class': 'text-start ps-4'
                                                },
                                                'text': '做种体积'
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': site_trs
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))

    def refresh_by_domain(self, domain: str, apikey: str) -> schemas.Response:
        """
        刷新一个站点数据，可由API调用
        """
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        site_info = self.siteshelper.get_indexer(domain)
        if site_info:
            site_data = SiteChain().refresh_userdata(site=site_info)
            if site_data:
                return schemas.Response(
                    success=True,
                    message=f"站点 {domain} 刷新成功",
                    data=site_data.dict()
                )
            return schemas.Response(
                success=False,
                message=f"站点 {domain} 刷新数据失败，未获取到数据"
            )
        return schemas.Response(
            success=False,
            message=f"站点 {domain} 不存在"
        )
    
    def refresh_all_sites(self):
        """
        每日 23:59：为今日采集失败的站点补一条有效数据（沿用上次成功获取的值），
        保证次日有增量基线可用；随后触发一次全站刷新。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        filled, skipped, failed = 0, 0, 0
        try:
            data_list: List[SiteUserData] = self.siteoper.get_userdata() or []
        except Exception as e:
            logger.error(f"读取站点数据失败，跳过今日补行：{str(e)}")
            data_list = []
        if data_list:
            # 每个站点只保留最近一条
            latest: Dict[str, SiteUserData] = {}
            for data in data_list:
                if not data.domain:
                    continue
                prev = latest.get(data.domain)
                if prev is None or ((data.updated_day or ""), (data.updated_time or "")) > \
                        ((prev.updated_day or ""), (prev.updated_time or "")):
                    latest[data.domain] = data
            # 逐站点补行，单个站点异常不影响其它站点
            for domain, newest in list(latest.items()):
                if (newest.updated_day or "") >= today:
                    continue
                try:
                    site = self.siteoper.get_by_domain(domain)
                    if not site or not getattr(site, "is_active", False):
                        skipped += 1
                        continue
                    payload = SSiteUserData(
                        domain=domain,
                        userid=newest.userid,
                        username=newest.username,
                        user_level=newest.user_level,
                        join_at=newest.join_at,
                        upload=newest.upload,
                        download=newest.download,
                        ratio=newest.ratio,
                        bonus=newest.bonus,
                        seeding=newest.seeding,
                        seeding_size=newest.seeding_size,
                        seeding_info=newest.seeding_info or [],
                        leeching=newest.leeching,
                        leeching_size=newest.leeching_size,
                        message_unread=newest.message_unread,
                        message_unread_contents=newest.message_unread_contents or [],
                        updated_day=today,
                        err_msg=newest.err_msg
                    ).dict()
                    ok, msg = self.siteoper.update_userdata(domain=domain, name=newest.name, payload=payload)
                    if ok:
                        filled += 1
                        logger.info(f"站点{newest.name}没有今日（{today}）数据，以 {newest.updated_day} 的数据补行成功")
                    else:
                        failed += 1
                        logger.warning(f"站点{newest.name}今日数据补行失败：{msg}")
                except Exception as e:
                    failed += 1
                    logger.error(f"站点{newest.name}今日数据补行异常：{str(e)}")
        logger.info(f"站点数据补行完成（{today}）：补行 {filled} 个，跳过 {skipped} 个（站点不存在或未启用），失败 {failed} 个")
        # 顺带刷新一次持久化快照（补行后的值也会写进快照，作为次日基线）
        try:
            self.__get_data()
        except Exception as e:
            logger.error(f"刷新站点累计快照失败：{str(e)}")
        self.sitechain.refresh_userdatas()