import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_optional_positive_number(value):
    """把历史配置中的空值和 0 统一转换为未设置。"""
    if value is None or value == "":
        return None
    try:
        if float(value) == 0:
            return None
    except (TypeError, ValueError):
        return value
    return value


class BrushTaskPayload(BaseModel):
    """
    刷流任务新增与更新请求模型
    """

    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=80)
    enabled: bool = True
    notify: bool = True
    site_id: int = Field(..., gt=0)
    downloader: str = Field(..., min_length=1, max_length=80)
    brush_interval: int = Field(10, ge=1, le=1440)
    check_interval: int = Field(5, ge=1, le=1440)
    cron: Optional[str] = None
    active_time_range: Optional[str] = None
    site_ratio_control: bool = False
    site_ratio_target: Optional[float] = Field(None, gt=0)
    disksize: Optional[float] = Field(None, gt=0)
    maxupspeed: Optional[float] = Field(None, gt=0)
    maxdlspeed: Optional[float] = Field(None, gt=0)
    maxdlcount: Optional[int] = Field(None, gt=0)
    freeleech: Literal["", "free", "2xfree"] = "free"
    hr: Literal["yes", "no"] = "yes"
    include: Optional[str] = None
    exclude: Optional[str] = None
    size: Optional[str] = None
    seeder: Optional[str] = None
    timezone_offset: float = 0
    pubtime: Optional[str] = None
    old_seed_min_age: Optional[float] = Field(None, gt=0, description="老种下载：发布时间大于此小时数才下载（与 pubtime 发布上限互补）")
    seed_time: Optional[float] = Field(None, gt=0)
    hr_seed_time: Optional[float] = Field(None, gt=0)
    seed_ratio: Optional[float] = Field(None, gt=0)
    seed_size: Optional[float] = Field(None, gt=0)
    download_time: Optional[float] = Field(None, gt=0)
    seed_avgspeed: Optional[float] = Field(None, gt=0)
    seed_inactivetime: Optional[float] = Field(None, gt=0)
    delete_size_range: Optional[str] = None
    up_speed: Optional[float] = Field(None, gt=0)
    dl_speed: Optional[float] = Field(None, gt=0)
    auto_archive_days: Optional[float] = Field(None, gt=0)
    save_path: Optional[str] = None
    delete_except_tags: Optional[str] = None
    except_subscribe: bool = True
    proxy_delete: bool = False
    del_no_free: bool = False
    qb_category: Optional[str] = None
    site_hr_active: bool = False
    site_skip_tips: bool = False
    rss_support: bool = False
    tag: Optional[str] = None
    # 本地版新增配置
    skip_check_no_active: bool = False
    download_limit_enabled: bool = False
    download_limit: Optional[int] = Field(None, gt=0)
    only_delete_completed: bool = True
    min_seed_time: float = Field(10, gt=0, description="最短做种时间（分钟），未设时默认10分钟")
    speed_limit_downloading: bool = Field(False, description="下载中分享率达到即触发限速")
    speed_limit_downloading_threshold: Optional[float] = Field(None, gt=0, description="下载中限速的分享率阈值")
    speed_limit_downloading_value: Optional[int] = Field(None, gt=0, description="下载中限速的限速值（KB/s）")
    speed_limit_complete: bool = Field(False, description="下载完成后触发限速")
    speed_limit_complete_threshold: Optional[float] = Field(None, gt=0, description="完成后限速的分享率阈值")
    speed_limit_complete_value: Optional[int] = Field(None, gt=0, description="完成后限速的限速值（KB/s）")
    site_data_enabled: bool = False
    site_data_interval: Optional[int] = Field(None, ge=1, description="站点数据刷新间隔（分钟）")
    resurrect_enabled: bool = False
    resurrect_url: Optional[str] = Field(None, description="复活区URL")
    resurrect_min_peers: Optional[int] = Field(None, ge=0, description="最小下载人数")
    ttgl_discount_enabled: bool = Field(False, description="TTGL积分商店自动购买并使用下载折扣")
    ttgl_discount_min_size: Optional[float] = Field(None, gt=0, description="大于此体积(GB)才购买使用折扣")
    ttgl_discount_tier: Literal["30", "50", "fl"] = Field("30", description="TTGL折扣档位：30=30%下载，50=50%下载，fl=免费FL")

    @field_validator(
        "disksize",
        "maxupspeed",
        "maxdlspeed",
        "maxdlcount",
        "site_ratio_target",
        "seed_time",
        "hr_seed_time",
        "seed_ratio",
        "seed_size",
        "download_time",
        "seed_avgspeed",
        "seed_inactivetime",
        "up_speed",
        "dl_speed",
        "auto_archive_days",
        "old_seed_min_age",
        mode="before",
    )
    @classmethod
    def normalize_optional_positive_number(cls, value):
        """兼容旧版用 0 表示未配置的可选正数配置。"""
        return _normalize_optional_positive_number(value)

    @field_validator("name", "downloader")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        """清理必填文本并拒绝纯空白内容"""
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("字段不能为空")
        return cleaned

    @field_validator(
        "cron",
        "active_time_range",
        "include",
        "exclude",
        "size",
        "seeder",
        "pubtime",
        "delete_size_range",
        "save_path",
        "delete_except_tags",
        "qb_category",
        "tag",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        """把空白可选文本统一转换为 None"""
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: Optional[str]) -> Optional[str]:
        """qBittorrent 标签不允许包含逗号"""
        if value and "," in value:
            raise ValueError("下载器标签不能包含逗号")
        return value

    @field_validator("size", "seeder", "pubtime", "delete_size_range")
    @classmethod
    def validate_number_range(cls, value: Optional[str]) -> Optional[str]:
        """校验单值或数字范围配置"""
        if value and not re.fullmatch(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?", value):
            raise ValueError("请输入数字或数字范围，例如 10 或 10-80")
        return value

    @field_validator("active_time_range")
    @classmethod
    def validate_active_time_range(cls, value: Optional[str]) -> Optional[str]:
        """校验每日开启时间段格式并允许跨越午夜"""
        if not value:
            return None
        if not re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", value):
            raise ValueError("开启时间段格式应为 HH:MM-HH:MM")
        start, end = value.split("-", 1)
        datetime.strptime(start, "%H:%M")
        datetime.strptime(end, "%H:%M")
        return value

    @field_validator("include", "exclude")
    @classmethod
    def validate_regex(cls, value: Optional[str]) -> Optional[str]:
        """提前校验选种正则表达式"""
        if value:
            re.compile(value)
        return value

    @model_validator(mode="after")
    def validate_site_ratio_control(self):
        """启用站点分享率控制时要求同时配置有效目标值。"""
        if self.site_ratio_control and self.site_ratio_target is None:
            raise ValueError("启用站点分享率控制时必须设置目标分享率")
        return self


class BrushTaskStatePayload(BaseModel):
    """
    刷流任务启停请求模型
    """

    enabled: bool


class BrushFlowSettingsPayload(BaseModel):
    """
    刷流插件全局设置请求模型
    """

    enabled: bool = True
    show_sidebar_nav: bool = True
    global_disksize: Optional[float] = Field(None, gt=0)
    global_maxdlcount: Optional[int] = Field(None, gt=0)
    global_maxupspeed: Optional[float] = Field(None, gt=0)
    global_maxdlspeed: Optional[float] = Field(None, gt=0)
    global_proxy_delete: bool = False
    global_delete_size_range: Optional[str] = None
    global_dynamic_delete_threshold: Optional[float] = Field(None, gt=0, description="触发阈值（GB），剩余空间小于此值时启动动态删种")
    global_disk_space_delete: bool = False
    global_disk_space_threshold: Optional[float] = Field(None, gt=0)
    global_disk_space_target: Optional[float] = Field(None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def clear_global_delete_size_range_when_disabled(cls, data):
        """关闭全局动态删种时忽略隐藏阈值，确保无效草稿不会阻止保存。"""
        if not isinstance(data, dict):
            return data
        enabled = data.get("global_proxy_delete", False)
        if enabled not in (False, None, 0, "", "0", "false", "False"):
            return data
        normalized = dict(data)
        normalized["global_delete_size_range"] = None
        normalized["global_dynamic_delete_threshold"] = None
        return normalized

    @field_validator(
        "global_disksize",
        "global_maxdlcount",
        "global_maxupspeed",
        "global_maxdlspeed",
        "global_disk_space_threshold",
        "global_disk_space_target",
        mode="before",
    )
    @classmethod
    def normalize_optional_positive_number(cls, value):
        """兼容全局限额中用于表示不限的空值和 0。"""
        return _normalize_optional_positive_number(value)

    @field_validator("global_delete_size_range", mode="before")
    @classmethod
    def normalize_global_delete_size_range(cls, value):
        """清理全局动态删种阈值中的空白值。"""
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("global_delete_size_range")
    @classmethod
    def validate_global_delete_size_range(cls, value: Optional[str]) -> Optional[str]:
        """校验全局动态删种的单值或区间阈值。"""
        if value and not re.fullmatch(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?", value):
            raise ValueError("请输入数字或数字范围，例如 100 或 50-100")
        if value:
            limits = [float(item) for item in value.split("-")]
            if any(item <= 0 for item in limits) or (len(limits) > 1 and limits[0] >= limits[1]):
                raise ValueError("动态删种区间下限必须小于上限")
        return value

    @model_validator(mode="after")
    def validate_global_dynamic_delete(self):
        """启用全局动态删种时要求配置有效体积阈值。"""
        if self.global_proxy_delete and not self.global_delete_size_range and not self.global_dynamic_delete_threshold:
            raise ValueError("启用全局动态删种时必须设置至少一个触发条件：体积阈值或剩余空间阈值")
        return self
