import base64
from .brushcheck_context import BrushCheckContext
import json
import re
import requests
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from fastapi import Query

from app import schemas
from app.api.endpoints.plugin import register_plugin_api
from app.chain.torrents import TorrentsChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import Event, eventmanager
from app.core.metainfo import MetaInfo
from app.db.site_oper import SiteOper
from app.db.subscribe_oper import SubscribeOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.helper.thread import ThreadHelper
from app.log import logger
from app.modules.qbittorrent import Qbittorrent
from app.modules.transmission import Transmission
from app.plugins import _PluginBase
from app.scheduler import Scheduler
from app.schemas import MediaType, NotificationType, ServiceInfo, TorrentInfo
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from app.utils.string import StringUtils

from .models import BrushFlowSettingsPayload, BrushTaskPayload, BrushTaskStatePayload


TASK_CONFIG_FIELDS = (
    "enabled",
    "notify",
    "site_id",
    "downloader",
    "brush_interval",
    "check_interval",
    "cron",
    "active_time_range",
    "site_ratio_control",
    "site_ratio_target",
    "disksize",
    "maxupspeed",
    "maxdlspeed",
    "maxdlcount",
    "freeleech",
    "hr",
    "include",
    "exclude",
    "size",
    "seeder",
    "timezone_offset",
    "pubtime",
    "seed_time",
    "hr_seed_time",
    "seed_ratio",
    "seed_size",
    "download_time",
    "seed_avgspeed",
    "seed_inactivetime",
    "delete_size_range",
    "up_speed",
    "dl_speed",
    "auto_archive_days",
    "save_path",
    "delete_except_tags",
    "except_subscribe",
    "proxy_delete",
    "del_no_free",
    "qb_category",
    "site_hr_active",
    "site_skip_tips",
    "rss_support",
    "skip_check_no_active",
    "download_limit_enabled",
    "download_limit",
    "only_delete_completed",
    "min_seed_time",
    "speed_limit_downloading",
    "speed_limit_complete",
    "speed_limit_downloading_threshold",
    "speed_limit_downloading_value",
    "speed_limit_complete_threshold",
    "speed_limit_complete_value",
    
    # v5.3.2 新增配置
    "site_data_enabled",
    "site_data_interval",
    # v5.3.10 新增配置
    "resurrect_enabled",
    "resurrect_url",
    "resurrect_min_peers",
    # v5.5.2: TTGL积分商店折扣
    "ttgl_discount_enabled",
    "ttgl_discount_min_size",
    # v5.5.6: 折扣档位可选
    "ttgl_discount_tier",
    # v5.5.7: 老种下载
    "old_seed_min_age",
)

LEGACY_SITE_OVERRIDE_FIELDS = {
    "freeleech",
    "hr",
    "include",
    "exclude",
    "size",
    "seeder",
    "timezone_offset",
    "pubtime",
    "seed_time",
    "hr_seed_time",
    "seed_ratio",
    "seed_size",
    "download_time",
    "seed_avgspeed",
    "seed_inactivetime",
    "save_path",
    "proxy_delete",
    "qb_category",
    "site_hr_active",
    "site_skip_tips",
    "del_no_free",
    "rss_support",
}

GLOBAL_LIMIT_FIELDS = (
    "global_disksize",
    "global_maxdlcount",
    "global_maxupspeed",
    "global_maxdlspeed",
    "global_disk_space_delete",
    "global_disk_space_threshold",
    "global_disk_space_target",
)

GLOBAL_DYNAMIC_DELETE_FIELDS = (
    "global_proxy_delete",
    "global_delete_size_range",
    "global_dynamic_delete_threshold",
)




class BrushTaskConfig:
    """
    单个站点刷流任务的运行配置
    """

    def __init__(self, config: dict):
        """读取并标准化一项刷流任务配置"""
        self.id = str(config.get("id") or uuid.uuid4().hex)
        self.name = str(config.get("name") or "刷流任务").strip()
        self.enabled = bool(config.get("enabled", True))
        self.notify = bool(config.get("notify", True))
        self.site_id = int(config.get("site_id") or 0)
        self.downloader = str(config.get("downloader") or "").strip()
        self.brush_interval = max(int(self._parse_number(config.get("brush_interval")) or 10), 1)
        self.check_interval = max(int(self._parse_number(config.get("check_interval")) or 5), 1)
        self.cron = self._clean_text(config.get("cron"))
        self.active_time_range = self._clean_text(config.get("active_time_range"))
        self.site_ratio_control = bool(config.get("site_ratio_control", False))
        self.site_ratio_target = self._parse_number(config.get("site_ratio_target"))
        self.disksize = self._parse_number(config.get("disksize"))
        self.maxupspeed = self._parse_number(config.get("maxupspeed"))
        self.maxdlspeed = self._parse_number(config.get("maxdlspeed"))
        self.maxdlcount = self._parse_number(config.get("maxdlcount"))
        self.freeleech = config.get("freeleech", "free")
        self.hr = config.get("hr", "yes")
        self.include = self._clean_text(config.get("include"))
        self.exclude = self._clean_text(config.get("exclude"))
        self.size = self._clean_text(config.get("size"))
        self.seeder = self._clean_text(config.get("seeder"))
        self.timezone_offset = float(self._parse_number(config.get("timezone_offset")) or 0)
        self.pubtime = self._clean_text(config.get("pubtime"))
        self.old_seed_min_age = self._parse_number(config.get("old_seed_min_age"))
        self.seed_time = self._parse_number(config.get("seed_time"))
        self.hr_seed_time = self._parse_number(config.get("hr_seed_time"))
        self.seed_ratio = self._parse_number(config.get("seed_ratio"))
        self.seed_size = self._parse_number(config.get("seed_size"))
        self.download_time = self._parse_number(config.get("download_time"))
        self.seed_avgspeed = self._parse_number(config.get("seed_avgspeed"))
        self.seed_inactivetime = self._parse_number(config.get("seed_inactivetime"))
        self.delete_size_range = self._clean_text(config.get("delete_size_range"))
        self.up_speed = self._parse_number(config.get("up_speed"))
        self.dl_speed = self._parse_number(config.get("dl_speed"))
        self.auto_archive_days = self._parse_number(config.get("auto_archive_days"))
        self.save_path = self._clean_text(config.get("save_path"))
        self.delete_except_tags = self._clean_text(config.get("delete_except_tags"))
        self.except_subscribe = bool(config.get("except_subscribe", True))
        self.proxy_delete = bool(config.get("proxy_delete", False))
        self.del_no_free = bool(config.get("del_no_free", False)) if self.freeleech in {"free", "2xfree"} else False
        self.qb_category = self._clean_text(config.get("qb_category"))
        self.site_hr_active = bool(config.get("site_hr_active", False))
        self.site_skip_tips = bool(config.get("site_skip_tips", False))
        self.rss_support = bool(config.get("rss_support", False))
        # v5.3.1 新增配置
        self.skip_check_no_active = bool(config.get("skip_check_no_active", False))
        self.download_limit_enabled = bool(config.get("download_limit_enabled", False))
        self.download_limit = self._parse_number(config.get("download_limit"))
        self.only_delete_completed = bool(config.get("only_delete_completed", True))
        self.min_seed_time = self._parse_number(config.get("min_seed_time")) or 10
        self.speed_limit_downloading = bool(config.get("speed_limit_downloading", False))
        self.speed_limit_complete = bool(config.get("speed_limit_complete", False))
        self.speed_limit_downloading_threshold = self._parse_number(config.get("speed_limit_downloading_threshold"))
        self.speed_limit_downloading_value = self._parse_number(config.get("speed_limit_downloading_value"))
        self.speed_limit_complete_threshold = self._parse_number(config.get("speed_limit_complete_threshold"))
        self.speed_limit_complete_value = self._parse_number(config.get("speed_limit_complete_value"))
        # v5.3.2 新增配置
        self.site_data_enabled = bool(config.get("site_data_enabled", False))
        self.site_data_interval = self._parse_number(config.get("site_data_interval"))
        # v5.3.10 新增配置
        self.resurrect_enabled = bool(config.get("resurrect_enabled", False))
        self.resurrect_url = self._clean_text(config.get("resurrect_url"))
        self.resurrect_min_peers = self._parse_number(config.get("resurrect_min_peers"))
        # v5.5.2: TTGL积分商店折扣
        self.ttgl_discount_enabled = bool(config.get("ttgl_discount_enabled", False))
        self.ttgl_discount_min_size = self._parse_number(config.get("ttgl_discount_min_size"))
        # v5.5.6: 折扣档位（30/50/fl），非法值回退 30
        tier = str(config.get("ttgl_discount_tier") or "30")
        self.ttgl_discount_tier = tier if tier in ("30", "50", "fl") else "30"
        
        # 预计算范围值（避免每次选种时重复解析）
        self._size_range = self._parse_range(self.size, multiplier=1024 ** 3)
        self._seeder_range = self._parse_range(self.seeder)
        self._pubtime_range = self._parse_range(self.pubtime)

    @staticmethod
    def _parse_range(value: Optional[str], multiplier: float = 1.0) -> Optional[List[float]]:
        """解析 'min-max' 或 'min' 格式的范围字符串"""
        if not value:
            return None
        try:
            return [float(v) * multiplier for v in value.split("-")]
        except (TypeError, ValueError):
            return None

    @property
    def brush_tag(self) -> str:
        """返回当前任务在下载器中使用的唯一标签（格式：刷流-站点名）"""
        site_name = BrushFlow._get_site_name(self.site_id) or self.id[:8]
        return f"刷流-{site_name}"

    @staticmethod
    def _clean_text(value: Any) -> Optional[str]:
        """把空白文本标准化为 None"""
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _parse_number(value: Any) -> Optional[Union[int, float]]:
        """兼容解析历史配置中的整数、浮点数和空值"""
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number

    def to_dict(self) -> Dict[str, Any]:
        """返回可持久化和供前端编辑的任务配置"""
        data = {"id": self.id, "name": self.name}
        data.update({field: getattr(self, field) for field in TASK_CONFIG_FIELDS})
        return data


