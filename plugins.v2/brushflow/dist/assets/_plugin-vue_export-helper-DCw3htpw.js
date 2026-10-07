const taskDefaults = {
  id: '',
  name: '',
  enabled: true,
  notify: true,
  site_id: null,
  downloader: '',
  brush_interval: 10,
  check_interval: 5,
  cron: null,
  active_time_range: null,
  site_ratio_control: false,
  site_ratio_target: null,
  disksize: null,
  maxupspeed: null,
  maxdlspeed: null,
  maxdlcount: null,
  freeleech: 'free',
  hr: 'yes',
  include: null,
  exclude: null,
  size: null,
  seeder: null,
  timezone_offset: 0,
  pubtime: null,
  // v5.5.7：老种下载
  old_seed_min_age: null,
  seed_time: null,
  hr_seed_time: null,
  seed_ratio: null,
  seed_size: null,
  download_time: null,
  seed_avgspeed: null,
  seed_inactivetime: null,
  delete_size_range: null,
  up_speed: null,
  dl_speed: null,
  auto_archive_days: null,
  save_path: null,
  delete_except_tags: null,
  except_subscribe: true,
  proxy_delete: false,
  del_no_free: false,
  qb_category: null,
  site_hr_active: false,
  site_skip_tips: false,
  rss_support: false,
  // v5.3.0 新增
  skip_check_no_active: false,
  download_limit_enabled: false,
  download_limit: null,
  only_delete_completed: true,
  min_seed_time: 10,
  speed_limit_downloading: false,
  speed_limit_downloading_threshold: null,
  speed_limit_downloading_value: null,
  speed_limit_complete: false,
  speed_limit_complete_threshold: null,
  speed_limit_complete_value: null,
  // v5.3.2：站点数据刷新
  site_data_enabled: false,
  site_data_interval: null,
  // v5.3.10：复活区
  resurrect_enabled: false,
  resurrect_url: null,
  resurrect_min_peers: null,
  // v5.5.6：TTGL 折扣档位
  ttgl_discount_enabled: false,
  ttgl_discount_min_size: null,
  ttgl_discount_tier: '30',
};

/** 统一提取宿主 API 客户端与标准响应模型中的业务数据。 */
function unwrapResponse(response) {
  if (response && Object.prototype.hasOwnProperty.call(response, 'success')) {
    if (response.success === false) throw new Error(response.message || '操作失败')
    return response.data
  }
  return response?.data ?? response
}

/** 基于完整默认值创建可安全编辑的任务深拷贝。 */
function cloneTask(task = {}) {
  return JSON.parse(JSON.stringify({ ...taskDefaults, ...(task || {}) }))
}

/** 把表单空值和数字字段标准化为后端请求模型需要的类型。 */
function normalizeTask(task) {
  const result = cloneTask(task);
  const nullableNumbers = [
    'disksize',
    'maxupspeed',
    'maxdlspeed',
    'maxdlcount',
    'site_ratio_target',
    'seed_time',
    'hr_seed_time',
    'seed_ratio',
    'seed_size',
    'download_time',
    'seed_avgspeed',
    'seed_inactivetime',
    'up_speed',
    'dl_speed',
    'auto_archive_days',
    // v5.3.0 新增
    'download_limit',
    'speed_limit_downloading_threshold',
    'speed_limit_downloading_value',
    'speed_limit_complete_threshold',
    'speed_limit_complete_value',
    'site_data_interval',
    // v5.5.7：老种下载
    'old_seed_min_age',
  ];
  nullableNumbers.forEach(key => {
    const value = result[key] === '' || result[key] === null ? null : Number(result[key]);
    result[key] = value === 0 ? null : value;
  });
  // min_seed_time 默认 10 分钟
  result.min_seed_time = result.min_seed_time === '' || result.min_seed_time === null || Number(result.min_seed_time) === 0 ? 10 : Number(result.min_seed_time);
  const optionalText = [
    'cron',
    'active_time_range',
    'include',
    'exclude',
    'size',
    'seeder',
    'pubtime',
    'delete_size_range',
    'save_path',
    'delete_except_tags',
    'qb_category',
  ];
  optionalText.forEach(key => {
    result[key] = String(result[key] || '').trim() || null;
  });
  result.site_id = Number(result.site_id);
  result.brush_interval = Number(result.brush_interval || 10);
  result.check_interval = Number(result.check_interval || 5);
  result.timezone_offset = Number(result.timezone_offset || 0);
  return result
}

