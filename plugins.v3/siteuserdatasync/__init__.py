"""
站点数据同步插件
定时同步指定站点的用户数据
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from app.chain.site import SiteChain
from app.core.config import settings
from app.core.event import eventmanager
from app.db.models.siteuserdata import SiteUserData
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.string import StringUtils
from app.api.endpoints.plugin import register_plugin_api


class siteuserdatasync(_PluginBase):
    """站点数据同步插件。"""

    plugin_name = "站点数据同步"
    plugin_desc = "定时同步指定站点的用户数据"
    plugin_icon = "mdi-sync"
    plugin_version = "1.0.0"
    plugin_label = "站点管理"
    plugin_author = "local"
    plugin_config_prefix = "siteudata_sync_"
    plugin_order = 50
    auth_level = 1

    _enabled: bool = False
    _scheduler: Optional[BackgroundScheduler] = None
    _sites: List[int] = []
    _interval: int = 6  # 同步间隔（小时）
    _last_sync_time: Optional[datetime] = None
    _sync_count: int = 0

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self.stop_service()
        self._enabled = False
        self._sites = []
        self._interval = 6
        self._last_sync_time = None
        self._sync_count = 0

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._sites = [int(s) for s in config.get("sites", [])]
        self._interval = int(config.get("interval", 6))

        if self._enabled and self._sites:
            self.__start_scheduler()
            # 注册API路由
            register_plugin_api(plugin_id=self.__class__.__name__)
            logger.info(f"站点数据同步插件已启用，将同步 {len(self._sites)} 个站点，间隔 {self._interval} 小时")

    def __start_scheduler(self) -> None:
        """启动定时同步服务。"""
        if self._scheduler:
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        self._scheduler.add_job(
            self.__sync_sites,
            "cron",
            hour="*/6",
            minute="0",
            id="site_udata_sync",
            name="站点数据同步",
            misfire_grace_time=300,
        )
        self._scheduler.print_jobs()
        self._scheduler.start()
        logger.info(f"站点数据同步服务已启动")

    def stop_service(self) -> None:
        """停止插件后台服务并释放资源。"""
        try:
            if self._scheduler:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None
                logger.info("站点数据同步服务已停止")
        except Exception as e:
            logger.error(f"停止站点数据同步服务失败：{str(e)}")

    def __sync_sites(self) -> None:
        """执行站点数据同步。"""
        logger.info(f"开始同步 {len(self._sites)} 个站点数据...")
        site_chain = SiteChain()
        total = len(self._sites)
        success_count = 0
        failed_sites = []

        for index, site_id in enumerate(self._sites, start=1):
            site = SitesHelper().get_indexer(site_id)
            if not site:
                logger.warning(f"站点 {site_id} 不存在，跳过")
                failed_sites.append(site_id)
                continue

            site_name = site.get("name", f"站点{site_id}")
            try:
                result = site_chain.refresh_userdata(site)
                if result:
                    success_count += 1
                    logger.info(f"站点 [{index}/{total}] {site_name} 同步成功，分享率: {result.ratio or 'N/A'}")
                else:
                    logger.warning(f"站点 [{index}/{total}] {site_name} 同步失败")
                    failed_sites.append(site_id)
            except Exception as e:
                logger.error(f"站点 [{index}/{total}] {site_name} 同步异常：{str(e)}")
                failed_sites.append(site_id)

        self._last_sync_time = datetime.now()
        self._sync_count += success_count

        logger.info(f"站点数据同步完成：成功 {success_count}/{total}，失败 {len(failed_sites)}")

        if success_count > 0:
            eventmanager.send_event(EventType.SiteRefreshed, {
                "site_id": "*",
                "sites": self._sites,
                "success_count": success_count,
            })

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回插件远程命令列表。"""
        return [
            {
                "cmd": "site-sync-now",
                "name": "立即同步站点数据",
                "info": "立即执行一次指定的站点数据同步",
                "event": "site_sync_now",
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/sitedata_sync_now",
                "endpoint": self.sync_now,
                "methods": ["POST"],
                "summary": "立即同步站点数据",
                "description": "立即执行一次指定的站点数据同步",
                "auth": "bear",
            },
            {
                "path": "/sitedata_sync_status",
                "endpoint": self.get_sync_status,
                "methods": ["GET"],
                "summary": "获取同步状态",
                "description": "获取当前同步任务和统计信息",
                "auth": "bear",
            },
        ]

    def sync_now(self) -> Dict[str, Any]:
        """立即执行站点数据同步。"""
        logger.info("收到立即同步请求")
        self.__sync_sites()
        return {"success": True, "message": "同步完成"}

    def get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态。"""
        return {
            "enabled": self._enabled,
            "sites": self._sites,
            "site_names": self.__get_site_names(),
            "interval": self._interval,
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
            "sync_count": self._sync_count,
        }

    def __get_site_names(self) -> List[Dict[str, Any]]:
        """获取选中的站点名称列表。"""
        names = []
        for site_id in self._sites:
            site = SitesHelper().get_indexer(site_id)
            if site:
                names.append({
                    "id": site_id,
                    "name": site.get("name"),
                    "domain": site.get("domain"),
                })
        return names

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        sites = SitesHelper().get_indexers() or []
        site_options = [
            {"title": s.get("name"), "value": s.get("id")}
            for s in sites
            if s.get("is_active")
        ]

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "interval",
                                            "label": "同步间隔（小时）",
                                            "type": "number",
                                            "min": 1,
                                            "max": 24,
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VMultiSelect",
                                        "props": {
                                            "model": "sites",
                                            "label": "选择站点",
                                            "items": site_options,
                                            "return-object": True,
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "text": "插件将定时同步所选站点的用户数据，包括上传量、下载量、分享率等统计信息。",
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
            "sites": [],
            "interval": 6,
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页面。"""
        if not self._enabled:
            return None
        
        site_names = self.__get_site_names()
        sites_text = ", ".join([s.get("name") for s in site_names]) if site_names else "未选择站点"
        
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success",
                    "text": f"插件已启用，当前同步 {len(self._sites)} 个站点，间隔 {self._interval} 小时\n同步次数: {self._sync_count}\n最后同步: {self._last_sync_time.strftime('%Y-%m-%d %H:%M') if self._last_sync_time else '从未'}",
                }
            },
            {
                "component": "VList",
                "props": {"dense": True},
                "content": [
                    {
                        "component": "VListItem",
                        "content": [
                            {
                                "component": "VListItemTitle",
                                "content": [{"component": "span", "props": {}, "content": f"已同步站点: {sites_text}"}]
                            }
                        ]
                    }
                ]
            }
        ]

    def update_config(self, config: dict = None) -> None:
        """更新插件配置。"""
        if config:
            self._enabled = bool(config.get("enabled", False))
            self._sites = [int(s) for s in config.get("sites", [])]
            self._interval = int(config.get("interval", 6))
            
            if self._enabled and self._sites:
                self.__start_scheduler()
                # 注册API路由
                register_plugin_api(plugin_id=self.__class__.__name__)
                logger.info(f"配置已更新，同步 {len(self._sites)} 个站点")
            else:
                self.stop_service()