class BrushFlow(_PluginBase):
    """
    多站点独立任务刷流插件
    """

    plugin_name = "站点刷流"
    plugin_desc = "自动托管多个站点刷流任务，并独立调度、统计与诊断。"
    plugin_icon = "brush-flow.png"
    plugin_version = "5.5.7"
    plugin_author = "jxxghp,InfinityPacer,Seed680"
    author_url = "https://github.com/InfinityPacer"
    plugin_config_prefix = "brushflow_"
    plugin_order = 21
    auth_level = 2

    DATA_SCHEMA_VERSION = 2
    MAX_RUN_HISTORY = 50
    GLOBAL_BRUSH_TAG = "刷流"
    TASK_DATA_NAMES = ("torrents", "archived", "unmanaged", "statistic", "runs")

    @staticmethod
    def _get_site_name(site_id: int) -> Optional[str]:
        """按站点 ID 获取名称并兼容已删除站点"""
        site = SiteOper().get(site_id)
        return site.name if site else None

    def init_plugin(self, config: dict = None) -> None:
        """初始化全局开关、任务配置、运行锁和历史数据迁移"""
        raw_config = config or {}
        self._task_context = threading.local()
        self._task_locks: Dict[str, threading.Lock] = {}
        self._brush_lock = threading.Lock()
        self._global_delete_lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._runtime: Dict[str, dict] = {}
        # v5.3.9：刷流等待标记，用于"同时触发时刷流优先"
        self._brush_pending: Dict[str, bool] = {}
        self._subscribe_infos: Dict[str, List[str]] = {}
        self._site_user_data_cache: Optional[Dict[str, Any]] = None
        self._site_user_data_cache_time: float = 0
        self._last_scheduler_refresh: float = 0
        self._subscribe_titles_cache: Optional[Set[str]] = None
        self._subscribe_titles_cache_time: float = 0
        self._enabled = bool(raw_config.get("enabled", False))
        self._show_sidebar_nav = bool(raw_config.get("show_sidebar_nav", True))
        # 空间不足全局删种锁 — 同一下载器同一时间只允许一个任务触发
        self._disk_space_cleanup_running = False

        legacy_config = not isinstance(raw_config.get("tasks"), list) and bool(raw_config.get("brushsites"))
        for field in GLOBAL_LIMIT_FIELDS:
            value = raw_config.get(field)
            if value is None and legacy_config:
                value = raw_config.get(field.removeprefix("global_"))
            parsed_value = BrushTaskConfig._parse_number(value)
            setattr(self, f"_{field}", parsed_value if parsed_value and parsed_value > 0 else None)
        global_proxy_delete = raw_config.get(
            "global_proxy_delete",
            raw_config.get("proxy_delete", False) if legacy_config else False,
        )
        legacy_delete_range = raw_config.get("delete_size_range") if legacy_config else None
        self._global_proxy_delete, self._global_delete_size_range, self._global_dynamic_delete_threshold = self._validate_global_dynamic_delete_config(
            global_proxy_delete,
            raw_config.get("global_delete_size_range", legacy_delete_range),
            raw_config.get("global_dynamic_delete_threshold"),
        )
        


        task_rows = raw_config.get("tasks") if isinstance(raw_config.get("tasks"), list) else None
        migrated = task_rows is None and bool(raw_config.get("brushsites"))
        if migrated:
            task_rows = self._migrate_legacy_config(raw_config)
        task_rows = task_rows or []

        self._task_configs: Dict[str, BrushTaskConfig] = {}
        for row in task_rows:
            if not isinstance(row, dict):
                continue
            task = BrushTaskConfig(row)
            if not self._validate_task_reference(task, notify=False):
                task.enabled = False
            self._task_configs[task.id] = task
            self._task_locks[task.id] = threading.Lock()
            self._runtime[task.id] = {"state": "idle", "operation": None, "last_error": None}

        normalized = self._current_config()
        if migrated or raw_config != normalized:
            self.update_config(normalized)
        
        self._migrate_legacy_data()

        # V5 为任务增加了唯一标签；启动时回收升级前已经失去任务配置的孤立标签。
        if self._enabled:
            try:
                ThreadHelper().submit(self._cleanup_unused_task_tags)
            except Exception as err:
                logger.warning(f"提交刷流标签清理任务失败：{str(err)}")

        if migrated and raw_config.get("onlyonce") and self._enabled:
            for task in self._task_configs.values():
                if task.enabled:
                    ThreadHelper().submit(self.brush, task.id)
                    ThreadHelper().submit(self.check, task.id)

    def get_state(self) -> bool:
        """返回插件全局启用状态"""
        return bool(getattr(self, "_enabled", False))

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """当前插件不注册远程命令"""
        return []

    @staticmethod
    def get_render_mode() -> Tuple[str, str]:
        """声明使用 Vue 联邦组件渲染插件界面"""
        return "vue", "dist/assets"

    def get_sidebar_nav(self) -> List[Dict[str, Any]]:
        """向主界面整理分组注册刷流任务入口"""
        if not self.get_state() or not getattr(self, "_show_sidebar_nav", True):
            return []
        return [
            {
                "nav_key": "main",
                "title": "站点刷流",
                "icon": "mdi-sync",
                "section": "organize",
                "permission": "manage",
                "order": 45,
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册 Vue 工作台使用的刷流任务 API"""
        return [
            {
                "path": "/status",
                "endpoint": self.get_status,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取刷流任务总览",
            },
            {
                "path": "/settings",
                "endpoint": self.update_settings,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "更新刷流插件设置",
            },
            {
                "path": "/tasks",
                "endpoint": self.create_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "创建刷流任务",
            },
            {
                "path": "/tasks/{task_id}",
                "endpoint": self.get_task_detail,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取刷流任务详情",
            },
            {
                "path": "/tasks/{task_id}",
                "endpoint": self.update_task,
                "methods": ["PUT"],
                "auth": "bear",
                "summary": "更新刷流任务",
            },
            {
                "path": "/tasks/{task_id}",
                "endpoint": self.delete_task,
                "methods": ["DELETE"],
                "auth": "bear",
                "summary": "删除刷流任务",
            },
            {
                "path": "/tasks/{task_id}/state",
                "endpoint": self.update_task_state,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "启用或暂停刷流任务",
            },
            {
                "path": "/tasks/{task_id}/run",
                "endpoint": self.run_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即执行刷流刷新",
            },
            {
                "path": "/tasks/{task_id}/check",
                "endpoint": self.check_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即检查刷流种子",
            },
            {
                "path": "/tasks/{task_id}/clear",
                "endpoint": self.clear_task_data,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "清除单个刷流任务数据",
            },
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """Vue 配置组件只需要接收当前配置模型"""
        return [], self._current_config()

    def get_page(self) -> List[dict]:
        """Vue 详情组件自行通过插件 API 获取页面数据"""
        return []

    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], None]]:
        """保留原有仪表板入口并改由 Vue 组件渲染"""
        if not self.get_state():
            return None
        return (
            {"cols": 12, "sm": 6, "md": 6},
            {
                "title": "站点刷流",
                "subtitle": "多任务运行概览",
                "refresh": 30,
                "border": True,
            },
            None,
        )

    def get_service(self) -> List[Dict[str, Any]]:
        """为每个启用任务注册独立的刷流刷新和状态检查服务"""
        if not self.get_state():
            return []
        services: List[Dict[str, Any]] = []
        for task in self._task_configs.values():
            if not task.enabled:
                continue
            if task.cron:
                try:
                    brush_trigger: Union[str, CronTrigger] = CronTrigger.from_crontab(task.cron)
                    brush_kwargs: Dict[str, Any] = {}
                except ValueError as err:
                    logger.error(f"刷流任务 [{task.name}] CRON 表达式无效：{str(err)}")
                    brush_trigger = "interval"
                    brush_kwargs = {"minutes": task.brush_interval}
            else:
                brush_trigger = "interval"
                brush_kwargs = {"minutes": task.brush_interval}
            services.append(
                {
                    "id": f"Task_{task.id}_Brush",
                    "name": f"刷流刷新 - {task.name}",
                    "trigger": brush_trigger,
                    "func": self.brush,
                    "kwargs": brush_kwargs,
                    "func_kwargs": {"task_id": task.id},
                }
            )
            services.append(
                {
                    "id": f"Task_{task.id}_Check",
                    "name": f"刷流检查 - {task.name}",
                    "trigger": "interval",
                    "func": self.check,
                    "kwargs": {"minutes": task.check_interval},
                    "func_kwargs": {"task_id": task.id},
                }
            )
            # v5.3.2：站点数据刷新服务（任务级配置）
            if getattr(task, "site_data_enabled", False) and getattr(task, "site_data_interval", None):
                services.append(
                    {
                        "id": f"Task_{task.id}_SiteDataRefresh",
                        "name": f"站点数据刷新 - {task.name}",
                        "trigger": "interval",
                        "func": self._refresh_site_userdata,
                        "kwargs": {"minutes": int(task.site_data_interval)},
                        "func_kwargs": {"task_id": task.id},
                    }
                )
        
        return services

    def _refresh_site_userdata(self, task_id: str = None) -> None:
        """v5.3.3：定时刷新当前任务绑定的站点用户数据（仅当有活跃种子时执行）"""
        try:
            from app.helper.sites import SitesHelper
            from app.chain.site import SiteChain

            # 确定要刷新的任务
            if task_id:
                task = self._task_configs.get(task_id)
                if not task or not task.enabled:
                    return
                task_name = task.name
                site_id = task.site_id
            else:
                # 兼容旧版：刷新所有启用任务的站点
                task_name = "所有任务"
                site_id = None

            # v5.3.4：检查是否有活跃种子（复用 check 方法的逻辑）
            active_count = 0
            if task_id and task:
                # 优先使用本地缓存（与 check 方法一致）
                torrent_data = self._current_task_data("torrents", {})
                active_count = sum(1 for item in torrent_data.values() if not item.get("deleted"))
                
                # 如果缓存为空，尝试从下载器获取
                if active_count == 0 and task.downloader:
                    try:
                        from app.helper.downloader import DownloaderHelper
                        dh = DownloaderHelper()
                        service = dh.get_service(name=task.downloader)
                        if service and service.instance and not service.instance.is_inactive():
                            seeding_torrents = service.instance.get_torrents() or []
                            active_count = len(seeding_torrents)
                    except Exception as e:
                        logger.warning(f"刷流任务 [{task_name}] 检查活跃种子异常: {e}")
            
            if active_count == 0:
                logger.info(f"刷流任务 [{task_name}] 无活跃种子，跳过站点数据刷新")
                return

            logger.info(f"刷流任务 [{task_name}] 开始刷新站点数据（活跃种子: {active_count}）")

            # 先确保认证
            SitesHelper().check_user()

            if site_id:
                # 只刷新绑定站点
                sh = SitesHelper()
                sites = sh.get_indexers()
                target_site = None
                for site in sites:
                    if site.get('id') == site_id:
                        target_site = site
                        break

                if target_site:
                    sc = SiteChain()
                    result = sc.refresh_userdata(target_site)
                    if result:
                        logger.info(f"刷流任务 [{task_name}] 站点数据刷新成功：分享率={result.ratio}, 做种={result.seeding}")
                    else:
                        logger.error(f"刷流任务 [{task_name}] 站点数据刷新失败")
                else:
                    logger.error(f"刷流任务 [{task_name}] 未找到绑定站点 (id={site_id})")
            else:
                # 兼容旧版：刷新所有站点
                SiteChain().refresh_userdatas()

            logger.info(f"刷流任务 [{task_name}] 站点数据刷新完成")
        except Exception as err:
            logger.error(f"刷流任务 站点数据刷新失败：{str(err)}")

    def stop_service(self) -> None:
        """插件不再维护私有调度器，公共服务由宿主统一停止"""
        with getattr(self, "_runtime_lock", threading.Lock()):
            for runtime in getattr(self, "_runtime", {}).values():
                runtime.update({"state": "idle", "operation": None})

    @property
    def service_info(self) -> Optional[ServiceInfo]:
        """获取当前任务绑定的下载器服务"""
        task = self._get_task_config()
        if not task or not task.downloader:
            return None
        dh = DownloaderHelper()
        service = dh.get_service(name=task.downloader)
        if not service:
            self._log_and_notify_error(f"刷流任务 [{task.name}] 获取下载器实例失败，请检查配置")
            return None
        if service.instance.is_inactive():
            self._log_and_notify_error(f"刷流任务 [{task.name}] 下载器未连接")
            return None
        return service

    @property
    def downloader(self) -> Optional[Union[Qbittorrent, Transmission]]:
        """返回当前任务绑定的下载器实例"""
        service = self.service_info
        return service.instance if service else None

    def get_status(self) -> schemas.Response:
        """返回全局设置、任务摘要和前端可选项"""
        return schemas.Response(success=True, data=self._build_status_data())

    def update_settings(self, payload: BrushFlowSettingsPayload) -> schemas.Response:
        """更新插件全局开关并刷新宿主任务调度"""
        global_dynamic_delete_was_enabled = self._global_dynamic_delete_enabled()
        self._enabled = payload.enabled
        self._show_sidebar_nav = payload.show_sidebar_nav
        for field in GLOBAL_LIMIT_FIELDS:
            setattr(self, f"_{field}", getattr(payload, field))
        for field in GLOBAL_DYNAMIC_DELETE_FIELDS:
            setattr(self, f"_{field}", getattr(payload, field))
        if global_dynamic_delete_was_enabled and not self._global_dynamic_delete_enabled():
            for task in self._task_configs.values():
                if task.proxy_delete and not task.delete_size_range:
                    task.proxy_delete = False
        self._save_config()
        self._refresh_scheduler()
        return schemas.Response(success=True, data=self._build_status_data())

    def create_task(self, payload: BrushTaskPayload) -> schemas.Response:
        """创建一个站点与下载器均独立的刷流任务"""
        task_data = payload.model_dump()
        task_data["id"] = uuid.uuid4().hex
        task = BrushTaskConfig(task_data)
        if not self._validate_task_reference(task):
            return schemas.Response(success=False, message="站点或下载器配置无效")
        self._task_configs[task.id] = task
        self._task_locks[task.id] = threading.Lock()
        self._runtime[task.id] = {"state": "idle", "operation": None, "last_error": None}
        self._save_config()
        self._refresh_scheduler()
        return schemas.Response(success=True, data=self._build_task_detail(task.id))

    def get_task_detail(
        self,
        task_id: str,
        state: str = Query("active", pattern="^(active|deleted|all)$"),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=10, le=200),
    ) -> schemas.Response:
        """分页返回任务配置、统计、诊断记录和种子明细"""
        if task_id not in self._task_configs:
            return schemas.Response(success=False, message="刷流任务不存在")
        return schemas.Response(
            success=True,
            data=self._build_task_detail(task_id, state=state, page=page, page_size=page_size),
        )

    def update_task(self, task_id: str, payload: BrushTaskPayload) -> schemas.Response:
        """更新任务配置并保持原任务 ID 与历史数据关联"""
        if task_id not in self._task_configs:
            return schemas.Response(success=False, message="刷流任务不存在")
        if self._is_task_busy(task_id):
            return schemas.Response(success=False, message="任务正在执行，请稍后再修改")
        task_data = payload.model_dump()
        task_data["id"] = task_id
        task = BrushTaskConfig(task_data)
        if not self._validate_task_reference(task):
            return schemas.Response(success=False, message="站点或下载器配置无效")
        self._task_configs[task_id] = task
        self._save_config()
        self._refresh_scheduler()
        return schemas.Response(success=True, data=self._build_task_detail(task_id))

    def delete_task(self, task_id: str) -> schemas.Response:
        """删除没有活跃种子的任务及其独立历史数据"""
        task = self._task_configs.get(task_id)
        if not task:
            return schemas.Response(success=False, message="刷流任务不存在")
        if self._is_task_busy(task_id):
            return schemas.Response(success=False, message="任务正在执行，请稍后再删除")
        torrents = self._get_task_data(task_id, "torrents") or {}
        active_count = sum(1 for item in torrents.values() if not item.get("deleted"))
        if active_count:
            return schemas.Response(success=False, message="任务仍有活跃种子，请先处理后再删除")
        self._task_configs.pop(task_id, None)
        self._task_locks.pop(task_id, None)
        self._runtime.pop(task_id, None)
        for data_name in self.TASK_DATA_NAMES:
            self.del_data(self._task_data_key(task_id, data_name))
        self._save_config()
        self._refresh_scheduler()
        try:
            # 删除接口不应被下载器网络请求阻塞，标签清理由后台线程完成。
            ThreadHelper().submit(self._cleanup_unused_task_tag, task)
        except Exception as err:
            logger.warning(f"提交刷流任务标签清理失败：{str(err)}")
        return schemas.Response(success=True, data=self._build_status_data())

    def update_task_state(self, task_id: str, payload: BrushTaskStatePayload) -> schemas.Response:
        """启用或暂停单个任务并更新对应宿主调度"""
        task = self._task_configs.get(task_id)
        if not task:
            return schemas.Response(success=False, message="刷流任务不存在")
        task.enabled = payload.enabled
        # 重置运行时状态，避免暂停后仍显示"运行中"导致无法修改
        self._set_runtime(task_id, state="idle", operation=None)
        self._save_config()
        self._refresh_scheduler()
        return schemas.Response(success=True, data=self._build_task_detail(task_id))

    def run_task(self, task_id: str) -> schemas.Response:
        """异步提交单个任务的立即刷流刷新"""
        return self._submit_task_operation(task_id, "brush")

    def check_task(self, task_id: str) -> schemas.Response:
        """异步提交单个任务的立即状态检查"""
        return self._submit_task_operation(task_id, "check")

    def clear_task_data(self, task_id: str) -> schemas.Response:
        """清除单个任务的统计、历史与托管记录"""
        if task_id not in self._task_configs:
            return schemas.Response(success=False, message="刷流任务不存在")
        if self._is_task_busy(task_id):
            return schemas.Response(success=False, message="任务正在执行，请稍后再清除")
        for data_name in self.TASK_DATA_NAMES:
            self._save_task_data(task_id, data_name, {} if data_name != "runs" else [])
        return schemas.Response(success=True, data=self._build_task_detail(task_id))

    @eventmanager.register(EventType.PluginReload)
    def reload(self, event: Event) -> None:
        """插件重载后重新注册动态 API 和任务调度"""
        if event and event.event_data.get("plugin_id") == self.__class__.__name__:
            register_plugin_api(plugin_id=self.__class__.__name__)
            Scheduler().update_plugin_job(self.__class__.__name__)

    def _current_config(self) -> Dict[str, Any]:
        """返回插件当前可持久化配置快照"""
        config = {
            "schema_version": self.DATA_SCHEMA_VERSION,
            "enabled": bool(getattr(self, "_enabled", False)),
            "show_sidebar_nav": bool(getattr(self, "_show_sidebar_nav", True)),
            "tasks": [task.to_dict() for task in getattr(self, "_task_configs", {}).values()],
        }
        config.update({field: getattr(self, f"_{field}", None) for field in GLOBAL_LIMIT_FIELDS})
        config.update({field: getattr(self, f"_{field}", None) for field in GLOBAL_DYNAMIC_DELETE_FIELDS})
        return config

    def _save_config(self) -> None:
        """保存全局设置和全部任务配置"""
        self.update_config(self._current_config())

    def _refresh_scheduler(self) -> None:
        """通知宿主按最新任务列表重建插件服务（防抖：5秒内不重复触发）"""
        now = time.time()
        if now - self._last_scheduler_refresh < 5:
            return
        self._last_scheduler_refresh = now
        try:
            Scheduler().update_plugin_job(self.__class__.__name__)
        except Exception as err:
            logger.error(f"更新站点刷流调度失败：{str(err)}")

    def _global_dynamic_delete_enabled(self) -> bool:
        """返回全局动态删种是否启用：开关开启且至少设置了一个触发条件"""
        if not getattr(self, "_global_proxy_delete", False):
            return False
        if getattr(self, "_global_delete_size_range", None):
            return True
        if getattr(self, "_global_dynamic_delete_threshold", None):
            return True
        return False

    def _global_dynamic_delete_should_trigger(self) -> bool:
        """检查是否满足触发条件：原条件（做种体积）或新条件（剩余空间）任一满足即可"""
        # 检查剩余空间阈值（如果设置了）
        threshold = getattr(self, "_global_dynamic_delete_threshold", None)
        if threshold is not None:
            try:
                helper = DownloaderHelper()
                for task in self._task_configs.values():
                    if task.downloader:
                        service = helper.get_service(name=task.downloader)
                        if service and service.instance:
                            free_space = self.__get_downloader_free_space(task)
                            if free_space is not None:
                                free_gb = self.__bytes_to_gb(free_space)
                                if free_gb < float(threshold):
                                    return True
                # 设置了阈值但剩余空间足够
                if getattr(self, "_global_delete_size_range", None):
                    # 同时设置了原条件，继续让原条件判断
                    return True
                return False
            except Exception:
                return True
        # 未设置剩余空间阈值，由做种体积条件判断
        return True

    @staticmethod
    def _validate_global_dynamic_delete_config(enabled: Any, size_range: Any, threshold: Any = None) -> Tuple[bool, Optional[str], Optional[float]]:
        """复用设置模型校验持久化或迁移得到的全局动态删种配置。"""
        try:
            data = {"global_proxy_delete": enabled, "global_delete_size_range": size_range}
            if threshold is not None:
                data["global_dynamic_delete_threshold"] = threshold
            payload = BrushFlowSettingsPayload.model_validate(data)
        except ValueError as err:
            logger.warning(f"全局动态删种配置无效，已自动关闭：{str(err)}")
            return False, None, None
        return payload.global_proxy_delete, payload.global_delete_size_range, payload.global_dynamic_delete_threshold

    @staticmethod
    def _promotion_expiry_at(freedate_origin: Any, timezone_offset: float) -> Optional[datetime]:
        """把站点促销截止时间换算为宿主时区中的实际到期时刻"""
        if not freedate_origin:
            return None
        try:
            freedate_text = str(freedate_origin).strip().replace("T", " ").removesuffix("Z")
            site_expiry = datetime.strptime(freedate_text, "%Y-%m-%d %H:%M:%S")
            local_expiry = site_expiry + timedelta(hours=timezone_offset)
            return local_expiry.replace(tzinfo=ZoneInfo(settings.TZ))
        except (TypeError, ValueError) as err:
            logger.warning(f"解析促销截止时间失败：{str(err)}")
            return None

    def _next_promotion_expiry(self, task: BrushTaskConfig) -> Optional[datetime]:
        """返回任务中下一项未完成下载的促销截止时间"""
        if not task.del_no_free:
            return None
        now = datetime.now(ZoneInfo(settings.TZ))
        expiries: List[datetime] = []
        torrent_tasks = self._get_task_data(task.id, "torrents") or {}
        for torrent_task in torrent_tasks.values():
            if not isinstance(torrent_task, dict) or torrent_task.get("deleted"):
                continue
            try:
                total_size = float(torrent_task.get("size") or 0)
                downloaded = float(torrent_task.get("downloaded") or 0)
            except (TypeError, ValueError):
                total_size = downloaded = 0
            # 使用本地缓存判断种子是否已完成（缓存数据不含 completion_on，使用 downloaded >= total_size）
            if total_size > 0 and downloaded >= total_size:
                continue
            expiry = self._promotion_expiry_at(torrent_task.get("freedate"), task.timezone_offset)
            if expiry and expiry > now:
                expiries.append(expiry)
        return min(expiries) if expiries else None

    def _check_promotion_expiry(self, task_id: str) -> None:
        """在最近促销截止时等待当前操作结束，检查后重排下一截止任务"""
        try:
            self.check(task_id, wait_for_lock=True)
        finally:
            self._refresh_scheduler()

    def _validate_task_reference(self, task: BrushTaskConfig, notify: bool = True) -> bool:
        """校验任务引用的私有站点和下载器是否仍然存在"""
        site = SiteOper().get(task.site_id)
        downloader_configs = DownloaderHelper().get_configs()
        valid = bool(
            site
            and not getattr(site, "public", False)
            and task.downloader
            and task.downloader in downloader_configs
        )
        if notify and not valid:
            self._log_and_notify_error(f"刷流任务 [{task.name}] 引用的站点或下载器不存在")
        return valid

    @staticmethod
    def _parse_tags(tags_string: str) -> Set[str]:
        """解析逗号分隔的标签字符串为去重集合"""
        return {
            item.strip()
            for item in str(tags_string or "").split(",")
            if item.strip()
        }

    @staticmethod
    def _torrent_has_tag(torrent: Any, tag: str) -> bool:
        """判断 qBittorrent 种子是否仍绑定指定标签"""
        if not isinstance(torrent, dict):
            return False
        return tag in BrushFlow._parse_tags(torrent.get("tags"))

    @staticmethod
    def _delete_qbittorrent_tags(service: Any, tags: Union[str, List[str]]) -> bool:
        """删除 qBittorrent 全局标签定义，不调用需要种子 Hash 的 removeTags。"""
        client = getattr(getattr(service, "instance", None), "qbc", None)
        if not client or not tags:
            return False
        client.torrents_delete_tags(tags=tags)
        return True

    def _cleanup_unused_task_tag(
        self,
        task: BrushTaskConfig,
        torrents: Optional[List[Any]] = None,
    ) -> None:
        """仅删除不再被任何 qBittorrent 种子使用的任务唯一标签"""
        if not task or not task.downloader:
            return
        try:
            helper = DownloaderHelper()
            service = helper.get_service(name=task.downloader)
            if not service or not service.instance or not helper.is_downloader("qbittorrent", service=service):
                return
            if torrents is None:
                torrents, error = service.instance.get_torrents()
                if error:
                    logger.warning(f"清理刷流任务 [{task.name}] 标签时获取下载器种子失败")
                    return
            if any(self._torrent_has_tag(torrent, task.brush_tag) for torrent in torrents or []):
                return
            if self._delete_qbittorrent_tags(service, task.brush_tag):
                logger.info(f"清理刷流任务 [{task.name}] 未使用标签：{task.brush_tag}")
        except Exception as err:
            # 标签清理失败不应影响刷流检查或任务删除主流程。
            logger.warning(f"清理刷流任务 [{task.name}] 标签失败：{str(err)}")

    def _cleanup_unused_task_tags(self) -> None:
        """扫描全部 qBittorrent 下载器，清理历史遗留的刷流唯一标签"""
        try:
            helper = DownloaderHelper()
            downloader_names = set(helper.get_configs().keys())
            for downloader_name in downloader_names:
                service = helper.get_service(name=downloader_name)
                if not service or not service.instance or not helper.is_downloader("qbittorrent", service=service):
                    continue
                client = getattr(service.instance, "qbc", None)
                if not client:
                    continue
                all_tags = [str(tag).strip() for tag in client.torrents_tags() or [] if str(tag).strip()]
                task_tags = [tag for tag in all_tags if tag.startswith("刷流-")]
                if not task_tags:
                    continue
                torrents, error = service.instance.get_torrents()
                if error:
                    logger.warning(f"扫描下载器 [{downloader_name}] 刷流标签时获取种子失败")
                    continue
                used_tags = {
                    tag
                    for torrent in torrents or []
                    for tag in self._parse_tags(torrent.get("tags"))
                }
                unused_tags = [tag for tag in task_tags if tag not in used_tags]
                if unused_tags and self._delete_qbittorrent_tags(service, unused_tags):
                    logger.info(f"清理下载器 [{downloader_name}] 未使用刷流标签：{','.join(unused_tags)}")
        except Exception as err:
            logger.warning(f"扫描清理历史刷流标签失败：{str(err)}")

    def _migrate_legacy_config(self, config: dict) -> List[dict]:
        """把旧全局配置和站点覆盖 JSON 拆分为一站点一任务"""
        overrides = self._parse_legacy_site_overrides(config)
        tasks: List[dict] = []
        for site_id in config.get("brushsites") or []:
            site = SiteOper().get(site_id)
            if not site or getattr(site, "public", False):
                continue
            task_data = {field: config.get(field) for field in TASK_CONFIG_FIELDS if field in config}
            site_override = overrides.get(site.name, {})
            task_data.update(site_override)
            # 旧版会把全局时区小时数转换成分钟后再次持久化，站点覆盖 JSON 则始终保留小时数。
            if "timezone_offset" not in site_override:
                timezone_offset = BrushTaskConfig._parse_number(config.get("timezone_offset")) or 0
                task_data["timezone_offset"] = float(timezone_offset) / 60
            task_data.update(
                {
                    "id": uuid.uuid4().hex,
                    "name": site.name,
                    "site_id": site.id,
                    "enabled": True,
                    "brush_interval": 10,
                    "check_interval": 5,
                }
            )
            tasks.append(BrushTaskConfig(task_data).to_dict())
        return tasks

    @staticmethod
    def _parse_legacy_site_overrides(config: dict) -> Dict[str, dict]:
        """解析旧版允许注释的站点覆盖 JSON"""
        if not config.get("enable_site_config") or not config.get("site_config"):
            return {}
        try:
            content = re.sub(r"//.*?(?:\n|$)", "", str(config.get("site_config"))).strip()
            rows = json.loads(content)
        except (TypeError, ValueError) as err:
            logger.error(f"解析旧版站点独立配置失败：{str(err)}")
            return {}
        overrides: Dict[str, dict] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("sitename"):
                continue
            overrides[str(row["sitename"])] = {
                key: row[key] for key in LEGACY_SITE_OVERRIDE_FIELDS if key in row
            }
        return overrides

    def _migrate_legacy_data(self) -> None:
        """按站点把旧全局种子、归档和未托管记录迁移到任务命名空间"""
        if (self.get_data("task_data_schema_version") or 0) >= self.DATA_SCHEMA_VERSION:
            return
        tasks = list(self._task_configs.values())
        by_site_id = {str(task.site_id): task for task in tasks}
        by_site_name = {
            self._get_site_name(task.site_id): task for task in tasks if self._get_site_name(task.site_id)
        }
        for data_name in ("torrents", "archived", "unmanaged"):
            legacy_rows = self.get_data(data_name) or {}
            buckets: Dict[str, dict] = {task.id: {} for task in tasks}
            for item_id, item in legacy_rows.items() if isinstance(legacy_rows, dict) else []:
                task = by_site_id.get(str(item.get("site"))) or by_site_name.get(item.get("site_name"))
                if not task and len(tasks) == 1:
                    task = tasks[0]
                if not task:
                    continue
                migrated_item = dict(item)
                migrated_item.update({"task_id": task.id, "task_name": task.name})
                buckets[task.id][item_id] = migrated_item
    
    @contextmanager
    def _task_scope(self, task_id: str) -> Iterator[BrushTaskConfig]:
        """在当前线程中绑定任务上下文，供深层核心逻辑读取"""
        previous = getattr(self._task_context, "task_id", None)
        self._task_context.task_id = task_id
        try:
            task = self._task_configs.get(task_id)
            if not task:
                raise KeyError(f"刷流任务不存在：{task_id}")
            yield task
        finally:
            if previous is None:
                if hasattr(self._task_context, "task_id"):
                    delattr(self._task_context, "task_id")
            else:
                self._task_context.task_id = previous

    @contextmanager
    def _all_task_locks_scope(self) -> Iterator[None]:
        """按任务 ID 顺序锁定全部任务，保护跨任务删种的数据一致性"""
        acquired_locks: List[threading.Lock] = []
        try:
            for task_id in sorted(self._task_configs):
                task_lock = self._task_locks.setdefault(task_id, threading.Lock())
                task_lock.acquire()
                acquired_locks.append(task_lock)
            yield
        finally:
            for task_lock in reversed(acquired_locks):
                task_lock.release()

    def _get_task_config(self, task_id: Optional[str] = None) -> Optional[BrushTaskConfig]:
        """获取显式任务或当前线程绑定的任务配置"""
        resolved_id = task_id or getattr(self._task_context, "task_id", None)
        return self._task_configs.get(resolved_id) if resolved_id else None

    def __get_brush_config(self, sitename: str = None) -> Optional[BrushTaskConfig]:
        """兼容核心逻辑获取当前任务配置"""
        task = self._get_task_config()
        if task or not sitename:
            return task
        return next(
            (item for item in self._task_configs.values() if self._get_site_name(item.site_id) == sitename),
            None,
        )

    @staticmethod
    def _task_data_key(task_id: str, data_name: str) -> str:
        """生成任务独立的插件数据键"""
        return f"task.{task_id}.{data_name}"

    def _get_task_data(self, task_id: str, data_name: str) -> Any:
        """读取指定任务的独立持久化数据"""
        return self.get_data(self._task_data_key(task_id, data_name))

    def _save_task_data(self, task_id: str, data_name: str, value: Any) -> None:
        """保存指定任务的独立持久化数据"""
        self.save_data(self._task_data_key(task_id, data_name), value)

    def _current_task_data(self, data_name: str, default: Any = None) -> Any:
        """读取当前线程任务的数据并提供缺省值"""
        task = self._get_task_config()
        if not task:
            return default
        value = self._get_task_data(task.id, data_name)
        return default if value is None else value

    def _save_current_task_data(self, data_name: str, value: Any) -> None:
        """保存当前线程任务的数据"""
        task = self._get_task_config()
        if task:
            self._save_task_data(task.id, data_name, value)

    def _submit_task_operation(self, task_id: str, operation: str) -> schemas.Response:
        """校验运行条件后把手动操作提交到宿主线程池"""
        task = self._task_configs.get(task_id)
        if not task:
            return schemas.Response(success=False, message="刷流任务不存在")
        if not self.get_state() or not task.enabled:
            return schemas.Response(success=False, message="插件或任务未启用")
        if not self._mark_task_queued(task_id, operation):
            return schemas.Response(success=False, message="任务已有操作正在执行")
        target = self.brush if operation == "brush" else self.check
        try:
            ThreadHelper().submit(target, task_id)
        except Exception as err:
            self._set_runtime(task_id, state="idle", operation=None, last_error=str(err))
            logger.error(f"提交刷流任务 [{task.name}] 失败：{str(err)}")
            return schemas.Response(success=False, message="任务提交失败")
        return schemas.Response(success=True, message="任务已提交", data=self._task_summary(task_id))

    def _is_task_busy(self, task_id: str) -> bool:
        """判断任务是否已经排队或正在执行，保护运行中的配置与数据"""
        task_lock = self._task_locks.get(task_id)
        with self._runtime_lock:
            runtime = self._runtime.get(task_id, {})
            return bool(
                runtime.get("state") in {"queued", "running"}
                or (task_lock and task_lock.locked())
            )

    def _mark_task_queued(self, task_id: str, operation: str) -> bool:
        """以原子方式把空闲任务标记为排队，避免重复提交手动操作"""
        with self._runtime_lock:
            runtime = self._runtime.setdefault(
                task_id,
                {"state": "idle", "operation": None, "last_error": None},
            )
            if runtime.get("state") in {"queued", "running"}:
                return False
            runtime.update({"state": "queued", "operation": operation, "last_error": None})
            return True

    def _set_runtime(self, task_id: str, **updates: Any) -> None:
        """线程安全地更新任务瞬时运行状态"""
        with self._runtime_lock:
            runtime = self._runtime.setdefault(task_id, {"state": "idle", "operation": None, "last_error": None})
            runtime.update(updates)

    def _append_run(self, task_id: str, report: dict) -> None:
        """保存最近的刷流或检查诊断记录"""
        history = self._get_task_data(task_id, "runs") or []
        stored_report = {
            **report,
            "reason_counts": dict(report.get("reason_counts") or {}),
        }
        history.insert(0, stored_report)
        self._save_task_data(task_id, "runs", history[: self.MAX_RUN_HISTORY])

    def _build_status_data(self) -> Dict[str, Any]:
        """组装工作台总览、任务摘要和可选站点下载器"""
        site_user_data = (
            self._latest_site_user_data_by_domain()
            if any(task.site_ratio_control or task.download_limit_enabled for task in self._task_configs.values())
            else {}
        )
        task_rows = [
            self._task_summary(task_id, site_user_data_by_domain=site_user_data)
            for task_id in self._task_configs
        ]
        aggregate = {
            "task_count": len(task_rows),
            "enabled_count": sum(1 for row in task_rows if row.get("enabled")),
            "running_count": sum(
                1 for row in task_rows if row.get("state") in {"running", "brush", "check"}
            ),
            "active_count": sum(row.get("statistic", {}).get("active", 0) for row in task_rows),
            "uploaded": sum(row.get("statistic", {}).get("uploaded", 0) for row in task_rows),
            "downloaded": sum(row.get("statistic", {}).get("downloaded", 0) for row in task_rows),
            "seeding_size": sum(row.get("seeding_size", 0) for row in task_rows),
        }
        site_options = [
            {"title": site.get("name"), "value": site.get("id")}
            for site in SitesHelper().get_indexers()
            if not site.get("public")
        ]
        downloader_options = [
            {"title": item.name, "value": item.name}
            for item in DownloaderHelper().get_configs().values()
        ]
        return {
            "enabled": self.get_state(),
            "show_sidebar_nav": self._show_sidebar_nav,
            **{field: getattr(self, f"_{field}", None) for field in GLOBAL_LIMIT_FIELDS},
            **{field: getattr(self, f"_{field}", None) for field in GLOBAL_DYNAMIC_DELETE_FIELDS},
            "summary": aggregate,
            "tasks": task_rows,
            "options": {"sites": site_options, "downloaders": downloader_options},
        }

    def _task_summary(
        self,
        task_id: str,
        site_user_data_by_domain: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """组装单个任务在左侧任务列表和仪表板中的摘要"""
        task = self._task_configs.get(task_id)
        if not task:
            return {}
        statistic = self._get_statistic_info(task_id)
        torrents = self._get_task_data(task_id, "torrents") or {}
        history = self._get_task_data(task_id, "runs") or []
        runtime = dict(self._runtime.get(task_id, {}))
        site_user_data_status = self._build_site_user_data_status(task, site_user_data_by_domain)
        site_ratio = site_user_data_status["ratio"]
        site_downloaded = site_user_data_status["downloaded"]
        if not self.get_state():
            display_state = "disabled"
        elif not task.enabled:
            display_state = "paused"
        elif runtime.get("state") in {"queued", "running"}:
            display_state = runtime.get("operation") or "running"
        elif site_ratio["enabled"] and not site_ratio["available"]:
            display_state = "ratio_unavailable"
        elif site_ratio["reached"]:
            display_state = "waiting_ratio"
        elif site_downloaded["enabled"] and not site_downloaded["available"]:
            display_state = "download_waiting"
        elif site_downloaded["reached"]:
            display_state = "waiting_download"

        elif not self._is_current_time_in_range(task):
            display_state = "waiting"
        elif runtime.get("last_error"):
            display_state = "error"
        else:
            display_state = "running"
        return {
            "id": task.id,
            "name": task.name,
            "enabled": task.enabled,
            "site_id": task.site_id,
            "site_name": self._get_site_name(task.site_id) or "站点已删除",
            "downloader": task.downloader,
            "brush_interval": task.brush_interval,
            "check_interval": task.check_interval,
            "cron": task.cron,
            "active_time_range": task.active_time_range,
            "state": display_state,
            "operation": runtime.get("operation"),
            "last_error": runtime.get("last_error"),
            "next_run_at": self._next_run_at(task, history),
            "last_run": history[0] if history else None,
            "statistic": statistic,
            "seeding_size": self.__calculate_seeding_torrents_size(torrents),
            "site_ratio": site_ratio,
            "site_downloaded": site_downloaded,
            "config": task.to_dict(),
        }

    def _latest_site_user_data_by_domain(self) -> Dict[str, Any]:
        """按标准化域名索引各站点最新一条有效用户统计（60秒缓存）。"""
        now = time.time()
        if self._site_user_data_cache is not None and now - self._site_user_data_cache_time < 60:
            return self._site_user_data_cache
        result: Dict[str, Any] = {}
        for row in SiteOper().get_userdata_latest() or []:
            domain = StringUtils.get_url_domain(getattr(row, "domain", None))
            if domain and domain not in result:
                result[domain] = row
        self._site_user_data_cache = result
        self._site_user_data_cache_time = now
        return result

    def _build_site_ratio_status(
        self,
        task: BrushTaskConfig,
        site_user_data_by_domain: Optional[Dict[str, Any]] = None,
        site: Any = None,
    ) -> Dict[str, Any]:
        """组装任务绑定站点的当前分享率、目标值和控制状态。"""
        return self._build_site_user_data_status(task, site_user_data_by_domain, site)["ratio"]

    def _build_site_downloaded_status(
        self,
        task: BrushTaskConfig,
        site_user_data_by_domain: Optional[Dict[str, Any]] = None,
        site: Any = None,
    ) -> Dict[str, Any]:
        """组装任务绑定站点的当前下载量、目标值和控制状态。"""
        return self._build_site_user_data_status(task, site_user_data_by_domain, site)["downloaded"]

    def _build_site_user_data_status(
        self,
        task: BrushTaskConfig,
        site_user_data_by_domain: Optional[Dict[str, Any]] = None,
        site: Any = None,
    ) -> Dict[str, Dict[str, Any]]:
        """单次查询并组装站点分享率与下载量状态（统一查询，避免重复调用）。"""
        ratio_status = {
            "enabled": bool(task.site_ratio_control),
            "target": task.site_ratio_target,
            "current": None,
            "available": False,
            "unlimited": False,
            "reached": False,
            "updated_at": None,
        }
        downloaded_status = {
            "enabled": bool(task.download_limit_enabled),
            "target": task.download_limit,
            "current": None,
            "available": False,
            "reached": False,
            "updated_at": None,
        }

        need_ratio = task.site_ratio_control and task.site_ratio_target
        need_downloaded = task.download_limit_enabled and task.download_limit
        if not need_ratio and not need_downloaded:
            return {"ratio": ratio_status, "downloaded": downloaded_status}

        site = site or SiteOper().get(task.site_id)
        if not site:
            return {"ratio": ratio_status, "downloaded": downloaded_status}
        if site_user_data_by_domain is None:
            site_user_data_by_domain = self._latest_site_user_data_by_domain()
        domain = StringUtils.get_url_domain(getattr(site, "domain", None))
        user_data = site_user_data_by_domain.get(domain)
        if not user_data:
            return {"ratio": ratio_status, "downloaded": downloaded_status}

        updated_day = getattr(user_data, "updated_day", None)
        updated_time = getattr(user_data, "updated_time", None)
        updated_at = " ".join(value for value in (updated_day, updated_time) if value) or None

        if need_ratio:
            ratio = BrushTaskConfig._parse_number(getattr(user_data, "ratio", None))
            if ratio is not None:
                upload = BrushTaskConfig._parse_number(getattr(user_data, "upload", None)) or 0
                download = BrushTaskConfig._parse_number(getattr(user_data, "download", None)) or 0
                unlimited = float(ratio) == 0 and float(upload) > 0 and float(download) <= 0
                ratio_status.update(
                    {
                        "current": None if unlimited else float(ratio),
                        "available": True,
                        "unlimited": unlimited,
                        "reached": unlimited or float(ratio) >= float(task.site_ratio_target),
                        "updated_at": updated_at,
                    }
                )

        if need_downloaded:
            download = BrushTaskConfig._parse_number(getattr(user_data, "download", None)) or 0
            current_gb = self.__bytes_to_gb(download)
            downloaded_status.update(
                {
                    "current": current_gb,
                    "available": True,
                    "reached": current_gb >= float(task.download_limit),
                    "updated_at": updated_at,
                }
            )

        return {"ratio": ratio_status, "downloaded": downloaded_status}

    def _evaluate_site_ratio_control(
        self,
        task: BrushTaskConfig,
        site: Any = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """判断站点分享率是否允许当前任务继续新增种子。"""
        status = self._build_site_ratio_status(task, site=site)
        if not status["enabled"]:
            return True, None, status
        if not status["available"]:
            return False, "暂无站点分享率统计，等待数据更新", status
        if status["reached"]:
            current = "无限" if status["unlimited"] else f"{status['current']:.2f}"
            return (
                False,
                f"站点分享率 {current}，已达到目标 {float(status['target']):.2f}",
                status,
            )
        return True, None, status

    def _evaluate_download_limit_control(
        self,
        task: BrushTaskConfig,
        site: Any = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """判断站点下载量是否允许当前任务继续新增种子。"""
        status = self._build_site_downloaded_status(task, site=site)
        if not status["enabled"]:
            return True, None, status
        if not status["available"]:
            return False, "暂无站点下载量统计，等待数据更新", status
        if status["reached"]:
            return (
                False,
                f"站点下载量 {status['current']:.1f} GB，已达目标 {float(status['target']):.1f} GB",
                status,
            )
        return True, None, status

    def _build_task_detail(
        self,
        task_id: str,
        state: str = "active",
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """组装任务编辑、概览、诊断与分页种子数据"""
        task = self._task_configs[task_id]
        torrents = self._get_task_data(task_id, "torrents") or {}
        archived = self._get_task_data(task_id, "archived") or {}
        rows = list(torrents.values())
        if state == "active":
            rows = [row for row in rows if not row.get("deleted")]
        elif state == "deleted":
            rows = [row for row in rows if row.get("deleted")] + list(archived.values())
        else:
            rows.extend(archived.values())
        rows.sort(key=lambda item: item.get("time") or 0, reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        selected_rows = rows[start : start + page_size]
        
        # v5.3.1：获取站点数据状态
        site_user_data_by_domain = self._latest_site_user_data_by_domain()
        site_user_data_status = self._build_site_user_data_status(task, site_user_data_by_domain)
        site_ratio = site_user_data_status["ratio"]
        site_downloaded = site_user_data_status["downloaded"]
        
        return {
            "task": task.to_dict(),
            "summary": self._task_summary(task_id, site_user_data_by_domain=site_user_data_by_domain),
            "site_ratio": site_ratio,
            "site_downloaded": site_downloaded,
            "runs": (self._get_task_data(task_id, "runs") or [])[:20],
            "torrents": {
                "items": selected_rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "state": state,
            },
        }

    @staticmethod
    def _next_run_at(task: BrushTaskConfig, history: List[dict]) -> Optional[str]:
        """按 CRON 或固定间隔估算任务下一次刷新时间"""
        now = datetime.now().astimezone()
        try:
            if task.cron:
                next_time = CronTrigger.from_crontab(task.cron, timezone=settings.TZ).get_next_fire_time(None, now)
            else:
                last_brush = next((item for item in history if item.get("kind") == "brush"), None)
                if last_brush and last_brush.get("started_at"):
                    base = datetime.fromisoformat(last_brush["started_at"])
                    next_time = max(base + timedelta(minutes=task.brush_interval), now)
                else:
                    next_time = now + timedelta(minutes=task.brush_interval)
            return next_time.isoformat(timespec="minutes") if next_time else None
        except (TypeError, ValueError):
            return None

    def brush(self, task_id: Optional[str] = None) -> None:
        """执行单个任务的站点刷新、选种和下载流程"""
        task = self._get_task_config(task_id)
        if not task or not self.get_state() or not task.enabled:
            return
        task_lock = self._task_locks.setdefault(task.id, threading.Lock())
        # v5.3.9：刷流优先 — 提前标记等待状态，让检查服务主动避让
        self._brush_pending[task.id] = True
        # v5.3.9：刷流等待锁释放，而非跳过本轮
        if not task_lock.acquire(blocking=True):
            logger.info(f"刷流任务 [{task.name}] 获取锁超时，本轮跳过")
            self._brush_pending.pop(task.id, None)
            return
        self._brush_pending.pop(task.id, None)
        report = self._new_run_report("brush")
        self._set_runtime(task.id, state="running", operation="brush", last_error=None)
        try:
            with self._brush_lock, self._task_scope(task.id):
                self._run_brush(task, report)
            report["success"] = report.get("result") not in {"downloader_unavailable", "site_missing"}
        except Exception as err:
            report.update({"success": False, "error": str(err)})
            self._set_runtime(task.id, last_error=str(err))
            logger.error(f"刷流任务 [{task.name}] 执行失败：{str(err)}")
        finally:
            report["finished_at"] = self._now_iso()
            self._append_run(task.id, report)
            self._set_runtime(task.id, state="idle", operation=None)
            task_lock.release()
            if report.get("added_count"):
                self._refresh_scheduler()

    def _run_brush(self, task: BrushTaskConfig, report: dict) -> None:
        """在已绑定任务上下文中执行刷流核心流程"""
        if not self._validate_task_reference(task) or not self.downloader:
            report["result"] = "downloader_unavailable"
            return
        if not self._is_current_time_in_range(task):
            report["result"] = "outside_active_time"
            report["reason_counts"]["不在开启时间段"] = 1
            return
        site = SiteOper().get(task.site_id)
        if not site:
            report["result"] = "site_missing"
            return
        ratio_passed, ratio_reason, ratio_status = self._evaluate_site_ratio_control(task, site=site)
        if ratio_status["enabled"]:
            report["site_ratio"] = ratio_status
        if not ratio_passed:
            report["result"] = "site_ratio_blocked"
            report["reason_counts"][ratio_reason] = 1
            return
        # v5.3.1：站点下载量控制
        download_passed, download_reason, download_status = self._evaluate_download_limit_control(task, site=site)
        if download_status["enabled"]:
            report["site_downloaded"] = download_status
        if not download_passed:
            report["result"] = "site_download_limit_blocked"
            report["reason_counts"][download_reason] = 1
            if task.notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=f"[刷流] {task.name} 下载量限制",
                    text=f"站点下载量已达上限：{download_reason}"
                )
            return
        torrent_tasks: Dict[str, dict] = self._current_task_data("torrents", {})
        seeding_size = self.__calculate_seeding_torrents_size(torrent_tasks)
        global_seeding_size = self._calculate_global_seeding_size(task.id, torrent_tasks)
        passed, reason = self.__evaluate_size_condition_for_brush(
            seeding_size,
            global_torrents_size=global_seeding_size,
        )
        if not passed:
            report["result"] = "precondition_blocked"
            report["reason_counts"][reason] = 1
            return
        passed, reason = self.__evaluate_pre_conditions_for_brush()
        if not passed:
            report["result"] = "precondition_blocked"
            report["reason_counts"][reason] = 1
            return
        all_torrent_tasks = self._load_all_torrent_tasks()
        subscribe_titles = self.__get_subscribe_titles()
        self.__brush_site_torrents(
            site=site,
            torrent_tasks=torrent_tasks,
            all_torrent_tasks=all_torrent_tasks,
            subscribe_titles=subscribe_titles,
            report=report,
            global_seeding_size=global_seeding_size,
        )
        self._save_current_task_data("torrents", torrent_tasks)
        self._recalculate_statistics(task.id)

    def _load_all_torrent_tasks(self) -> Dict[str, dict]:
        """聚合所有任务的当前记录以保持跨站点重复种子保护"""
        rows: Dict[str, dict] = {}
        for task_id in self._task_configs:
            task_rows = self._get_task_data(task_id, "torrents") or {}
            rows.update(task_rows)
        return rows

    def _calculate_global_seeding_size(
        self,
        current_task_id: Optional[str] = None,
        current_torrent_tasks: Optional[Dict[str, dict]] = None,
    ) -> float:
        """汇总所有任务未删除种子的体积，并允许使用当前任务的内存快照。"""
        total_size = 0.0
        for task_id in self._task_configs:
            if task_id == current_task_id and current_torrent_tasks is not None:
                task_rows = current_torrent_tasks
            else:
                task_rows = self._get_task_data(task_id, "torrents") or {}
            total_size += self.__calculate_seeding_torrents_size(task_rows)
        return total_size

    def __brush_site_torrents(
        self,
        site: Any,
        torrent_tasks: Dict[str, dict],
        all_torrent_tasks: Dict[str, dict],
        subscribe_titles: Set[str],
        report: dict,
        global_seeding_size: float,
    ) -> None:
        """获取当前任务站点候选并逐项执行保留的选种规则"""
        task = self._get_task_config()
        logger.info(f"刷流任务 [{task.name}] 开始获取站点 {site.name} 的新种子")
        # v5.3.10：复活区支持 - 使用自定义URL获取种子
        if task.resurrect_enabled and task.resurrect_url:
            logger.info(f"刷流任务 [{task.name}] 使用复活区URL: {task.resurrect_url}")
            torrents = self.__fetch_resurrect_torrents(site, task, task.resurrect_url)
        else:
            torrents = TorrentsChain().rss(domain=site.domain) if task.rss_support else TorrentsChain().browse(domain=site.domain)
        if not torrents:
            report["result"] = "no_candidates"
            return
        report["source_count"] = len(torrents)
        if task.except_subscribe:
            before_count = len(torrents)
            torrents = self.__filter_torrents_contains_subscribe(torrents, subscribe_titles)
            report["subscription_excluded"] = before_count - len(torrents)
            if report["subscription_excluded"]:
                report["reason_counts"]["命中订阅内容"] = report["subscription_excluded"]
        report["candidate_count"] = len(torrents)
        torrents.sort(key=lambda item: item.pubdate or "", reverse=True)
        seeding_size = self.__calculate_seeding_torrents_size(torrent_tasks)
        for torrent in torrents:
            passed, reason = self.__evaluate_pre_conditions_for_brush(include_network_conditions=False)
            if not passed:
                report["reason_counts"][reason] += 1
                report["result"] = "precondition_blocked"
                break
            passed, reason = self.__evaluate_size_condition_for_brush(
                seeding_size,
                torrent.size,
                global_torrents_size=global_seeding_size,
            )
            if not passed:
                report["reason_counts"][reason] += 1
                continue
            passed, reason = self.__evaluate_conditions_for_brush(torrent, all_torrent_tasks)
            if not passed:
                report["reason_counts"][reason] += 1
                continue
            hash_string = self.__download(torrent)
            if not hash_string:
                report["reason_counts"]["下载器添加失败"] += 1
                continue
            torrent_task = self._torrent_to_task_record(torrent, site, task)
            torrent_tasks[hash_string] = torrent_task
            all_torrent_tasks[hash_string] = torrent_task
            seeding_size += torrent.size
            global_seeding_size += torrent.size
            report["added_count"] += 1
            report["added_titles"].append(torrent.title)
            self.eventmanager.send_event(
                etype=EventType.PluginTriggered,
                data={
                    "plugin_id": self.__class__.__name__,
                    "event_name": "brushflow_download_added",
                    "hash": hash_string,
                    "data": torrent_task,
                    "downloader": self.service_info.name,
                },
            )
            logger.info(f"刷流任务 [{task.name}] 新增种子：{torrent.title}|{torrent.description}")
            # v5.5.2: TTGL积分商店自动购买并使用折扣
            if task.ttgl_discount_enabled:
                self.__ttgl_apply_discount(torrent, task)
            self.__send_add_message(torrent)
        report["filtered_count"] = max(report["candidate_count"] - report["added_count"], 0)
        report["result"] = "completed"

    def __fetch_resurrect_torrents(self, site: Any, task: Any, url: str) -> list:
        """v5.3.10：从复活区自定义URL获取种子"""
        from app.helper.sites import SitesHelper
        from app.chain.torrents import TorrentsChain
        from urllib.parse import urlparse

        site_obj = SitesHelper().get_indexer(site.domain)
        if not site_obj:
            logger.error(f"站点 {site.domain} 不存在")
            return []

        # 解析自定义URL，提取path
        parsed = urlparse(url)
        custom_path = parsed.path.lstrip("/")
        if parsed.query:
            custom_path += "?" + parsed.query

        # 临时替换browse.path，保留list/fields选择器
        original_browse = site_obj.get("browse") or {}
        torrents = site_obj.get("torrents") or {}

        # 从torrents获取list/fields选择器，如果没有则使用空字典
        browse_config = {
            **original_browse,
            "path": custom_path,
            "start": 0,
            "list": original_browse.get("list") or torrents.get("list", {}),
            "fields": original_browse.get("fields") or torrents.get("fields", {}),
        }
        site_obj["browse"] = browse_config

        try:
            torrents = TorrentsChain().refresh_torrents(site=site_obj, page=0)
            # v5.3.10：按最小下载人数过滤
            if task.resurrect_min_peers is not None:
                original_count = len(torrents or [])
                torrents = [t for t in (torrents or []) if (t.peers or 0) > task.resurrect_min_peers]
                logger.info(f"复活区下载人数过滤: {original_count} -> {len(torrents)} (>{task.resurrect_min_peers})")
            logger.info(f"复活区获取到 {len(torrents)} 个种子")
            return torrents or []
        except Exception as e:
            logger.error(f"复活区获取种子失败: {e}")
            return []
        finally:
            if original_browse:
                site_obj["browse"] = original_browse
            elif "browse" in site_obj:
                del site_obj["browse"]

    @staticmethod
    def _torrent_to_task_record(torrent: TorrentInfo, site: Any, task: BrushTaskConfig) -> dict:
        """把站点候选种子转换为可持久化的任务记录"""
        return {
            "task_id": task.id,
            "task_name": task.name,
            "site": site.id,
            "site_name": site.name,
            "title": torrent.title,
            "size": torrent.size,
            "pubdate": torrent.pubdate,
            "description": torrent.description,
            "imdbid": torrent.imdbid,
            "page_url": torrent.page_url,
            "date_elapsed": torrent.date_elapsed,
            "freedate": torrent.freedate,
            "uploadvolumefactor": torrent.uploadvolumefactor,
            "downloadvolumefactor": torrent.downloadvolumefactor,
            "hit_and_run": torrent.hit_and_run or task.site_hr_active,
            "volume_factor": torrent.volume_factor,
            "freedate_diff": torrent.freedate_diff,
            "ratio": 0,
            "downloaded": 0,
            "uploaded": 0,
            "seeding_time": 0,
            "deleted": False,
            "time": time.time(),
        }

    @staticmethod
    def _new_run_report(kind: str) -> dict:
        """创建一条结构稳定的运行诊断记录"""
        return {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "started_at": BrushFlow._now_iso(),
            "finished_at": None,
            "success": None,
            "result": None,
            "error": None,
            "source_count": 0,
            "subscription_excluded": 0,
            "candidate_count": 0,
            "filtered_count": 0,
            "added_count": 0,
            "deleted_count": 0,
            "active_count": 0,
            "reason_counts": Counter(),
            "added_titles": [],
        }

    @staticmethod
    def _now_iso() -> str:
        """返回带本地时区且精确到秒的时间文本"""
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _is_completed(torrent_info: dict) -> bool:
        """判断种子是否已完成下载（统一判断逻辑，替代重复表达式）"""
        completion_on = torrent_info.get("completion_on", 0)
        amount_left = torrent_info.get("amount_left", 0)
        progress = torrent_info.get("progress", 0)
        total_size = torrent_info.get("total_size", 0)
        return (completion_on > 0) or (amount_left == 0) if total_size > 0 else False

    def __evaluate_size_condition_for_brush(
        self,
        torrents_size: float,
        add_torrent_size: float = 0.0,
        global_torrents_size: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """校验当前任务及所有任务新增种子后是否超过保种体积。"""
        task = self._get_task_config()
        if not task:
            return False, "任务配置不存在"
        estimated_size = torrents_size + (add_torrent_size or 0)
        if task.disksize:
            limit_size = float(task.disksize) * 1024 ** 3
            if estimated_size > limit_size:
                reason = (
                    f"预计做种体积 {self.__bytes_to_gb(estimated_size):.1f} GB，"
                    f"超过任务保种上限 {task.disksize} GB"
                )
                return False, reason
        global_disksize = getattr(self, "_global_disksize", None)
        if global_disksize:
            if global_torrents_size is None:
                global_torrents_size = self._calculate_global_seeding_size()
            estimated_global_size = global_torrents_size + (add_torrent_size or 0)
            if estimated_global_size > float(global_disksize) * 1024 ** 3:
                reason = (
                    f"预计全局做种体积 {self.__bytes_to_gb(estimated_global_size):.1f} GB，"
                    f"超过全局保种上限 {global_disksize} GB"
                )
                return False, reason
        return True, None

    def __evaluate_pre_conditions_for_brush(
        self,
        include_network_conditions: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """校验单任务与全局下载并发及上传下载带宽。"""
        task = self._get_task_config()
        if not task:
            return False, "任务配置不存在"
        global_maxdlcount = getattr(self, "_global_maxdlcount", None)
        if global_maxdlcount and self.__get_global_downloading_count() >= int(global_maxdlcount):
            return False, f"全局同时下载任务数达到上限 {global_maxdlcount}"
        if task.maxdlcount and self.__get_downloading_count() >= int(task.maxdlcount):
            return False, f"同时下载任务数达到上限 {task.maxdlcount}"
        if not include_network_conditions:
            return True, None
        avg_upload_speed, avg_download_speed = self.__get_average_bandwidth()
        if avg_upload_speed is None or avg_download_speed is None:
            return True, None
        global_maxupspeed = getattr(self, "_global_maxupspeed", None)
        global_maxdlspeed = getattr(self, "_global_maxdlspeed", None)
        if global_maxupspeed and avg_upload_speed >= float(global_maxupspeed) * 1024 * 1024:
            return False, f"全局总上传带宽达到上限 {global_maxupspeed} MB/s"
        if global_maxdlspeed and avg_download_speed >= float(global_maxdlspeed) * 1024 * 1024:
            return False, f"全局总下载带宽达到上限 {global_maxdlspeed} MB/s"
        if task.maxupspeed and avg_upload_speed >= float(task.maxupspeed) * 1024 * 1024:
            return False, f"总上传带宽达到上限 {task.maxupspeed} MB/s"
        if task.maxdlspeed and avg_download_speed >= float(task.maxdlspeed) * 1024 * 1024:
            return False, f"总下载带宽达到上限 {task.maxdlspeed} MB/s"
        return True, None

    def __evaluate_conditions_for_brush(
        self,
        torrent: TorrentInfo,
        torrent_tasks: Dict[str, dict],
    ) -> Tuple[bool, Optional[str]]:
        """按原有促销、H&R、规则、体积、人数和发布时间筛选候选"""
        task = self._get_task_config()
        if not task:
            return False, "任务配置不存在"
        task_key = f"{torrent.site_name}{torrent.title}"
        if any(task_key == f"{item.get('site_name')}{item.get('title')}" for item in torrent_tasks.values()):
            return False, "重复种子"
        if torrent.page_url:
            page_key = f"{torrent.site_name}{torrent.page_url}"
            if any(page_key == f"{item.get('site_name')}{item.get('page_url')}" for item in torrent_tasks.values()):
                return False, "重复种子"
        if torrent.title and any(
            torrent.site_name != item.get("site_name")
            and torrent.title == item.get("title")
            and not item.get("deleted")
            and (item.get("downloaded") or 0) < (item.get("size") or 0)  # 使用缓存数据判断下载中
            for item in torrent_tasks.values()
        ):
            return False, "其他站点存在尚未下载完成的相同种子"
        if task.freeleech and torrent.downloadvolumefactor != 0:
            return False, "非免费种子"
        if task.freeleech == "2xfree" and torrent.uploadvolumefactor != 2:
            return False, "非双倍上传种子"
        if task.hr == "yes" and torrent.hit_and_run:
            return False, "存在 H&R"
        if task.include:
            include_match = bool(
                (torrent.title and re.search(task.include, torrent.title, re.I))
                or (torrent.description and re.search(task.include, torrent.description, re.I))
            )
            if not include_match:
                return False, "不符合包含规则"
        if task.exclude:
            exclude_match = bool(
                (torrent.title and re.search(task.exclude, torrent.title, re.I))
                or (torrent.description and re.search(task.exclude, torrent.description, re.I))
            )
            if exclude_match:
                return False, "符合排除规则"
        if task.size:
            if task._size_range is None:
                return False, "种子大小范围配置无效"
            if len(task._size_range) == 1 and torrent.size < task._size_range[0]:
                return False, "种子大小低于下限"
            if len(task._size_range) > 1 and not task._size_range[0] <= torrent.size <= task._size_range[1]:
                return False, "种子大小不在范围内"
        if task.seeder:
            if task._seeder_range is None:
                return False, "做种人数范围配置无效"
            seeders = torrent.seeders or 0
            if len(task._seeder_range) == 1 and seeders > task._seeder_range[0]:
                return False, "做种人数超过上限"
            if len(task._seeder_range) > 1 and not task._seeder_range[0] <= seeders <= task._seeder_range[1]:
                return False, "做种人数不在范围内"
        # 发布时间过滤（v5.5.7）：新种（pubtime 上限）与老种（old_seed_min_age 下限）为 OR 关系——
        # 只设一方用该单方条件；双方都设时满足任一侧即下载；pubdate 未知（解析失败=0分钟）不满足老种下限
        if task.pubtime or task.old_seed_min_age:
            if task.pubtime and task._pubtime_range is None:
                return False, "发布时间范围配置无效"
            pubdate_minutes = self.__get_pubminutes(torrent.pubdate) - task.timezone_offset * 60
            new_ok = None
            if task.pubtime:
                if len(task._pubtime_range) == 1:
                    new_ok = pubdate_minutes <= task._pubtime_range[0]
                else:
                    new_ok = task._pubtime_range[0] <= pubdate_minutes <= task._pubtime_range[1]
            old_ok = (pubdate_minutes >= task.old_seed_min_age * 60) if task.old_seed_min_age else None
            if new_ok is None:
                # 仅老种条件：必须达到发布下限
                if not old_ok:
                    return False, "发布时间未达老种下限"
            elif old_ok is None:
                # 仅新种条件：原有 pubtime 语义不变
                if not new_ok:
                    if len(task._pubtime_range) == 1:
                        return False, "发布时间超过上限"
                    return False, "发布时间不在范围内"
            elif not (new_ok or old_ok):
                # 双方都设：OR 语义，两侧都不满足才拒绝
                return False, "发布时间不在新种范围内且未达老种下限"
        return True, None

    def check(self, task_id: Optional[str] = None, wait_for_lock: bool = True) -> None:
        """执行状态同步、删种和归档，到期检查可等待同任务的当前操作"""
        task = self._get_task_config(task_id)
        if not task or not self.get_state() or not task.enabled:
            return
        # v5.3.9：刷流优先 — 如果 brush 正在等待、运行或排队，跳过本次检查
        if self._brush_pending.get(task.id):
            logger.info(f"刷流任务 [{task.name}] 刷流等待中，本次检查跳过")
            return
        task_lock = self._task_locks.setdefault(task.id, threading.Lock())
        with self._runtime_lock:
            runtime = self._runtime.get(task.id, {})
            if runtime.get("operation") == "brush" and runtime.get("state") in {"queued", "running"}:
                logger.info(f"刷流任务 [{task.name}] 刷流执行中，本次检查跳过")
                return
        # v5.3.1：check 服务等待 lock 释放，确保站点条件检查能执行
        acquired = task_lock.acquire(blocking=wait_for_lock)
        if not acquired:
            logger.info(f"刷流任务 [{task.name}] 获取锁超时，本轮检查跳过")
            return
        report = self._new_run_report("check")
        self._set_runtime(task.id, state="running", operation="check", last_error=None)
        try:
            with self._task_scope(task.id):
                self._run_check(task, report)
            report["success"] = report.get("result") not in {"downloader_unavailable", "downloader_error"}
        except Exception as err:
            report.update({"success": False, "error": str(err)})
            self._set_runtime(task.id, last_error=str(err))
            logger.error(f"刷流任务 [{task.name}] 检查失败：{str(err)}")
        finally:
            task_lock.release()
        if self._global_dynamic_delete_enabled():
            try:
                global_deleted_count = self._run_global_dynamic_delete()
                report["global_deleted_count"] = global_deleted_count
                report["deleted_count"] = report.get("deleted_count", 0) + global_deleted_count
            except Exception as err:
                report.update({"success": False, "error": str(err)})
                self._set_runtime(task.id, last_error=str(err))
                logger.error(f"全局动态删种失败：{str(err)}")
        report["finished_at"] = self._now_iso()
        self._append_run(task.id, report)
        self._set_runtime(task.id, state="idle", operation=None)

    def _execute_check(self, task: BrushTaskConfig, report: dict) -> None:
        """执行刷流种子检查的新入口（使用上下文对象）"""
        logger.info(f"刷流任务 [{task.name}] 开始执行检查，skip_check_no_active={task.skip_check_no_active}, download_limit_enabled={task.download_limit_enabled}")
        ctx = BrushCheckContext(task=task, report=report, downloader=self.downloader)
        
        # 步骤1: 前置校验（下载器连接检查）
        if not self._validate_task_reference(ctx.task) or not ctx.downloader:
            ctx.report["result"] = "downloader_unavailable"
            return
        
        # v5.3.1：先读取本地缓存判断活跃种子数
        # 注意：这里只读取缓存，不调用下载器API
        ctx.torrent_tasks = self._current_task_data("torrents", {})
        
        # v5.3.12: 空间不足时删除种子（全局跨任务统一处理）
        disk_space_delete = getattr(self, "_global_disk_space_delete", False)
        disk_space_threshold = getattr(self, "_global_disk_space_threshold", None)
        disk_space_target = getattr(self, "_global_disk_space_target", None)
        if disk_space_delete and disk_space_threshold:
            free_space = self.__get_downloader_free_space(task)
            if free_space is not None:
                free_gb = self.__bytes_to_gb(free_space)
                report["free_space_gb"] = round(free_gb, 2)
                if free_gb < float(disk_space_threshold):
                    # 跨任务互斥：如果已有任务在执行空间不足删种，跳过
                    with self._runtime_lock:
                        if self._disk_space_cleanup_running:
                            logger.info(f"刷流任务 [{task.name}] 空间不足删种已在其他任务执行中，跳过")
                            report["result"] = "disk_space_skipped"
                            report["reason"] = "其他任务正在执行空间不足删种"
                            return
                        self._disk_space_cleanup_running = True
                    try:
                        logger.warning(f"磁盘空间不足: {free_gb:.1f} GB < {disk_space_threshold} GB，开始全局删种")
                        target_val = float(disk_space_target) if disk_space_target else None
                        deleted_count = self.__delete_all_disk_space_seeds(task, target_val)
                        report["disk_space_deleted"] = deleted_count
                        report["result"] = "disk_space_cleanup"
                    finally:
                        with self._runtime_lock:
                            self._disk_space_cleanup_running = False
                    return

        # 无活跃种子时跳过整个检查流程
        if ctx.task.skip_check_no_active:
            active_count = sum(1 for item in ctx.torrent_tasks.values() if not item.get("deleted"))
            ctx.report["active_count"] = active_count
            if active_count == 0:
                logger.info(f"刷流任务 [{ctx.task.name}] 无活跃种子，跳过状态检查")
                ctx.report.update({
                    "result": "no_active",
                    "skipped_reason": "无活跃种子"
                })
                self._recalculate_statistics(ctx.task.id)
                return
            # 有活跃种子，继续正常流程
            self._step_fetch_data(ctx)
        else:
            # 未启用 skip_check_no_active，正常流程
            self._step_fetch_data(ctx)
        
        # 步骤3: 站点条件检查（分享率、下载量）
        site = SiteOper().get(task.site_id)
        logger.info(f"刷流任务 [{task.name}] 开始检查站点条件，download_limit_enabled={task.download_limit_enabled}")
        ratio_passed, ratio_reason, ratio_status = self._evaluate_site_ratio_control(task, site=site)
        if ratio_status["enabled"]:
            report["site_ratio"] = ratio_status
            logger.info(f"刷流任务 [{task.name}] 分享率检查：passed={ratio_passed}, reason={ratio_reason}")
        if not ratio_passed:
            report["result"] = "site_ratio_blocked"
            report["reason_counts"][ratio_reason] = 1
            self._send_once_daily(task, "site_ratio_blocked", f"[刷流] {task.name} 分享率限制", f"站点分享率已达上限：{ratio_reason}")
            return
        
        download_passed, download_reason, download_status = self._evaluate_download_limit_control(task, site=site)
        logger.info(f"刷流任务 [{task.name}] 下载量检查：passed={download_passed}, reason={download_reason}, enabled={download_status['enabled']}")
        if download_status["enabled"]:
            report["site_downloaded"] = download_status
        if not download_passed:
            report["result"] = "site_download_limit_blocked"
            report["reason_counts"][download_reason] = 1
            self._send_once_daily(task, "site_download_limit_blocked", f"[刷流] {task.name} 下载量限制", f"站点下载量已达上限：{download_reason}")
            return
        
        # 步骤4: 同步标签状态
        self._step_sync_tags(ctx)
        
        # 步骤4: 更新种子状态
        self._step_update_state(ctx)
        
        # 步骤5: 标记消失种子
        self._step_mark_missing(ctx)
        
        # 步骤6: 评估删除条件
        self._step_evaluate_deletions(ctx)
        
        # 步骤7: 执行删除
        self._step_execute_deletions(ctx)
        
        # 步骤8: 后处理（归档+清理标签）
        self._step_post_process(ctx)
        
        # 步骤9: 限速检查
        self._step_check_speed_limit(ctx)
        
        # 步骤10: 统计与报告
        self._step_finalize_report(ctx)

    def _send_once_daily(self, task: BrushTaskConfig, notify_key: str, title: str, text: str) -> bool:
        """发送通知，每日仅发送一次（持久化存储去重）"""
        if not task.notify:
            return False
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 检查任务数据中的通知发送标记
        notify_history = self._get_task_data(task.id, "notify_history") or {}
        if notify_history.get(notify_key) == today:
            return False  # 今天已发送过
        
        # 发送通知
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=title,
            text=text
        )
        
        # 持久化标记
        notify_history[notify_key] = today
        self._save_task_data(task.id, "notify_history", notify_history)
        return True

    def _step_pre_check(self, ctx: BrushCheckContext) -> bool:
        """前置校验，返回 True 表示应终止"""
        if not self._validate_task_reference(ctx.task) or not ctx.downloader:
            ctx.report["result"] = "downloader_unavailable"
            return True
        
        # v5.3.1：无活跃种子时跳过状态检查（使用本地缓存）
        if ctx.task.skip_check_no_active:
            active_count = sum(1 for item in ctx.torrent_tasks.values() if not item.get("deleted"))
            if active_count == 0:
                logger.info(f"刷流任务 [{ctx.task.name}] 无活跃种子，跳过状态检查")
                ctx.report.update({
                    "result": "no_active",
                    "active_count": 0,
                    "skipped_reason": "无活跃种子"
                })
                self._recalculate_statistics(ctx.task.id)
                return True
        return False

    def _step_fetch_data(self, ctx: BrushCheckContext) -> None:
        """获取下载器数据（唯一读取调用）"""
        logger.info(f"刷流任务 [{ctx.task.name}] 开始获取下载器数据...")
        ctx.torrent_tasks = self._current_task_data("torrents", {})
        ctx.unmanaged_tasks = self._current_task_data("unmanaged", {})
        logger.info(f"刷流任务 [{ctx.task.name}] 本地缓存种子数: {len(ctx.torrent_tasks)}")
        
        seeding_torrents, error = ctx.downloader.get_torrents()
        if error:
            logger.error(f"刷流任务 [{ctx.task.name}] 获取下载器数据失败: {error}")
            ctx.report["result"] = "downloader_error"
            raise RuntimeError("连接下载器出错")
        
        logger.info(f"刷流任务 [{ctx.task.name}] 下载器返回种子数: {len(seeding_torrents)}")
        ctx.seeding_torrents = seeding_torrents
        ctx.seeding_torrents_dict = {self.__get_hash(t): t for t in seeding_torrents}
        ctx.check_hashes = list(ctx.torrent_tasks.keys())
        logger.info(f"刷流任务 [{ctx.task.name}] 准备检查的种子hash数: {len(ctx.check_hashes)}")

    def _step_sync_tags(self, ctx: BrushCheckContext) -> None:
        """同步标签状态"""
        # v5.3.1：即使本地缓存为空，也要检查下载器中的种子并同步标签
        self.__update_seeding_tasks_based_on_tags(ctx.torrent_tasks, ctx.unmanaged_tasks, ctx.seeding_torrents_dict)
        # v5.3.1：更新 check_hashes 以反映最新的种子列表
        ctx.check_hashes = list(ctx.torrent_tasks.keys())
        if not ctx.check_hashes:
            self._cleanup_unused_task_tag(ctx.task, torrents=ctx.seeding_torrents)
            ctx.report.update({"result": "no_managed_torrents", "active_count": 0})
            self._recalculate_statistics(ctx.task.id)
            return

    def _step_update_state(self, ctx: BrushCheckContext) -> None:
        """更新种子状态"""
        ctx.check_torrents = [ctx.seeding_torrents_dict[h] for h in ctx.check_hashes if h in ctx.seeding_torrents_dict]
        self.__update_seeding_tasks_state(ctx.check_torrents, ctx.torrent_tasks)

    def _step_mark_missing(self, ctx: BrushCheckContext) -> None:
        """标记消失的种子"""
        self.__update_undeleted_torrents_missing_in_downloader(ctx.torrent_tasks, ctx.check_hashes, ctx.seeding_torrents)
        ctx.filtered_torrents = self.__filter_torrents_by_tag(ctx.check_torrents, ctx.task.delete_except_tags)

    def _step_evaluate_deletions(self, ctx: BrushCheckContext) -> None:
        """评估删除条件"""
        # 托管至全局的任务不执行任务级删种，由全局动态删种统一处理
        if ctx.task.proxy_delete:
            ctx.need_delete_hashes = []
        else:
            ctx.need_delete_hashes = self.__delete_torrent_for_evaluate_conditions(ctx.filtered_torrents, ctx.torrent_tasks)
        ctx.need_delete_hashes = list(dict.fromkeys(ctx.need_delete_hashes or []))

    def _step_execute_deletions(self, ctx: BrushCheckContext) -> None:
        """执行删除操作"""
        if not ctx.need_delete_hashes:
            return
        
        if DownloaderHelper().is_downloader("qbittorrent", service=self.service_info):
            self.__qb_torrents_reannounce(ctx.need_delete_hashes)
        
        if ctx.downloader.delete_torrents(ids=ctx.need_delete_hashes, delete_file=True):
            ctx.deleted_from_downloader = True
            for h in ctx.need_delete_hashes:
                if h in ctx.torrent_tasks:
                    ctx.torrent_tasks[h]["deleted"] = True
                    ctx.torrent_tasks[h]["deleted_time"] = time.time()

    def __delete_all_disk_space_seeds(self, task: BrushTaskConfig, target_gb: Optional[float]) -> int:
        """空间不足全局删种 — 跨任务统一处理
        逻辑：
        1. 不区分种子所属任务，首先统计所有已完成种子，按上传速度排序
        2. 依次累加已完成种子体积，获取预计剩余空间1
        3. 当预计剩余空间1 > 目标空间时，执行删种，退出
        4. 若已完成种子不够，剩余所有种子按上传速度排序，依次累加种子体积×进度，获取预计剩余空间2
        5. 当预计剩余空间2 > 目标空间时，开始删种，退出
        若 target_gb 为 None，则删除所有已完成种子。
        排除 HR（Hit & Run）种子。
        """
        downloader = self.downloader
        if not downloader:
            return 0

        try:
            torrents, error = downloader.get_torrents()
            if error or not torrents:
                return 0

            # 获取当前剩余空间
            free_space = self.__get_downloader_free_space(task)
            if free_space is None:
                return 0
            current_free_gb = self.__bytes_to_gb(free_space)

            now_timestamp = int(time.time())

            # 收集所有刷流种子（带标签识别所属任务）
            all_completed = []
            all_active = []

            for torrent in torrents:
                torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
                tags_str = torrent_info.get("tags", "")
                tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]

                # 查找种子属于哪个任务
                seed_task = self._get_task_by_tags(tags_list)
                if not seed_task:
                    continue

                torrent_hash = self.__get_hash(torrent)
                if not torrent_hash:
                    continue

                # 排除 HR 种子（通过 QB 标签）
                if "HR" in tags_list:
                    continue

                total_size = torrent_info.get("total_size", 0)
                progress = torrent_info.get("progress", 0)
                upspeed = torrent_info.get("upspeed", 0)

                if self._is_completed(torrent_info):
                    all_completed.append({
                        "hash": torrent_hash,
                        "info": torrent_info,
                        "task": seed_task,
                        "size": total_size,
                        "upspeed": upspeed,
                    })
                else:
                    all_active.append({
                        "hash": torrent_hash,
                        "info": torrent_info,
                        "task": seed_task,
                        "size": total_size,
                        "progress": progress,
                        "upspeed": upspeed,
                    })

            if not all_completed and not all_active:
                return 0

            # 按上传速度升序排序
            all_completed.sort(key=lambda x: x["upspeed"])
            all_active.sort(key=lambda x: x["upspeed"])

            # 阶段1：累加已完成种子体积
            delete_candidates = []
            accumulated_gb = 0.0
            if target_gb is None:
                # 无目标：全部已完成种子加入删除列表
                delete_candidates.extend(all_completed)
            else:
                for item in all_completed:
                    delete_candidates.append(item)
                    accumulated_gb += self.__bytes_to_gb(item["size"])
                    expected_free_gb = current_free_gb + accumulated_gb
                    if expected_free_gb >= target_gb:
                        break

                # 如果已完成种子不够，继续阶段2
                if current_free_gb + accumulated_gb < target_gb:
                    for item in all_active:
                        delete_candidates.append(item)
                        # 活跃种子：按已下载比例估算可释放空间
                        effective_size = item["size"] * min(item["progress"], 1.0)
                        accumulated_gb += self.__bytes_to_gb(effective_size)
                        expected_free_gb = current_free_gb + accumulated_gb
                        if expected_free_gb >= target_gb:
                            break

            if not delete_candidates:
                return 0

            # 执行删除
            delete_hashes = [c["hash"] for c in delete_candidates]
            logger.warning(
                f"空间不足全局删种: 将删除 {len(delete_candidates)} 个种子, "
                f"预计释放 {accumulated_gb:.2f} GB, 目标 {target_gb} GB"
            )

            # 按种子所属任务发送通知
            for candidate in delete_candidates:
                self.__send_disk_space_delete_message(
                    candidate["task"], candidate["info"]
                )

            if DownloaderHelper().is_downloader("qbittorrent", service=self.service_info):
                self.__qb_torrents_reannounce(delete_hashes)

            if downloader.delete_torrents(ids=delete_hashes, delete_file=True):
                logger.info(f"空间不足全局删种完成: 已删除 {len(delete_candidates)} 个种子")
                return len(delete_candidates)

        except Exception as err:
            logger.error(f"空间不足全局删种失败: {err}")
        return 0

    def _get_task_by_tags(self, tags_list: List[str]) -> Optional[BrushTaskConfig]:
        """根据种子标签列表查找所属任务"""
        for tag in tags_list:
            if tag.startswith("刷流-"):
                site_name = tag[len("刷流-"):]
                for task in self._task_configs.values():
                    task_site = self._get_site_name(task.site_id)
                    if task_site and task_site == site_name:
                        return task
        return None

    def __send_disk_space_delete_message(self, task: BrushTaskConfig, torrent_info: dict) -> None:
        """发送空间不足删种通知，格式与条件删种一致"""
        if not task or not task.notify:
            return
        seeding_time = torrent_info.get("seeding_time", 0)
        if seeding_time:
            hours = int(seeding_time // 3600)
            minutes = int((seeding_time % 3600) // 60)
            if hours > 0:
                seeding_time_str = f"{hours}小时{minutes}分钟"
            else:
                seeding_time_str = f"{minutes}分钟"
        else:
            seeding_time_str = "未知"
        uploaded = torrent_info.get("uploaded", 0)
        size = torrent_info.get("total_size", 0)
        uploaded_gb = self.__bytes_to_gb(uploaded)
        size_gb = self.__bytes_to_gb(size)
        site_name = self._get_site_name(task.site_id) or "未知"
        text = (
            f"任务：{task.name}\n"
            f"站点：{site_name}\n"
            f"标题：{torrent_info.get('title') or '未知'}\n"
            f"做种时长：{seeding_time_str}\n"
            f"上传量：{uploaded_gb:.2f} GB / 种子体积：{size_gb:.2f} GB\n"
            f"原因：空间不足自动删种"
        )
        self.post_message(mtype=NotificationType.SiteMessage, title="【刷流任务种子删除】", text=text)

    def _step_post_process(self, ctx: BrushCheckContext) -> None:
        """后处理：归档+清理标签"""
        self.__auto_archive_tasks(ctx.torrent_tasks)
        self._cleanup_unused_task_tag(
            ctx.task,
            torrents=None if ctx.deleted_from_downloader else ctx.seeding_torrents,
        )
        self._save_current_task_data("torrents", ctx.torrent_tasks)

    def _step_check_speed_limit(self, ctx: BrushCheckContext) -> None:
        """限速检查（使用缓存数据）"""
        self.__check_speed_limit(ctx.task, torrents=ctx.seeding_torrents)

    def _step_finalize_report(self, ctx: BrushCheckContext) -> None:
        """统计与报告"""
        self._recalculate_statistics(ctx.task.id)
        ctx.report.update({
            "result": "completed",
            "deleted_count": len(ctx.need_delete_hashes),
            "active_count": sum(1 for item in ctx.torrent_tasks.values() if not item.get("deleted")),
        })

    def _run_check(self, task: BrushTaskConfig, report: dict) -> None:
        """在已绑定任务上下文中执行刷流种子检查（适配器方法）"""
        self._execute_check(task, report)

    def __update_seeding_tasks_state(self, torrents: List[Any], torrent_tasks: Dict[str, dict]) -> None:
        """更新当前任务种子的上下传、分享率和做种时间，并计算近期上传速度"""
        now = time.time()
        now_timestamp = int(now)
        for torrent in torrents:
            torrent_hash = self.__get_hash(torrent)
            torrent_task = torrent_tasks.get(torrent_hash)
            if not torrent_task:
                continue
            torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
            prev_uploaded = torrent_task.get("prev_uploaded", 0)
            prev_time = torrent_task.get("prev_time", 0)

            uploaded = torrent_info.get("uploaded", 0)
            seeding_time = torrent_info.get("seeding_time", 0)
            dltime = torrent_info.get("dltime", 0)

            # 计算近期上传速度（基于上次检查时的数据）
            current_speed = 0
            if prev_uploaded and prev_time and now > prev_time:
                delta_uploaded = uploaded - prev_uploaded
                delta_time = now - prev_time
                if delta_time > 0:
                    current_speed = int(delta_uploaded / delta_time)

            # 更新历史采样点
            torrent_task["prev_uploaded"] = uploaded
            torrent_task["prev_time"] = now

            # 计算近3次采样的平均速度（包括速度为0的情况）
            speed_samples = torrent_task.get("speed_samples", [])
            if prev_uploaded is not None:
                speed_samples.append(current_speed)
                if len(speed_samples) > 3:
                    speed_samples = speed_samples[-3:]
                torrent_task["speed_samples"] = speed_samples

            avg_upspeed = int(sum(speed_samples) / len(speed_samples)) if speed_samples else (
                int(uploaded / dltime) if dltime else (int(uploaded / seeding_time) if seeding_time else 0)
            )

            torrent_task.update(
                {
                    "downloaded": torrent_info.get("downloaded"),
                    "uploaded": uploaded,
                    "ratio": torrent_info.get("ratio"),
                    "seeding_time": seeding_time,
                    "avg_upspeed": avg_upspeed,
                }
            )

    def __update_seeding_tasks_based_on_tags(
        self,
        torrent_tasks: Dict[str, dict],
        unmanaged_tasks: Dict[str, dict],
        seeding_torrents_dict: Dict[str, Any],
    ) -> None:
        """按任务唯一标签同步 qBittorrent 中的纳管和移除状态"""
        task = self._get_task_config()
        if not task or not DownloaderHelper().is_downloader("qbittorrent", service=self.service_info):
            return
        added_tasks: List[dict] = []
        removed_tasks: List[dict] = []
        reset_tasks: List[dict] = []
        for torrent_hash, torrent in seeding_torrents_dict.items():
            tags = self.__get_label(torrent)
            has_unique_tag = task.brush_tag in tags
            has_global_tag = self.GLOBAL_BRUSH_TAG in tags
            existing = torrent_hash in torrent_tasks
            adopt_legacy = (
                has_global_tag
                and not existing
                and self._is_primary_task_for_torrent(task, torrent)
            )
            managed = has_unique_tag or (has_global_tag and existing) or adopt_legacy
            if managed:
                if not existing:
                    torrent_task = unmanaged_tasks.pop(torrent_hash, None) or self.__convert_torrent_info_to_task(torrent)
                    torrent_task.update({"task_id": task.id, "task_name": task.name})
                    torrent_tasks[torrent_hash] = torrent_task
                    added_tasks.append(torrent_task)
                    # v5.3.7: 确保纳管种子具有任务专属标签（处理adopt_legacy等无标签场景）
                    if not has_unique_tag and DownloaderHelper().is_downloader("qbittorrent", service=self.service_info):
                        try:
                            self.downloader.set_torrents_tag(ids=torrent_hash, tags=[task.brush_tag])
                            logger.info(f"刷流任务 [{task.name}] 为纳管种子添加标签: {task.brush_tag}")
                        except Exception as err:
                            logger.warning(f"刷流任务 [{task.name}] 添加标签失败: {err}")
                elif torrent_tasks[torrent_hash].get("deleted"):
                    torrent_tasks[torrent_hash]["deleted"] = False
                    torrent_tasks[torrent_hash].pop("deleted_time", None)
                    reset_tasks.append(torrent_tasks[torrent_hash])
            elif existing:
                unmanaged_tasks[torrent_hash] = torrent_tasks.pop(torrent_hash)
                removed_tasks.append(unmanaged_tasks[torrent_hash])
        self._save_current_task_data("torrents", torrent_tasks)
        self._save_current_task_data("unmanaged", unmanaged_tasks)
        if added_tasks:
            self.__log_and_send_torrent_task_update_message(
                "【刷流任务种子加入】", "纳入刷流管理", "刷流任务标签匹配", added_tasks
            )
        if removed_tasks:
            self.__log_and_send_torrent_task_update_message(
                "【刷流任务种子移除】", "移除刷流管理", "刷流任务标签移除", removed_tasks
            )
        if reset_tasks:
            self.__log_and_send_torrent_task_update_message(
                "【刷流任务状态更新】", "恢复为正常", "下载器中仍存在对应种子", reset_tasks
            )

    def _is_primary_task_for_torrent(self, task: BrushTaskConfig, torrent: Any) -> bool:
        """仅让同站点第一项任务接管没有唯一标签的旧版刷流种子"""
        site_id, _ = self.__get_site_by_torrent(torrent)
        if site_id != task.site_id:
            return False
        site_tasks = [item for item in self._task_configs.values() if item.site_id == site_id]
        return bool(site_tasks and site_tasks[0].id == task.id)

    def __evaluate_conditions_for_delete(
        self,
        torrent_info: dict,
        torrent_task: dict,
    ) -> Tuple[bool, str]:
        """评估普通与 H&R 种子的原有删除条件"""
        task = self._get_task_config()
        if not task:
            return False, "任务配置不存在"
        # v5.3.1：只删除已完成种子
        if task.only_delete_completed:
            is_completed = self._is_completed(torrent_info)
            if not is_completed:
                return False, "种子未完成下载"
        # v5.3.1：最小做种时间检查（单位：分钟）
        if task.only_delete_completed and task.min_seed_time and torrent_info.get("seeding_time", 0) < float(task.min_seed_time) * 60:
            return False, f"做种时间未达到最低要求 {task.min_seed_time} 分钟"
        hit_and_run = bool(torrent_task.get("hit_and_run"))
        if hit_and_run and (task.hr_seed_time or task.seed_ratio):
            if task.hr_seed_time and torrent_info.get("seeding_time", 0) >= float(task.hr_seed_time) * 3600:
                return True, f"H&R 做种时间达到 {task.hr_seed_time} 小时"
            if task.seed_ratio and torrent_info.get("ratio", 0) >= float(task.seed_ratio):
                return True, f"H&R 分享率达到 {task.seed_ratio}（当前 {torrent_info.get('ratio', 0):.2f}）"
            return False, "H&R 种子尚未满足删除条件"
        promotion_expired, promotion_reason = self.__promotion_expired(torrent_info, torrent_task)
        if promotion_expired:
            return True, promotion_reason
        if task.seed_time and torrent_info.get("seeding_time", 0) >= float(task.seed_time) * 3600:
            return True, f"做种时间达到 {task.seed_time} 小时"
        if task.seed_ratio and torrent_info.get("ratio", 0) >= float(task.seed_ratio):
            return True, f"分享率达到 {task.seed_ratio}（当前 {torrent_info.get('ratio', 0):.2f}）"
        if task.seed_size and torrent_info.get("uploaded", 0) >= float(task.seed_size) * 1024 ** 3:
            return True, f"上传量达到 {task.seed_size} GB"
        if (
            task.download_time
            and torrent_info.get("dltime", 0) >= float(task.download_time) * 3600
            and not self._is_completed(torrent_info)
        ):
            return True, f"下载耗时达到 {task.download_time} 小时"
        total_size = torrent_info.get("total_size", 0)
        amount_left = torrent_info.get("amount_left", 0)
        if (
            task.seed_avgspeed
            and torrent_info.get("avg_upspeed", 0) <= float(task.seed_avgspeed) * 1024
            and (total_size > 0 and amount_left < total_size * 0.5)
        ):
            return True, f"上传速度低于 {task.seed_avgspeed} KB/s"
        if task.seed_inactivetime and torrent_info.get("iatime", 0) >= float(task.seed_inactivetime) * 60:
            return True, f"未活动时间达到 {task.seed_inactivetime} 分钟"
        return False, "尚未满足删除条件"

    def __promotion_expired(self, torrent_info: dict, torrent_task: dict) -> Tuple[bool, str]:
        """判断免费促销是否结束且种子仍未完成下载"""
        task = self._get_task_config()
        is_completed = self._is_completed(torrent_info)
        if (
            not task
            or not task.del_no_free
            or is_completed
        ):
            return False, ""
        expiry = self._promotion_expiry_at(torrent_task.get("freedate"), task.timezone_offset)
        if not expiry:
            return False, ""
        expired = datetime.now(expiry.tzinfo) >= expiry
        return expired, "促销已过期" if expired else ""

    def __delete_torrent_for_evaluate_conditions(
        self,
        torrents: List[Any],
        torrent_tasks: Dict[str, dict],
        dynamic: bool = False,
    ) -> List[str]:
        """找出满足用户删除条件的种子并发送对应通知"""
        delete_hashes: List[str] = []
        now_timestamp = int(time.time())
        for torrent in torrents:
            torrent_hash = self.__get_hash(torrent)
            torrent_task = torrent_tasks.get(torrent_hash)
            if not torrent_task:
                continue
            torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
            # 使用 QB 实时上传速度
            torrent_info["avg_upspeed"] = torrent_info.get("upspeed", 0)
            should_delete, reason = self.__evaluate_conditions_for_delete(torrent_info, torrent_task)
            if not should_delete:
                continue
            delete_hashes.append(torrent_hash)
            if dynamic:
                reason = f"触发动态删除阈值，{reason}"
            self.__send_delete_message(torrent_task, reason)
            logger.info(f"刷流任务删除种子：{torrent_task.get('title')}，原因：{reason}")
        return delete_hashes

    def __delete_torrent_for_evaluate_proxy_pre_conditions(
        self,
        torrents: List[Any],
        torrent_tasks: Dict[str, dict],
    ) -> List[str]:
        """动态删种前优先清理促销过期或下载超时的非 H&R 种子"""
        task = self._get_task_config()
        delete_hashes: List[str] = []
        now_timestamp = int(time.time())
        for torrent in torrents:
            torrent_hash = self.__get_hash(torrent)
            torrent_task = torrent_tasks.get(torrent_hash)
            if not task or not torrent_task or torrent_task.get("hit_and_run"):
                continue
            torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
            expired, reason = self.__promotion_expired(torrent_info, torrent_task)
            timed_out = bool(
                task.download_time
                and torrent_info.get("dltime", 0) >= float(task.download_time) * 3600
                and not self._is_completed(torrent_info)
            )
            if not expired and not timed_out:
                continue
            if timed_out and not reason:
                reason = f"下载耗时达到 {task.download_time} 小时"
            delete_hashes.append(torrent_hash)
            self.__send_delete_message(torrent_task, reason)
        return delete_hashes

    @staticmethod
    def _select_global_dynamic_deletions(
        candidates: List[dict],
        total_size: float,
        min_size: float,
        max_size: float,
    ) -> Tuple[List[dict], float, bool]:
        """按 V4 优先级从跨任务候选中生成全局动态删种计划"""
        selected: List[dict] = []
        selected_keys: Set[Tuple[str, str]] = set()
        remaining_size = total_size

        def select(
            candidate: dict,
            reason: str,
            reason_field: Optional[str] = None,
            dynamic_reason: bool = False,
        ) -> None:
            """把未选候选加入计划并扣减预计做种体积"""
            nonlocal remaining_size
            candidate_key = (
                candidate["downloader_name"],
                candidate["torrent_hash"],
            )
            if candidate_key in selected_keys:
                return
            selected_keys.add(candidate_key)
            task_delete_reasons: Dict[str, str] = {}
            for task, _ in candidate.get(
                "associated_records",
                [(candidate["task"], candidate.get("torrent_task"))],
            ):
                task_reason = (
                    candidate.get("task_condition_reasons", {}).get(task.id, {}).get(reason_field)
                    if reason_field
                    else None
                ) or reason
                if dynamic_reason:
                    task_reason = f"触发全局动态删除阈值，{task_reason}"
                task_delete_reasons[task.id] = task_reason
            selected.append(
                {
                    **candidate,
                    "delete_reason": reason,
                    "task_delete_reasons": task_delete_reasons,
                }
            )
            remaining_size = max(remaining_size - float(candidate.get("size") or 0), 0)

        for candidate in candidates:
            if candidate.get("pre_delete_reason"):
                select(
                    candidate,
                    candidate["pre_delete_reason"],
                    reason_field="pre_delete_reason",
                )

        threshold_triggered = remaining_size >= max_size
        if not threshold_triggered:
            return selected, remaining_size, False

        for candidate in candidates:
            if remaining_size <= min_size:
                break
            if not candidate.get("proxy_delete") and candidate.get("conditional_reason"):
                select(
                    candidate,
                    candidate["conditional_reason"],
                    reason_field="conditional_reason",
                )

        if remaining_size > min_size:
            for candidate in candidates:
                if remaining_size <= min_size:
                    break
                if candidate.get("proxy_delete") and candidate.get("conditional_reason"):
                    select(
                        candidate,
                        f"触发全局动态删除阈值，{candidate['conditional_reason']}",
                        reason_field="conditional_reason",
                        dynamic_reason=True,
                    )

        fallback_candidates = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.get("proxy_delete")
                and candidate.get("completed")
                and not candidate.get("hit_and_run")
            ),
            key=lambda item: item.get("seeding_time", 0),
            reverse=True,
        )
        for candidate in fallback_candidates:
            if remaining_size <= min_size:
                break
            select(candidate, "触发全局动态删除阈值，系统按做种时间清理")

        return selected, remaining_size, True

    def _collect_global_dynamic_delete_candidates(
        self,
    ) -> Tuple[List[dict], float, Dict[str, Dict[str, dict]], Dict[str, ServiceInfo]]:
        """汇总启用任务的最新下载器状态、做种体积和全局删种候选（单遍扫描）"""
        candidate_rows: Dict[Tuple[str, str], List[dict]] = {}
        total_size = 0.0
        task_records: Dict[str, Dict[str, dict]] = {}
        services: Dict[str, ServiceInfo] = {}
        downloader_cache: Dict[str, Tuple[ServiceInfo, List[Any]]] = {}
        counted_torrents: Set[Tuple[str, str]] = set()
        associated_records: Dict[Tuple[str, str], List[Tuple[BrushTaskConfig, dict]]] = {}
        downloader_helper = DownloaderHelper()

        # 单遍扫描：先构建 task_records 和 associated_records，再处理启用任务
        for task in self._task_configs.values():
            torrent_tasks: Dict[str, dict] = self._get_task_data(task.id, "torrents") or {}
            task_records[task.id] = torrent_tasks
            for torrent_hash, torrent_task in torrent_tasks.items():
                if torrent_task.get("deleted"):
                    continue
                torrent_key = (task.downloader, torrent_hash)
                associated_records.setdefault(torrent_key, []).append((task, torrent_task))

        for task in self._task_configs.values():
            if not task.enabled:
                continue
            torrent_tasks = task_records[task.id]
            if task.downloader not in downloader_cache:
                service = downloader_helper.get_service(name=task.downloader)
                if not service or not service.instance or service.instance.is_inactive():
                    raise RuntimeError(
                        f"全局动态删种无法获取下载器 [{task.downloader}] 实时状态，本轮已中止"
                    )
                torrents, error = service.instance.get_torrents()
                if error:
                    raise RuntimeError(
                        f"全局动态删种获取下载器 [{task.downloader}] 种子失败，本轮已中止"
                    )
                downloader_cache[task.downloader] = (service, torrents or [])
                services[task.downloader] = service

            service, downloader_torrents = downloader_cache[task.downloader]
            with self._task_scope(task.id):
                downloader_torrent_map: Dict[str, Any] = {}
                for torrent in downloader_torrents:
                    torrent_hash = self.__get_hash(torrent)
                    if torrent_hash:
                        downloader_torrent_map[torrent_hash] = torrent
                check_hashes = list(torrent_tasks)
                check_torrents = [
                    downloader_torrent_map[torrent_hash]
                    for torrent_hash in check_hashes
                    if torrent_hash in downloader_torrent_map
                ]
                self.__update_seeding_tasks_state(check_torrents, torrent_tasks)
                self.__update_undeleted_torrents_missing_in_downloader(
                    torrent_tasks,
                    check_hashes,
                    downloader_torrents,
                )
                self._save_task_data(task.id, "torrents", torrent_tasks)
                now_timestamp = int(time.time())
                for torrent in check_torrents:
                    torrent_hash = self.__get_hash(torrent)
                    torrent_task = torrent_tasks.get(torrent_hash)
                    if not torrent_task or torrent_task.get("deleted"):
                        continue
                    torrent_key = (task.downloader, torrent_hash)
                    if torrent_key not in counted_torrents:
                        torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
                        total_size += float(
                            torrent_info.get("total_size") or torrent_task.get("size") or 0
                        )
                        counted_torrents.add(torrent_key)

                filtered_torrents = self.__filter_torrents_by_tag(check_torrents, task.delete_except_tags)
                for torrent in filtered_torrents:
                    torrent_hash = self.__get_hash(torrent)
                    torrent_task = torrent_tasks.get(torrent_hash)
                    if not torrent_task or torrent_task.get("deleted"):
                        continue
                    torrent_key = (task.downloader, torrent_hash)
                    torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
                    pre_delete_reason = ""
                    if not torrent_task.get("hit_and_run"):
                        expired, expired_reason = self.__promotion_expired(torrent_info, torrent_task)
                        timed_out = bool(
                            task.download_time
                            and torrent_info.get("dltime", 0) >= float(task.download_time) * 3600
                            and not self._is_completed(torrent_info)
                        )
                        if expired:
                            pre_delete_reason = expired_reason
                        elif timed_out:
                            pre_delete_reason = f"下载耗时达到 {task.download_time} 小时"
                    should_delete, conditional_reason = self.__evaluate_conditions_for_delete(
                        torrent_info,
                        torrent_task,
                    )
                    torrent_size = float(torrent_info.get("total_size") or torrent_task.get("size") or 0)
                    candidate_rows.setdefault(torrent_key, []).append(
                        {
                            "task": task,
                            "torrent_hash": torrent_hash,
                            "torrent_task": torrent_task,
                            "downloader_name": task.downloader,
                            "size": torrent_size,
                            "pre_delete_reason": pre_delete_reason,
                            "conditional_reason": conditional_reason if should_delete else "",
                            "proxy_delete": task.proxy_delete,
                            "completed": bool(
                                torrent_size > 0
                                and self._is_completed(torrent_info)
                            ),
                            "hit_and_run": bool(torrent_task.get("hit_and_run")),
                            "seeding_time": torrent_info.get("seeding_time", 0),
                        }
                    )

        candidates: List[dict] = []
        for torrent_key, rows in candidate_rows.items():
            associations = associated_records.get(torrent_key, [])
            if len(rows) != len(associations):
                continue
            candidate = dict(rows[0])
            candidate.update(
                {
                    "associated_records": associations,
                    "proxy_delete": all(row["proxy_delete"] for row in rows),
                    "completed": all(row["completed"] for row in rows),
                    "hit_and_run": any(row["hit_and_run"] for row in rows),
                    "pre_delete_reason": (
                        rows[0]["pre_delete_reason"]
                        if all(row["pre_delete_reason"] for row in rows)
                        else ""
                    ),
                    "conditional_reason": (
                        rows[0]["conditional_reason"]
                        if all(row["conditional_reason"] for row in rows)
                        else ""
                    ),
                    "seeding_time": max(row["seeding_time"] for row in rows),
                    "task_condition_reasons": {
                        row["task"].id: {
                            "pre_delete_reason": row["pre_delete_reason"],
                            "conditional_reason": row["conditional_reason"],
                        }
                        for row in rows
                    },
                }
            )
            candidates.append(candidate)
        return candidates, total_size, task_records, services

    def _send_global_dynamic_delete_summary(
        self,
        deleted_entries: List[dict],
        remaining_size: float,
    ) -> None:
        """按受影响任务通知开关发送全局区间删种汇总"""
        notified_tasks = {
            task.id: task
            for entry in deleted_entries
            for task, _ in entry.get(
                "associated_records",
                [(entry["task"], entry.get("torrent_task"))],
            )
            if task.notify
        }
        if not notified_tasks:
            return
        task_names = "、".join(task.name for task in notified_tasks.values())
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="【刷流任务全局动态删除】",
            text=(
                f"任务：{task_names}\n"
                f"删除：{len(deleted_entries)} 个种子\n"
                f"当前做种：{self.__bytes_to_gb(remaining_size):.1f} GB"
            ),
        )

    def _run_global_dynamic_delete(self) -> int:
        """串行执行跨任务、跨下载器的全局动态删种并返回成功删除数"""
        if not self._global_dynamic_delete_enabled():
            return 0
        if not self._global_dynamic_delete_should_trigger():
            return 0
        global_lock = getattr(self, "_global_delete_lock", None)
        if global_lock is None:
            self._global_delete_lock = threading.Lock()
            global_lock = self._global_delete_lock
        if not global_lock.acquire(blocking=False):
            logger.info("已有全局动态删种正在执行，本轮跳过")
            return 0

        try:
            with self._all_task_locks_scope():
                candidates, total_size, task_records, services = self._collect_global_dynamic_delete_candidates()
                size_range = getattr(self, "_global_delete_size_range", None)
                if size_range:
                    limits = [
                        float(value) * 1024 ** 3
                        for value in str(size_range).split("-")
                    ]
                    min_size = limits[0]
                    max_size = limits[1] if len(limits) > 1 else limits[0]
                else:
                    # 未设置体积阈值，仅使用剩余空间触发，设置 max_size=0 使体积检查始终通过
                    min_size = 0
                    max_size = 0
                delete_plan, _, threshold_triggered = self._select_global_dynamic_deletions(
                    candidates,
                    total_size,
                    min_size,
                    max_size,
                )
                if not delete_plan:
                    if threshold_triggered:
                        logger.info(
                            f"全局做种体积 {self.__bytes_to_gb(total_size):.1f} GB 已达到动态删种上限，"
                            "但没有符合任务策略的可删除种子"
                        )
                    else:
                        logger.info(
                            f"全局做种体积 {self.__bytes_to_gb(total_size):.1f} GB，"
                            f"未达到动态删种上限 {self.__bytes_to_gb(max_size):.1f} GB"
                        )
                    return 0

                plan_by_downloader: Dict[str, List[dict]] = {}
                for entry in delete_plan:
                    plan_by_downloader.setdefault(entry["downloader_name"], []).append(entry)

                deleted_entries: List[dict] = []
                downloader_helper = DownloaderHelper()
                for downloader_name, entries in plan_by_downloader.items():
                    service = services.get(downloader_name)
                    if not service or not service.instance:
                        continue
                    torrent_hashes = list(dict.fromkeys(entry["torrent_hash"] for entry in entries))
                    if downloader_helper.is_downloader("qbittorrent", service=service):
                        try:
                            if getattr(service.instance, "qbc", None):
                                service.instance.qbc.torrents_reannounce(torrent_hashes=torrent_hashes)
                        except Exception as err:
                            logger.warning(f"全局动态删种重新汇报下载器 [{downloader_name}] 失败：{str(err)}")
                    try:
                        if service.instance.delete_torrents(ids=torrent_hashes, delete_file=True):
                            deleted_entries.extend(entries)
                    except Exception as err:
                        logger.error(
                            f"全局动态删种调用下载器 [{downloader_name}] 删除失败：{str(err)}"
                        )

                deleted_at = time.time()
                affected_task_ids: Set[str] = set()
                recorded_entries: List[dict] = []
                notification_entries: List[Tuple[BrushTaskConfig, dict, str]] = []
                for entry in deleted_entries:
                    torrent_hash = entry["torrent_hash"]
                    entry_recorded = False
                    for task, _ in entry.get(
                        "associated_records",
                        [(entry["task"], entry.get("torrent_task"))],
                    ):
                        torrent_task = task_records.get(task.id, {}).get(torrent_hash)
                        if not torrent_task:
                            continue
                        delete_reason = entry.get("task_delete_reasons", {}).get(
                            task.id,
                            entry["delete_reason"],
                        )
                        torrent_task.update({"deleted": True, "deleted_time": deleted_at})
                        affected_task_ids.add(task.id)
                        notification_entries.append((task, torrent_task, delete_reason))
                        entry_recorded = True
                    if entry_recorded:
                        recorded_entries.append(entry)

                for affected_task_id in affected_task_ids:
                    self._save_task_data(
                        affected_task_id,
                        "torrents",
                        task_records[affected_task_id],
                    )
                    self._recalculate_statistics(affected_task_id)

                for task, torrent_task, delete_reason in notification_entries:
                    try:
                        with self._task_scope(task.id):
                            self.__send_delete_message(torrent_task, delete_reason)
                    except Exception as err:
                        logger.warning(f"全局动态删种发送任务 [{task.name}] 通知失败：{str(err)}")
                    logger.info(
                        f"全局动态删种删除任务 [{task.name}] 种子："
                        f"{torrent_task.get('title')}，原因：{delete_reason}"
                    )

                remaining_size = max(
                    total_size - sum(float(entry.get("size") or 0) for entry in deleted_entries),
                    0,
                )
                if threshold_triggered and len(limits) > 1 and recorded_entries:
                    try:
                        self._send_global_dynamic_delete_summary(recorded_entries, remaining_size)
                    except Exception as err:
                        logger.warning(f"全局动态删种发送汇总通知失败：{str(err)}")
                return len(recorded_entries)
        finally:
            global_lock.release()

    def __delete_torrent_for_proxy(
        self,
        torrents: List[Any],
        torrent_tasks: Dict[str, dict],
    ) -> List[str]:
        """按动态体积阈值执行前置、条件和兜底删种"""
        task = self._get_task_config()
        if not task or not task.proxy_delete or not task.delete_size_range:
            return []
        torrent_info_map = {
            self.__get_hash(torrent): self.__get_torrent_info(torrent) for torrent in torrents
        }
        total_size = self.__calculate_seeding_torrents_size(torrent_tasks)
        pre_delete_hashes = self.__delete_torrent_for_evaluate_proxy_pre_conditions(torrents, torrent_tasks)
        total_size -= sum(torrent_info_map[item].get("total_size", 0) for item in pre_delete_hashes if item in torrent_info_map)
        remaining_torrents = [torrent for torrent in torrents if self.__get_hash(torrent) not in pre_delete_hashes]
        limits = [float(value) * 1024 ** 3 for value in task.delete_size_range.split("-")]
        min_size = limits[0]
        max_size = limits[1] if len(limits) > 1 else limits[0]
        if total_size < max_size:
            return pre_delete_hashes
        delete_hashes = list(pre_delete_hashes)
        conditional_hashes = self.__delete_torrent_for_evaluate_conditions(
            remaining_torrents, torrent_tasks, dynamic=True
        )
        delete_hashes.extend(conditional_hashes)
        total_size -= sum(
            torrent_info_map[item].get("total_size", 0)
            for item in conditional_hashes
            if item in torrent_info_map
        )
        if total_size > min_size:
            remaining_hashes = [
                self.__get_hash(torrent)
                for torrent in remaining_torrents
                if self.__get_hash(torrent) not in delete_hashes
            ]
            completed = self.downloader.get_completed_torrents(ids=remaining_hashes)
            candidates = []
            for torrent in completed:
                torrent_hash = self.__get_hash(torrent)
                if torrent_tasks.get(torrent_hash, {}).get("hit_and_run"):
                    continue
                info = torrent_info_map.get(torrent_hash) or self.__get_torrent_info(torrent)
                candidates.append((torrent_hash, info))
            candidates.sort(key=lambda item: item[1].get("seeding_time", 0), reverse=True)
            for torrent_hash, torrent_info in candidates:
                if total_size <= min_size:
                    break
                delete_hashes.append(torrent_hash)
                total_size -= torrent_info.get("total_size", 0)
                torrent_task = torrent_tasks.get(torrent_hash, {})
                self.__send_delete_message(torrent_task, "触发动态删除阈值，系统按做种时间清理")
        if len(limits) > 1 and delete_hashes:
            self.__send_message(
                "【刷流任务动态删除】",
                f"任务：{task.name}\n删除：{len(delete_hashes)} 个种子\n当前做种：{self.__bytes_to_gb(total_size):.1f} GB",
            )
        return delete_hashes

    def __update_undeleted_torrents_missing_in_downloader(
        self,
        torrent_tasks: Dict[str, dict],
        torrent_check_hashes: List[str],
        torrents: List[Any],
    ) -> None:
        """把下载器中已不存在但仍标记正常的记录更新为已删除"""
        existing_hashes = set(self.__get_all_hashes(torrents))
        missing_hashes = [
            item for item in torrent_check_hashes
            if item not in existing_hashes and not torrent_tasks[item].get("deleted")
        ]
        deleted_tasks: List[dict] = []
        for torrent_hash in missing_hashes:
            torrent_task = torrent_tasks[torrent_hash]
            torrent_task.update({"deleted": True, "deleted_time": time.time()})
            deleted_tasks.append(torrent_task)
        if deleted_tasks:
            self.__log_and_send_torrent_task_update_message(
                "【刷流任务状态更新】", "更新为已删除", "下载器中找不到对应种子", deleted_tasks
            )

    def __convert_torrent_info_to_task(self, torrent: Any) -> dict:
        """把下载器种子转换为当前任务的托管记录"""
        torrent_info = self.__get_torrent_info(torrent)
        site_id, site_name = self.__get_site_by_torrent(torrent)
        task = self._get_task_config()
        return {
            "task_id": task.id if task else None,
            "task_name": task.name if task else None,
            "site": site_id,
            "site_name": site_name,
            "title": torrent_info.get("title", ""),
            "size": torrent_info.get("total_size", 0),
            "pubdate": None,
            "description": None,
            "imdbid": None,
            "page_url": None,
            "date_elapsed": None,
            "freedate": None,
            "uploadvolumefactor": None,
            "downloadvolumefactor": None,
            "hit_and_run": False,
            "volume_factor": None,
            "freedate_diff": None,
            "ratio": torrent_info.get("ratio", 0),
            "downloaded": torrent_info.get("downloaded", 0),
            "uploaded": torrent_info.get("uploaded", 0),
            "seeding_time": torrent_info.get("seeding_time", 0),
            "deleted": False,
            "time": torrent_info.get("add_on", time.time()),
        }

    @staticmethod
    def __get_redict_url(
        url: str,
        proxies: str = None,
        ua: str = None,
        cookie: str = None,
    ) -> Optional[str]:
        """解析带请求参数的跳转下载链接并返回真实地址"""
        match = re.search(r"\[(.*)](.*)", url)
        if not match:
            return None
        base64_str, request_url = match.group(1), match.group(2)
        if not base64_str:
            return request_url
        try:
            request_text = base64.b64decode(base64_str.encode("utf-8")).decode("utf-8")
            request_params: Dict[str, dict] = json.loads(request_text)
        except (ValueError, UnicodeDecodeError) as err:
            logger.error(f"解析种子跳转下载参数失败：{str(err)}")
            return None
        if not request_params.get("cookie"):
            cookie = None
        headers = request_params.get("header") or None
        request = RequestUtils(ua=ua, proxies=proxies, cookies=cookie, headers=headers)
        if request_params.get("method") == "get":
            response = request.get_res(request_url, params=request_params.get("params"))
        else:
            response = request.post_res(request_url, params=request_params.get("params"))
        if not response:
            return None
        result_path = request_params.get("result")
        if not result_path:
            return response.text
        data = response.json()
        success_key = request_params.get("success")
        if success_key and not data.get(success_key):
            return None
        for key in str(result_path).split("."):
            if not isinstance(data, dict):
                return None
            data = data.get(key)
            if data is None:
                return None
        result_url_path = request_params.get("result_path")
        result_query_param = request_params.get("result_query_param")
        if result_url_path and result_query_param:
            result_url = urljoin(
                f"{str(request_params.get('result_base_url')).rstrip('/')}/",
                str(result_url_path).lstrip("/"),
            )
            return f"{result_url}?{urlencode({result_query_param: data})}"
        return str(data)

    @staticmethod
    def __reset_download_url(torrent_url: str, site_id: int) -> str:
        """为支持的 NexusPHP 站点追加跳过下载提示参数"""
        try:
            if not torrent_url or torrent_url.startswith("magnet"):
                return torrent_url
            site = next(
                (item for item in SitesHelper().get_indexers() if item.get("id") == site_id),
                None,
            )
            if not site or site.get("name") in {"天空"} or not site.get("schema", "").startswith("Nexus"):
                return torrent_url
            parsed_url = urlparse(torrent_url)
            query_params = dict(parse_qsl(parsed_url.query))
            query_params["letdown"] = "1"
            return str(urlunparse(parsed_url._replace(query=urlencode(query_params))))
        except Exception as err:
            logger.error(f"处理种子下载提示地址失败：{str(err)}")
            return torrent_url

    def __download(self, torrent: TorrentInfo) -> Optional[str]:
        """按当前任务配置向 qBittorrent 或 Transmission 添加种子"""
        try:
            task = self._get_task_config()
            if not task or not torrent.enclosure:
                logger.error(f"获取种子下载链接失败：{torrent.title}")
                return None
            up_speed = int(task.up_speed) if task.up_speed else None
            down_speed = int(task.dl_speed) if task.dl_speed else None
            torrent_content: Union[str, bytes] = torrent.enclosure
            proxies = settings.PROXY if torrent.site_proxy else None
            cookies = torrent.site_cookie
            if isinstance(torrent_content, str) and torrent_content.startswith("["):
                torrent_content = self.__get_redict_url(
                    torrent_content,
                    proxies=proxies,
                    ua=torrent.site_ua,
                    cookie=cookies,
                )
                cookies = None
            if not torrent_content:
                return None
            if task.site_skip_tips and isinstance(torrent_content, str):
                torrent_content = self.__reset_download_url(torrent_content, torrent.site)
            downloader = self.downloader
            service = self.service_info
            if not downloader or not service:
                return None
            downloader_helper = DownloaderHelper()
            if downloader_helper.is_downloader("qbittorrent", service=service):
                up_limit = int(up_speed * 1024 * 1024) if up_speed else None
                down_limit = int(down_speed * 1024 * 1024) if down_speed else None
                random_tag = StringUtils.generate_random_str(10)
                # 判断是否为 HR 种子
                is_hr = bool(torrent.hit_and_run or task.site_hr_active)
                if isinstance(torrent_content, str) and not torrent_content.startswith("magnet"):
                    response = RequestUtils(cookies=cookies, proxies=proxies, ua=torrent.site_ua).get_res(
                        url=torrent_content
                    )
                    if response and response.ok:
                        torrent_content = response.content
                if not downloader.add_torrent(
                    content=torrent_content,
                    download_dir=task.save_path,
                    cookie=cookies,
                    category=task.qb_category,
                    tag=["已整理", self.GLOBAL_BRUSH_TAG, task.brush_tag, random_tag, "HR"] if is_hr else ["已整理", self.GLOBAL_BRUSH_TAG, task.brush_tag, random_tag],
                    upload_limit=up_limit,
                    download_limit=down_limit,
                ):
                    return None
                torrent_hash = downloader.get_torrent_id_by_tag(tags=random_tag)
                if not torrent_hash:
                    logger.error(f"刷流任务 [{task.name}] 获取种子 Hash 失败")
                return torrent_hash
            if downloader_helper.is_downloader("transmission", service=service):
                if isinstance(torrent_content, str) and not torrent_content.startswith("magnet"):
                    response = RequestUtils(cookies=cookies, proxies=proxies, ua=torrent.site_ua).get_res(
                        url=torrent_content
                    )
                    if response and response.ok:
                        torrent_content = response.content
                # 判断是否为 HR 种子
                is_hr = bool(torrent.hit_and_run or task.site_hr_active)
                added_torrent = downloader.add_torrent(
                    content=torrent_content,
                    download_dir=task.save_path,
                    cookie=cookies,
                    labels=["已整理", self.GLOBAL_BRUSH_TAG, task.brush_tag, "HR"] if is_hr else ["已整理", self.GLOBAL_BRUSH_TAG, task.brush_tag],
                )
                if not added_torrent:
                    return None
                if task.up_speed or task.dl_speed:
                    tr_up_limit = int(up_speed * 1024 * 1024) if up_speed else None
                    tr_down_limit = int(down_speed * 1024 * 1024) if down_speed else None
                    downloader.change_torrent(
                        hash_string=added_torrent.hashString,
                        upload_limit=tr_up_limit,
                        download_limit=tr_down_limit,
                    )
                return added_torrent.hashString
            return None
        except Exception as err:
            logger.error(f"添加种子下载失败: {err}")
            return None

    def __qb_torrents_reannounce(self, torrent_hashes: List[str]) -> None:
        """删除 qBittorrent 种子前强制重新汇报 Tracker"""
        downloader = self.downloader
        if not downloader or not getattr(downloader, "qbc", None) or not torrent_hashes:
            return
        try:
            downloader.qbc.torrents_reannounce(torrent_hashes=torrent_hashes)
        except Exception as err:
            logger.error(f"强制重新汇报 Tracker 失败：{str(err)}")

    def __ttgl_get_site_cookie(self) -> Optional[Tuple[str, str]]:
        """从数据库获取 TTGL 站点的 Cookie 和 UA"""
        try:
            import psycopg2
            from app.core.config import settings
            conn = psycopg2.connect(
                host=settings.DB_POSTGRESQL_HOST,
                port=settings.DB_POSTGRESQL_PORT,
                user=settings.DB_POSTGRESQL_USERNAME,
                password=settings.DB_POSTGRESQL_PASSWORD,
                database=settings.DB_POSTGRESQL_DATABASE,
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT cookie, ua FROM site WHERE domain = %s LIMIT 1",
                ("totheglory.im",)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0]:
                return row[0], row[1]
        except Exception as err:
            logger.error(f"获取TTGL站点Cookie失败: {err}")
        return None

    def __ttgl_get_site_username(self) -> Optional[str]:
        """从 siteuserdata 表获取站点最近一次抓取到的用户名（用于商城页登录态字节匹配）"""
        try:
            import psycopg2
            from app.core.config import settings
            conn = psycopg2.connect(
                host=settings.DB_POSTGRESQL_HOST,
                port=settings.DB_POSTGRESQL_PORT,
                user=settings.DB_POSTGRESQL_USERNAME,
                password=settings.DB_POSTGRESQL_PASSWORD,
                database=settings.DB_POSTGRESQL_DATABASE,
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT username FROM siteuserdata WHERE domain = %s AND username IS NOT NULL ORDER BY id DESC LIMIT 1",
                ("totheglory.im",)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0]:
                return row[0]
        except Exception as err:
            logger.error(f"获取TTGL站点用户名失败: {err}")
        return None

    def __ttgl_build_session(self, cookie: str, ua: str) -> requests.Session:
        """构建带已建立会话的 TTGL 站点请求 Session（cf_clearance 绑定容器 IP，必须先 GET 首页）"""
        s = requests.Session()
        s.headers.update({'User-Agent': ua})
        for c in cookie.split(';'):
            c = c.strip()
            if '=' in c:
                k, v = c.split('=', 1)
                s.cookies.set(k, v)
        s.get('https://totheglory.im/', timeout=30)
        return s

    # v5.5.6: TTGL 折扣档位映射（mid 已在 mall.php?cid=3 购买表单 hidden input 实证核实）
    # tier: (道具mid, 商城图标, 展示名, 价格积分, 生效说明文案)
    TTGL_TIER_MAP = {
        "30": ("2", "30ico.png", "30%下载", "5000", "下载量仅计算30%"),
        "50": ("1", "50ico.png", "50%下载", "2000", "下载量仅计算50%"),
        "fl": ("3", "freeico.png", "免费FL", "10000", "下载量计为免费"),
    }

    def __ttgl_tier_info(self, tier: str) -> Tuple[str, str, str, str, str]:
        """取档位映射，未知档位回退 30（与 __init__ 默认一致）"""
        return self.TTGL_TIER_MAP.get(tier, self.TTGL_TIER_MAP["30"])

    def __ttgl_get_unused_discount_count(self, cookie: str, ua: str, tier: str) -> Optional[int]:
        """检查TTGL积分商店中指定档位道具的未使用数量

        返回 None 表示无法验证（cookie失效/未登录/网络异常），调用方禁止据此购买。
        商城页为 latin-1 编码，v5.5.6 按档位图标（30ico/50ico/freeico.png）逐行匹配，
        比 v5.5.4 硬编码 '30%' 标签更通用（50%/FL 档位共用同一计数逻辑）。"""
        try:
            s = self.__ttgl_build_session(cookie, ua)
            resp = s.get('https://totheglory.im/mall.php?my=1', timeout=30)
            raw = resp.content
            # 动态用户名做登录态校验（从 siteuserdata.username 提取，避免硬编码）
            username = self.__ttgl_get_site_username()
            if not username:
                logger.error("TTGL无法获取站点用户名（siteuserdata.username为空），无法验证库存")
                return None
            if username.encode('latin-1', 'replace') not in raw:
                logger.error("TTGL商城页未检出登录态（cookie可能已失效），无法验证库存")
                return None
            _, icon, _, _, _ = self.__ttgl_tier_info(tier)
            # 库存行形如 <img src="/pic/tools/30ico.png" ...> ... <td>未使用</td><td> 1</td>
            # 状态文案：未使用=\xe6\x9c\xaa\xe4\xbd\xbf\xe7\x94\xa8，已使用=\xe5\xb7\xb2\xe4\xbd\xbf\xe7\x94\xa8
            # 先定位所有档位（30ico/50ico/freeico）工具图位置作为行边界，再取本档位行，
            # 窗口截止到下一个任意档位工具图，避免跨档位行状态误配对
            any_icon_pat = re.compile(rb'/pic/tools/\w+ico\.png')
            all_positions = [m.start() for m in any_icon_pat.finditer(raw)]
            status_pat = re.compile(rb'(\xe6\x9c\xaa|\xe5\xb7\xb2)\xe4\xbd\xbf\xe7\x94\xa8')
            unused = 0
            for i, p in enumerate(all_positions):
                row = raw[p:p + 40]
                if not row.startswith(('/pic/tools/' + icon).encode('utf-8')):
                    continue
                end = all_positions[i + 1] if i + 1 < len(all_positions) else len(raw)
                m = status_pat.search(raw, p, end)
                if m and m.group(1) == b'\xe6\x9c\xaa':
                    unused += 1
            return unused
        except Exception as err:
            logger.error(f"检查TTGL未使用折扣失败: {err}")
        return None

    def __ttgl_torrent_discount_used(self, s: requests.Session, torrent_id: str) -> bool:
        """查询种子详情页，判断该种子是否已使用过折扣道具

        种子详情页是 UTF-8（仅商城页为 latin-1），工具区使用过后显示
        绿色标记『您已对本种子使用过道具：<30ico/50ico>』，故直接按 UTF-8 字节判断。
        """
        try:
            resp = s.get(f'https://totheglory.im/t/{torrent_id}/', timeout=30)
            raw = resp.content
            has_tool_img = (b'30ico.png' in raw) or (b'50ico.png' in raw) or (b'freeico.png' in raw)
            # 站点固定文案"您已对本种子使用过道具："，用完整短语避免"未使用过道具"类否定语境假阳性
            used_marker = '您已对本种子使用过道具'.encode('utf-8')
            return has_tool_img and (used_marker in raw)
        except Exception as err:
            logger.error(f"查询TTGL种子{torrent_id}折扣使用状态失败: {err}")
            return False

    def __ttgl_purchase_discount(self, cookie: str, ua: str, tier: str) -> bool:
        """购买TTGL指定档位折扣（以商城未使用数量增加为成功判定）"""
        try:
            mid, _, label, price, _ = self.__ttgl_tier_info(tier)
            s = self.__ttgl_build_session(cookie, ua)
            before = self.__ttgl_get_unused_discount_count(cookie, ua, tier)
            if before is None:
                logger.error("TTGL库存检查失败（未登录/网络异常），取消购买以避免浪费积分")
                return False
            # POST购买
            resp = s.post(
                'https://totheglory.im/mall.php?action=exchange',
                data={'mid': mid, 'quantity': '1', 'submit': '消费！'},
                allow_redirects=False,
                timeout=30,
            )
            if resp.status_code == 302:
                after = self.__ttgl_get_unused_discount_count(cookie, ua, tier)
                if after is None:
                    # 购买前检查成功但复查失败：不报成功，提示人工核对（可能已扣积分）
                    logger.error("TTGL购买后复查库存失败，请手动核对商城是否已扣积分")
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【刷流】TTGL购买折扣未能验证",
                        text=f"购买请求已发出但库存复查失败，请手动到商城确认是否已扣{price}积分",
                    )
                    return False
                if after > before:
                    logger.info(f"TTGL购买{label}折扣成功（未使用数量 {before} → {after}）")
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title=f"【刷流】TTGL购买{label}折扣成功",
                        text=f"已消费{price}积分购买{label}1个，48小时内有效",
                    )
                    return True
                logger.error(f"TTGL购买折扣未生效: status=302 但未使用数量 {before} → {after}（可能积分不足或库存限制）")
                return False
            logger.error(f"TTGL购买折扣失败: status={resp.status_code}")
        except Exception as err:
            logger.error(f"TTGL购买折扣异常: {err}")
        return False

    def __ttgl_use_discount(self, cookie: str, ua: str, torrent_id: str, tier: str) -> bool:
        """在种子详情页使用指定档位折扣（以商城未使用数量下降为成功判定）"""
        try:
            mid, _, label, _, effect_text = self.__ttgl_tier_info(tier)
            s = self.__ttgl_build_session(cookie, ua)
            # 种子已绑定过道具则跳过（避免重复使用误报成功）
            if self.__ttgl_torrent_discount_used(s, torrent_id):
                logger.info(f"TTGL种子 {torrent_id} 已使用过折扣道具，跳过")
                return True
            before = self.__ttgl_get_unused_discount_count(cookie, ua, tier)
            if before is None:
                logger.error(f"TTGL种子 {torrent_id} 库存检查失败（cookie失效/网络异常），跳过使用")
                return False
            # POST使用折扣（NexusPHP 成功时 302 回跳种子页；失败也常返回 200/302，需商城二次验证）
            # v5.5.6 修复：种子页「对本种子使用道具」按钮(.torrent_tool)用的是 c=torrents；
            # c=nocheck 是另一个处理器(a.nocheck)，POST 它会回 200+"只能对自己使用"，道具不绑定。
            # 833095 生产事故（2026-09-11 14:08）实证：c=nocheck 库存 1→1 未生效。
            resp = s.post(
                'https://totheglory.im/usemall.php',
                data={'c': 'torrents', 'tid': torrent_id, 'mid': mid},
                allow_redirects=False,
                timeout=30,
            )
            if resp.status_code not in (200, 302):
                logger.error(f"TTGL使用折扣失败: status={resp.status_code}")
                return False
            # 二次验证：商城未使用数量下降，或种子详情页出现已使用标记
            time.sleep(2)
            if self.__ttgl_torrent_discount_used(s, torrent_id):
                logger.info(f"TTGL种子 {torrent_id} 使用{label}折扣成功（种子页已标记）")
                task = self._get_task_config()
                task_name = task.name if task else "TTGL"
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=f"【刷流】{task_name} 已使用{label}折扣",
                    text=f"种子 {torrent_id} 已应用{label}，{effect_text}",
                )
                return True
            after = self.__ttgl_get_unused_discount_count(cookie, ua, tier)
            if after is None:
                logger.error(f"TTGL种子 {torrent_id} 使用后复查库存失败，无法验证折扣是否生效，请手动到种子页确认")
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=f"【刷流】TTGL折扣使用未能验证（种子 {torrent_id}）",
                    text="usemall 已调用但商城复查失败，请手动到种子详情页确认折扣是否已生效",
                )
                return False
            if before > 0 and after < before:
                logger.info(f"TTGL种子 {torrent_id} 使用{label}折扣成功（未使用数量 {before} → {after}）")
                task = self._get_task_config()
                task_name = task.name if task else "TTGL"
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=f"【刷流】{task_name} 已使用{label}折扣",
                    text=f"种子 {torrent_id} 已应用{label}，{effect_text}",
                )
                return True
            logger.error(f"TTGL使用折扣未生效: 种子 {torrent_id} 未使用数量 {before} → {after}，status={resp.status_code}（可能无未使用库存或种子不可用）")
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title=f"【刷流】TTGL折扣使用失败（种子 {torrent_id}）",
                text=f"usemall 调用后商城未使用数量未下降（{before} → {after}），请检查积分库存与种子状态",
            )
            return False
        except Exception as err:
            logger.error(f"TTGL使用折扣异常: {err}")
            return False

    def __ttgl_apply_discount(self, torrent: TorrentInfo, task: BrushTaskConfig) -> None:
        """TTGL积分商店按任务档位自动购买并使用折扣"""
        try:
            # 检查是否启用且种子体积满足条件
            if not task.ttgl_discount_enabled:
                return
            if task.ttgl_discount_min_size and torrent.size < task.ttgl_discount_min_size * 1024**3:
                return
            tier = getattr(task, "ttgl_discount_tier", "30") or "30"
            # v5.5.6: 仅对发布时间在24小时内的种子生效；发布时间未知（pubdate为空/解析失败
            # 返回0）视为无法确认，同样跳过，避免给老种子误烧积分
            # 时区处理与 pubtime 过滤一致：分钟数减去 task.timezone_offset
            pub_minutes = self.__get_pubminutes(getattr(torrent, "pubdate", "") or "") - (task.timezone_offset or 0) * 60
            if pub_minutes <= 0 or pub_minutes > 1440:
                logger.info(f"TTGL种子 {torrent.title} 发布时间不在24小时内（{pub_minutes:.0f}分钟前），跳过折扣")
                return
            # 提取种子ID：兼容查询参数格式 (?id=123) 与 NexusPHP 路径格式 (/t/123)
            torrent_id = None
            for source_url in (torrent.page_url, torrent.enclosure):
                if not source_url:
                    continue
                match = re.search(r'(?<=id=)\d+', source_url) or re.search(r'/t/(\d+)', source_url)
                if match:
                    torrent_id = match.group(1) if match.lastindex else match.group(0)
                    break
            if not torrent_id:
                logger.warning(f"TTGL无法提取种子ID: {torrent.title} (page_url={torrent.page_url}, enclosure={torrent.enclosure})")
                return
            # 获取Cookie
            site_info = self.__ttgl_get_site_cookie()
            if not site_info:
                return
            cookie, ua = site_info
            # 检查是否有库存
            unused = self.__ttgl_get_unused_discount_count(cookie, ua, tier)
            if unused is None:
                logger.error("TTGL库存检查失败（cookie失效/未登录/网络异常），本次跳过折扣流程，避免误购买")
                return
            if unused <= 0:
                # 没有库存，先购买
                if not self.__ttgl_purchase_discount(cookie, ua, tier):
                    return
            # 使用折扣
            self.__ttgl_use_discount(cookie, ua, torrent_id, tier)
        except Exception as err:
            logger.error(f"TTGL折扣应用失败: {err}")

    def __get_hash(self, torrent: Any) -> str:
        """兼容获取 qBittorrent 与 Transmission 种子 Hash"""
        try:
            service = self.service_info
            if service and DownloaderHelper().is_downloader("qbittorrent", service=service):
                return torrent.get("hash") or ""
            return getattr(torrent, "hashString", "") or ""
        except Exception as err:
            logger.error(f"获取种子 Hash 失败：{str(err)}")
            return ""

    def __get_all_hashes(self, torrents: List[Any]) -> List[str]:
        """提取下载器种子列表中的全部有效 Hash"""
        return [torrent_hash for torrent in torrents if (torrent_hash := self.__get_hash(torrent))]

    def __get_label(self, torrent: Any) -> List[str]:
        """兼容获取 qBittorrent 标签和 Transmission Labels"""
        try:
            service = self.service_info
            if service and DownloaderHelper().is_downloader("qbittorrent", service=service):
                return list(self._parse_tags(torrent.get("tags")))
            return [str(item).strip() for item in getattr(torrent, "labels", None) or [] if str(item).strip()]
        except Exception as err:
            logger.error(f"获取种子标签失败：{str(err)}")
            return []

    def __get_torrent_info(self, torrent: Any, now_timestamp: int = None) -> dict:
        """统一提取 qBittorrent 与 transmission-rpc v7 种子状态"""
        default_info = {
            "hash": "",
            "title": "",
            "seeding_time": 0,
            "ratio": 0,
            "uploaded": 0,
            "downloaded": 0,
            "avg_upspeed": 0,
            "upspeed": 0,
            "iatime": 0,
            "dltime": 0,
            "total_size": 0,
            "progress": 0,
            "amount_left": 0,
            "completion_on": 0,
            "add_time": "",
            "add_on": 0,
            "tags": "",
            "tracker": "",
        }
        try:
            if now_timestamp is None:
                now_timestamp = int(time.time())
            service = self.service_info
            if service and DownloaderHelper().is_downloader("qbittorrent", service=service):
                torrent_id = torrent.get("hash")
                title = torrent.get("name")
                added_on = torrent.get("added_on") or 0
                completion_on = torrent.get("completion_on") or 0
                last_activity = torrent.get("last_activity") or 0
                dltime = now_timestamp - added_on if added_on > 0 else 0
                seeding_time = now_timestamp - completion_on if completion_on > 0 else 0
                iatime = now_timestamp - last_activity if last_activity > 0 else 0
                ratio = torrent.get("ratio") or 0
                uploaded = torrent.get("uploaded") or 0
                downloaded = torrent.get("downloaded") or 0
                total_size = torrent.get("total_size") or torrent.get("size") or 0
                progress = torrent.get("progress") or 0
                amount_left = torrent.get("amount_left") or 0
                tags = torrent.get("tags") or ""
                tracker = torrent.get("tracker") or ""
                upspeed = torrent.get("upspeed") or 0
            else:
                torrent_id = getattr(torrent, "hashString", "")
                title = getattr(torrent, "name", "")
                done_date = getattr(torrent, "done_date", None) or getattr(torrent, "date_done", None)
                added_date = getattr(torrent, "added_date", None) or getattr(torrent, "date_added", None)
                activity_date = getattr(torrent, "activity_date", None) or getattr(torrent, "date_active", None)
                done_timestamp = int(done_date.timestamp()) if done_date and done_date.timestamp() > 0 else 0
                added_on = int(added_date.timestamp()) if added_date and added_date.timestamp() > 0 else 0
                activity_timestamp = int(activity_date.timestamp()) if activity_date and activity_date.timestamp() > 0 else 0
                seeding_time = now_timestamp - done_timestamp if done_timestamp else 0
                dltime = now_timestamp - added_on if added_on else 0
                iatime = now_timestamp - activity_timestamp if activity_timestamp else 0
                total_size = getattr(torrent, "total_size", 0) or 0
                progress = getattr(torrent, "progress", 0) or 0
                downloaded = int(total_size * progress / 100)
                ratio = getattr(torrent, "ratio", 0) or 0
                uploaded = int(downloaded * ratio)
                tags = getattr(torrent, "labels", None) or ""
                tracker_list = getattr(torrent, "tracker_list", None)
                tracker = tracker_list[0] if tracker_list else ""
                upspeed = getattr(torrent, "upspeed", 0) or 0
            avg_upspeed = int(uploaded / dltime) if dltime else (int(uploaded / seeding_time) if seeding_time else 0)
            # 分享率 = 上传量 / 种子体积（PT 下载量最大只计算种子体积），Transmission 保持原始分享率
            if service and DownloaderHelper().is_downloader("qbittorrent", service=service):
                ratio = uploaded / total_size if total_size > 0 else 0
            return {
                "hash": torrent_id,
                "title": title,
                "seeding_time": seeding_time,
                "ratio": ratio,
                "uploaded": uploaded,
                "downloaded": downloaded,
                "avg_upspeed": avg_upspeed,
                "upspeed": upspeed,
                "iatime": iatime,
                "dltime": dltime,
                "total_size": total_size,
                "progress": progress,
                "amount_left": amount_left,
                "completion_on": completion_on,
                "add_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(added_on)),
                "add_on": added_on,
                "tags": tags,
                "tracker": tracker,
            }
        except Exception as err:
            logger.warning(f"提取种子信息失败: {err}，返回默认值")
            return default_info

    def __get_average_bandwidth(
        self,
        sample_count: int = 2,
        interval: float = 1.0,
    ) -> Tuple[Optional[float], Optional[float]]:
        """多次采样所有下载器带宽并返回平均值"""
        upload_speeds: List[float] = []
        download_speeds: List[float] = []
        for index in range(sample_count):
            downloader_info = self.__get_downloader_info()
            if downloader_info:
                upload_speeds.append(downloader_info.upload_speed or 0)
                download_speeds.append(downloader_info.download_speed or 0)
            if index < sample_count - 1:
                time.sleep(interval)
        if not upload_speeds or not download_speeds:
            return None, None
        return sum(upload_speeds) / len(upload_speeds), sum(download_speeds) / len(download_speeds)

    def __get_downloader_info(self) -> schemas.DownloaderInfo:
        """通过插件链汇总当前所有下载器的实时传输信息"""
        result = schemas.DownloaderInfo()
        transfer_infos = self.chain.run_module("downloader_info")
        for transfer_info in transfer_infos or []:
            result.download_speed += transfer_info.download_speed
            result.upload_speed += transfer_info.upload_speed
            result.download_size += transfer_info.download_size
            result.upload_size += transfer_info.upload_size
        return result

    def __get_downloader_free_space(self, task: BrushTaskConfig) -> Optional[int]:
        """获取下载器剩余磁盘空间（字节），通过 QB API sync/maindata → server_state.free_space_on_disk"""
        try:
            helper = DownloaderHelper()
            service = helper.get_service(name=task.downloader if task else None)
            if not service or not service.instance:
                return None
            qb = service.instance
            # qBittorrent API: sync/maindata → server_state.free_space_on_disk
            if hasattr(qb, 'qbc') and qb.qbc:
                data = qb.qbc.sync_maindata(rid=0)
                if data and 'server_state' in data:
                    state = data['server_state']
                    free_space = state.get('free_space_on_disk')
                    if free_space is not None:
                        return int(free_space)
            # 备选：通过 preferences 获取
            if hasattr(qb, 'preferences'):
                prefs = qb.preferences()
                free_space = prefs.get('free_space_on_disk')
                if free_space is not None:
                    return int(free_space)
        except Exception as err:
            logger.warning(f"获取下载器剩余空间失败: {err}")
        return None

    def __get_downloading_count(self) -> int:
        """获取带当前任务唯一标签的下载中种子数量"""
        task = self._get_task_config()
        downloader = self.downloader
        if not task or not downloader:
            return 0
        try:
            torrents = downloader.get_downloading_torrents(tags=task.brush_tag)
            return len(torrents or [])
        except Exception as err:
            logger.error(f"获取任务 [{task.name}] 下载数量失败：{str(err)}")
            return 0

    def __get_global_downloading_count(self) -> int:
        """按下载器去重汇总带全局刷流标签的下载中种子数量。"""
        total_count = 0
        downloader_names = {task.downloader for task in self._task_configs.values() if task.downloader}
        downloader_helper = DownloaderHelper()
        for downloader_name in downloader_names:
            try:
                service = downloader_helper.get_service(name=downloader_name)
                if not service or not service.instance:
                    continue
                torrents = service.instance.get_downloading_torrents(tags=self.GLOBAL_BRUSH_TAG)
                total_count += len(torrents or [])
            except Exception as err:
                logger.error(f"获取下载器 [{downloader_name}] 全局刷流下载数量失败：{str(err)}")
        return total_count

    def __check_speed_limit(self, task: BrushTaskConfig, torrents: List[Any] = None) -> None:
        """任务级限速检查，根据分享率触发限速（下载中和完成后独立配置）"""
        if not task.speed_limit_downloading and not task.speed_limit_complete:
            return
        downloader = self.downloader
        if not downloader:
            return
        try:
            # 获取所有种子（优先使用传入的缓存数据）
            if torrents is None:
                torrents, error = downloader.get_torrents()
                if error or not torrents:
                    return
            else:
                error = None
            if error or not torrents:
                return
            # 只限速该任务的种子（使用任务专属标签）
            task_torrents = [
                t for t in torrents
                if self._torrent_has_tag(t, task.brush_tag)
            ]
            if not task_torrents:
                return
            now_timestamp = int(time.time())
            for torrent in task_torrents:
                torrent_info = self.__get_torrent_info(torrent, now_timestamp=now_timestamp)
                total_size = torrent_info.get("total_size", 0)
                is_completed = self._is_completed(torrent_info)
                torrent_hash = self.__get_hash(torrent)
                if not torrent_hash:
                    continue
                # 分享率判断（与删种条件一致：ratio = uploaded / total_size）
                ratio = torrent_info.get("ratio", 0)
                if total_size <= 0:
                    continue
                # 根据种子状态选择对应的阈值和限速值
                if not is_completed and task.speed_limit_downloading:
                    # 下载中
                    if not task.speed_limit_downloading_threshold:
                        continue
                    threshold = float(task.speed_limit_downloading_threshold)
                    limit = task.speed_limit_downloading_value if task.speed_limit_downloading_value else None
                    if ratio >= threshold:
                        downloader.change_torrent(
                            hash_string=torrent_hash,
                            upload_limit=limit,
                        )
                elif is_completed and task.speed_limit_complete:
                    # 已完成
                    if not task.speed_limit_complete_threshold:
                        continue
                    threshold = float(task.speed_limit_complete_threshold)
                    limit = int(task.speed_limit_complete_value) if task.speed_limit_complete_value else None
                    if ratio >= threshold:
                        downloader.change_torrent(
                            hash_string=torrent_hash,
                            upload_limit=limit,
                        )
            logger.info(f"刷流任务 [{task.name}] 限速检查完成")
        except Exception as err:
            logger.error(f"刷流任务 [{task.name}] 限速检查失败：{str(err)}")

    @staticmethod
    def __get_pubminutes(pubdate: str) -> float:
        """计算站点发布时间距当前时间的分钟数"""
        if not pubdate:
            return 0
        try:
            publish_time = datetime.strptime(pubdate.replace("T", " ").replace("Z", ""), "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - publish_time).total_seconds() / 60
        except (TypeError, ValueError) as err:
            logger.error(f"解析发布时间 {pubdate} 失败：{str(err)}")
            return 0

    def __filter_torrents_by_tag(self, torrents: List[Any], exclude_tag: Optional[str]) -> List[Any]:
        """过滤包含任一删除排除标签的种子"""
        if not exclude_tag:
            return torrents
        excluded_tags = {item.strip() for item in exclude_tag.split(",") if item.strip()}
        return [
            torrent for torrent in torrents
            if not excluded_tags.intersection(self.__get_label(torrent))
        ]

    def __get_subscribe_titles(self) -> Set[str]:
        """识别并缓存当前订阅可用于排除匹配的标题集合（30秒TTL缓存）"""
        task = self._get_task_config()
        if not task or not task.except_subscribe:
            return set()
        
        now = time.time()
        if self._subscribe_titles_cache is not None and now - self._subscribe_titles_cache_time < 30:
            return self._subscribe_titles_cache
        
        subscribes = SubscribeOper().list() or []
        for subscribe in subscribes:
            cache_key = f"{subscribe.id}_{subscribe.name}"
            if cache_key in self._subscribe_infos:
                continue
            titles = [subscribe.name]
            try:
                meta = MetaInfo(subscribe.name)
                meta.year = subscribe.year
                meta.begin_season = subscribe.season or None
                meta.type = MediaType(subscribe.type)
                mediainfo: MediaInfo = self.chain.recognize_media(
                    meta=meta,
                    mtype=meta.type,
                    tmdbid=subscribe.tmdbid,
                    doubanid=subscribe.doubanid,
                    cache=True,
                )
                if mediainfo:
                    titles.extend(mediainfo.names)
            except Exception as err:
                logger.error(f"识别订阅 {subscribe.name} 失败：{str(err)}")
            self._subscribe_infos[cache_key] = [item.strip() for item in titles if item and item.strip()]
        current_keys = {f"{subscribe.id}_{subscribe.name}" for subscribe in subscribes}
        for cache_key in set(self._subscribe_infos) - current_keys:
            self._subscribe_infos.pop(cache_key, None)
        result = {title for titles in self._subscribe_infos.values() for title in titles}
        self._subscribe_titles_cache = result
        self._subscribe_titles_cache_time = now
        return result

    @staticmethod
    def __filter_torrents_contains_subscribe(
        torrents: List[TorrentInfo],
        subscribe_titles: Set[str],
    ) -> List[TorrentInfo]:
        """排除标题或描述命中任一订阅名称的候选种子"""
        if not subscribe_titles:
            return torrents
        included: List[TorrentInfo] = []
        for torrent in torrents:
            title = torrent.title or ""
            description = torrent.description or ""
            if any(item in title or item in description for item in subscribe_titles):
                logger.info(f"命中订阅内容，排除种子：{title}|{description}")
                continue
            included.append(torrent)
        return included

    @staticmethod
    def __bytes_to_gb(size_in_bytes: float) -> float:
        """把字节数转换为 GB"""
        return float(size_in_bytes or 0) / 1024 ** 3

    @staticmethod
    def __calculate_seeding_torrents_size(torrent_tasks: Dict[str, dict]) -> float:
        """计算未删除托管种子的总做种体积"""
        return sum(
            item.get("size", 0) or 0
            for item in torrent_tasks.values()
            if not item.get("deleted")
        )

    def __auto_archive_tasks(self, torrent_tasks: Dict[str, dict]) -> None:
        """按当前任务保留天数归档已删除种子记录"""
        task = self._get_task_config()
        if not task or not task.auto_archive_days or task.auto_archive_days <= 0:
            return
        archived_tasks: Dict[str, dict] = self._current_task_data("archived", {})
        threshold = float(task.auto_archive_days) * 86400
        now_timestamp = time.time()
        archive_hashes = []
        for torrent_hash, item in torrent_tasks.items():
            if not item.get("deleted"):
                continue
            deleted_time = item.get("deleted_time")
            if deleted_time is None or (
                isinstance(deleted_time, (int, float))
                and now_timestamp - deleted_time > threshold
            ):
                archive_hashes.append(torrent_hash)
        for torrent_hash in archive_hashes:
            archived_tasks[torrent_hash] = torrent_tasks.pop(torrent_hash)
        self._save_current_task_data("archived", archived_tasks)

    def _recalculate_statistics(self, task_id: str) -> Dict[str, int]:
        """从当前和归档记录重新计算单任务统计（单遍扫描）"""
        torrents = self._get_task_data(task_id, "torrents") or {}
        archived = self._get_task_data(task_id, "archived") or {}

        count = 0
        deleted = 0
        uploaded = 0
        downloaded = 0
        unarchived = 0
        active = 0
        active_uploaded = 0
        active_downloaded = 0

        # 单遍扫描归档记录
        for item in archived.values():
            count += 1
            if item.get("deleted"):
                deleted += 1
            uploaded += item.get("uploaded", 0) or 0
            downloaded += item.get("downloaded", 0) or 0

        # 单遍扫描当前记录
        for item in torrents.values():
            count += 1
            is_deleted = item.get("deleted")
            if is_deleted:
                deleted += 1
                unarchived += 1
            else:
                active += 1
                active_uploaded += item.get("uploaded", 0) or 0
                active_downloaded += item.get("downloaded", 0) or 0
            uploaded += item.get("uploaded", 0) or 0
            downloaded += item.get("downloaded", 0) or 0

        statistic = {
            "count": count,
            "deleted": deleted,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "unarchived": unarchived,
            "active": active,
            "active_uploaded": active_uploaded,
            "active_downloaded": active_downloaded,
        }
        self._save_task_data(task_id, "statistic", statistic)
        return statistic

    def _get_statistic_info(self, task_id: str) -> Dict[str, int]:
        """读取单任务统计并为历史空数据补齐字段"""
        defaults = {
            "count": 0,
            "deleted": 0,
            "uploaded": 0,
            "downloaded": 0,
            "unarchived": 0,
            "active": 0,
            "active_uploaded": 0,
            "active_downloaded": 0,
        }
        statistic = self._get_task_data(task_id, "statistic") or {}
        return {**defaults, **statistic}

    @staticmethod
    def _is_valid_time_range(time_range: Optional[str]) -> bool:
        """校验 HH:MM-HH:MM 格式的每日时间段"""
        if not time_range or not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", time_range):
            return False
        try:
            start, end = time_range.split("-", 1)
            datetime.strptime(start, "%H:%M")
            datetime.strptime(end, "%H:%M")
            return True
        except ValueError:
            return False

    def _is_current_time_in_range(self, task: Optional[BrushTaskConfig] = None) -> bool:
        """判断当前时间是否处于任务允许的每日开启区间"""
        task = task or self._get_task_config()
        if not task or not self._is_valid_time_range(task.active_time_range):
            return True
        start_text, end_text = task.active_time_range.split("-", 1)
        start_time = datetime.strptime(start_text, "%H:%M").time()
        end_time = datetime.strptime(end_text, "%H:%M").time()
        current_time = datetime.now().time()
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        return current_time >= start_time or current_time <= end_time

    @staticmethod
    def __get_site_by_torrent(torrent: Any) -> Tuple[int, str]:
        """根据 Tracker 或磁力链接识别种子所属站点"""
        trackers: List[str] = []
        last_domain = "未知"
        tracker_url = torrent.get("tracker") if isinstance(torrent, dict) else None
        if not tracker_url:
            tracker_list = getattr(torrent, "tracker_list", None)
            tracker_url = tracker_list[0] if tracker_list else None
        if tracker_url:
            trackers.append(tracker_url)
        magnet_link = torrent.get("magnet_uri") if isinstance(torrent, dict) else getattr(torrent, "magnet_link", None)
        if magnet_link:
            trackers.extend(unquote(item) for item in parse_qs(urlparse(magnet_link).query).get("tr", []))
        tracker_mappings = {
            "chdbits.xyz": "ptchdbits.co",
            "agsvpt.trackers.work": "agsvpt.com",
            "tracker.cinefiles.info": "audiences.me",
        }
        for tracker in trackers:
            if not tracker:
                continue
            domain = next(
                (mapped for keyword, mapped in tracker_mappings.items() if keyword in tracker),
                StringUtils.get_url_domain(tracker),
            )
            last_domain = domain or last_domain
            site = SitesHelper().get_indexer(domain)
            if site:
                return site.get("id"), site.get("name")
        return 0, last_domain

    def _log_and_notify_error(self, message: str) -> None:
        """记录错误并写入系统消息中心"""
        logger.error(message)
        self.systemmessage.put(message, title="站点刷流")

    def __send_delete_message(self, torrent_task: dict, reason: str) -> None:
        """发送包含任务、站点、种子、做种时长、上传量、种子体积和原因的删种通知"""
        task = self._get_task_config()
        if not task or not task.notify:
            return
        # v5.3.1: 计算做种时长
        seeding_time = torrent_task.get("seeding_time", 0)
        if seeding_time:
            hours = int(seeding_time // 3600)
            minutes = int((seeding_time % 3600) // 60)
            if hours > 0:
                seeding_time_str = f"{hours}小时{minutes}分钟"
            else:
                seeding_time_str = f"{minutes}分钟"
        else:
            seeding_time_str = "未知"
        # v5.3.1: 计算上传量和种子体积
        uploaded = torrent_task.get("uploaded", 0)
        size = torrent_task.get("size", 0) or torrent_task.get("total_size", 0)
        uploaded_gb = self.__bytes_to_gb(uploaded)
        size_gb = self.__bytes_to_gb(size)
        text = (
            f"任务：{task.name}\n"
            f"站点：{torrent_task.get('site_name') or '未知'}\n"
            f"标题：{torrent_task.get('title') or '未知'}\n"
            f"做种时长：{seeding_time_str}\n"
            f"上传量：{uploaded_gb:.2f} GB / 种子体积：{size_gb:.2f} GB\n"
            f"原因：{reason}"
        )
        self.post_message(mtype=NotificationType.SiteMessage, title="【刷流任务种子删除】", text=text)

    @staticmethod
    def __build_add_message_text(torrent: Union[TorrentInfo, dict], task_name: str) -> str:
        """兼容候选对象和任务字典构建新增通知文本"""
        def read_value(key: str, default: Any = None) -> Any:
            """统一读取候选对象或字典字段"""
            return torrent.get(key, default) if isinstance(torrent, dict) else getattr(torrent, key, default)

        lines = [f"任务：{task_name}"]
        labels = {
            "site_name": "站点",
            "title": "标题",
            "description": "内容",
            "size": "大小",
            "pubdate": "发布时间",
            "seeders": "做种数",
            "volume_factor": "促销",
            "hit_and_run": "Hit&Run",
        }
        for key, label in labels.items():
            value = read_value(key)
            if key == "size" and value:
                value = StringUtils.str_filesize(value)
            if value not in (None, "", False):
                lines.append(f"{label}：{'是' if key == 'hit_and_run' else value}")
        return "\n".join(lines)

    def __send_add_message(self, torrent: Union[TorrentInfo, dict]) -> None:
        """发送当前任务新增刷流种子的通知"""
        task = self._get_task_config()
        if not task or not task.notify:
            return
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="【刷流任务种子下载】",
            text=self.__build_add_message_text(torrent, task.name),
        )

    def __send_message(self, title: str, text: str) -> None:
        """按当前任务通知开关发送通用站点消息"""
        task = self._get_task_config()
        if task and task.notify:
            self.post_message(mtype=NotificationType.SiteMessage, title=title, text=text)

    def __log_and_send_torrent_task_update_message(
        self,
        title: str,
        status: str,
        reason: str,
        torrent_tasks: List[dict],
    ) -> None:
        """记录并汇总发送标签同步导致的任务状态变更"""
        if not torrent_tasks:
            return
        task = self._get_task_config()
        first_title = torrent_tasks[0].get("title") or "未知种子"
        text = (
            f"任务：{task.name if task else '未知'}\n"
            f"内容：{first_title} 等 {len(torrent_tasks)} 个种子已{status}\n"
            f"原因：{reason}"
        )
        logger.info(f"{title}，{text}")
        self.__send_message(title, text)