/** 把全局设置中的空值、零值和正数标准化为后端请求类型。 */
function normalizeSettings(settings = {}) {
  const result = { ...(settings || {}) };
  const limitFields = ['global_disksize', 'global_maxdlcount', 'global_maxupspeed', 'global_maxdlspeed'];
  limitFields.forEach(key => {
    const value = Number(result[key] || 0);
    result[key] = value > 0 ? value : null;
  });
  result.global_proxy_delete = Boolean(result.global_proxy_delete);
  result.global_delete_size_range = result.global_proxy_delete
    ? String(result.global_delete_size_range || '').trim() || null
    : null;
  result.global_dynamic_delete_threshold = result.global_proxy_delete
    ? Number(result.global_dynamic_delete_threshold || 0) || null
    : null;
  result.global_disk_space_delete = Boolean(result.global_disk_space_delete);
  result.global_disk_space_threshold = result.global_disk_space_delete
    ? Number(result.global_disk_space_threshold || 0) || null
    : null;
  result.global_disk_space_target = result.global_disk_space_delete
    ? Number(result.global_disk_space_target || 0) || null
    : null;
  // v5.3.2: 站点数据刷新功能
  result.global_site_data_enabled = Boolean(result.global_site_data_enabled);
  result.global_site_data_interval = result.global_site_data_enabled
    ? Number(result.global_site_data_interval || 0)
    : null;
  result.global_site_data_interval = result.global_site_data_interval > 0 ? result.global_site_data_interval : null;
  return result
}

/** 将字节数格式化为适合紧凑界面展示的容量文本。 */
function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const number = bytes / 1024 ** index;
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} ${units[index]}`
}

/** 格式化站点下载量显示（自动选择GB/TB/PB单位） */
function formatSiteDownload(siteDownloaded) {
  if (!siteDownloaded?.available) return '等待数据'
  const gb = Number(siteDownloaded.current || 0);
  if (!Number.isFinite(gb) || gb <= 0) return '暂无'
  const units = ['GB', 'TB', 'PB'];
  const index = Math.min(Math.floor(Math.log(gb) / Math.log(1024)), units.length - 1);
  const number = gb / 1024 ** index;
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} ${units[index]}`
}

/** 将时间值格式化为当前界面使用的月日与时分。 */
function formatDateTime(value) {
  if (!value) return '暂无'
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

/** 计算一次运行记录的秒级耗时。 */
function formatDuration(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return '-'
  const seconds = Math.max(Math.round((new Date(finishedAt) - new Date(startedAt)) / 1000), 0);
  return `${seconds} 秒`
}

/** 返回任务状态对应的中文文本、主题色和图标。 */
function taskStateMeta(state) {
  const states = {
    running: { text: '运行中', color: 'success', icon: 'mdi-check-circle-outline' },
    brush: { text: '正在刷新', color: 'primary', icon: 'mdi-sync' },
    check: { text: '正在检查', color: 'info', icon: 'mdi-progress-check' },
    paused: { text: '已暂停', color: 'secondary', icon: 'mdi-pause-circle-outline' },
    waiting: { text: '等待时段', color: 'warning', icon: 'mdi-clock-outline' },
    waiting_ratio: { text: '待刷流', color: 'warning', icon: 'mdi-target' },
    waiting_download: { text: '待刷流', color: 'warning', icon: 'mdi-download-check' },
    waiting_download_limit: { text: '待刷流', color: 'warning', icon: 'mdi-download-check' },
    ratio_unavailable: { text: '等待数据', color: 'info', icon: 'mdi-database-clock-outline' },
    disabled: { text: '插件停用', color: 'secondary', icon: 'mdi-stop-circle-outline' },
    error: { text: '运行异常', color: 'error', icon: 'mdi-alert-circle-outline' },
  };
  return states[state] || states.running
}

/** 根据已下载量和总大小计算种子完成百分比。 */
function torrentProgress(item) {
  const progress = Number(item?.progress || 0);
  if (progress > 0) return Math.min(Math.round(progress * 100), 100)
  const size = Number(item?.size || 0);
  const downloaded = Number(item?.downloaded || 0);
  if (size > 0 && downloaded > 0) return Math.min(Math.round((downloaded * 100) / size), 100);
  return 0
}

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

export { _export_sfc as _, formatDateTime as a, formatSiteDownload as b, cloneTask as c, formatDuration as d, torrentProgress as e, formatBytes as f, normalizeSettings as g, normalizeTask as n, taskStateMeta as t, unwrapResponse as u };
