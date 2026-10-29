# MoviePilot 本地定制插件仓库

自用的 MoviePilot 插件仓库（与官方仓库结构一致），存放本地定制/魔改插件，供 MoviePilot 通过插件市场直接安装与升级。

## 插件清单

**V3 线**（`plugins.v3/` + `package.v3.json`）

| 插件 ID | 名称 | 版本 | 来源（定制基准） | 说明 |
| --- | --- | --- | --- | --- |
| `brushflowmodern` | 站点刷流(本地版) | 6.1.2 | 官方 `BrushFlow` V3 6.1.2（jxxghp/MoviePilot-Plugins） | 以官方 V3 6.1.2 为基线的**独立本地版**（独立 ID，避免被在线源覆盖）。含本地魔改特性：复活区、站点数据自动刷新、站点下载量控制、任务级限速、空间不足删种、删种条件增强（老种下限/只删已完成/最短做种时间/无活跃跳过）、TTGL 积分商店折扣。 |
| `updatewechatipmodern` | 动态企微可信IP(魔改) | 5.1.6 | 书小白 `UpdateWeChatIp`（thshu/MoviePilot-Plugins，上游索引版 1.0.8） | 企业微信应用可信 IP 自动更新的本地魔改版（Cookie 惰性获取、执行日志、立即运行、IP 缓存优化）。**v5.1.6：取 IP 源全部换成 IPv4 可用源（ip.3322.net / ipv4.icanhazip.com / api-ipv4.ip.sb / ddns.oray.com），移除被重置且返回 IPv6 的 myip.ipip.net 与不可达的 4.ipw.cn，消除每 30 分钟一条取 IP 失败告警。** |
| `sitedailystatisticmodern` | 站点每日数据统计(本地版) | 4.2 | Xiang `SiteDailyStatistic` 4.0（xiangt920/MoviePilot-Plugins） | 每日汇总各 PT 站的做种/下载/分享率等数据。**本地定制：①「关注站点」多选，仅通知勾选站点；②增量基线持久化——站点缺日/采集失败时用上次有效累计值做基线；4.2 起采集失败写下的 0 值脏行不再当基线（否则恢复后当日增量=站点历史累计），并修正快照兜底读取时机；③今日无数据不再消失、累计回退记 0、23:59 补行逐站点隔离。** |
| `webdavbackupmodern` | WebDAV备份(魔改) | 2.0.3 | 官方 `WebDAVBackup` 2.0.2（InfinityPacer/MoviePilot-Plugins） | 定时把 MP V3 数据库一致性备份制品传到 WebDAV。**本地定制：新增「上传超时（秒）」配置（默认 300，最小 10），传入 webdav3 客户端的 `webdav_timeout`。** 官方版固定用该库默认 30s，慢速 WebDAV（天翼云 OpenList 实测 0.33 MB/s、26MB dump 需 82s）下每天上传必超时，异地副本会静默缺失一个月。 |

**V2 线存档**（`plugins.v2/` + `package.v2.json`）

| 插件 ID | 名称 | 版本 | 来源（定制基准） | 说明 |
| --- | --- | --- | --- | --- |
| `BrushFlow` | 站点刷流(V2 线存档) | 5.5.7 | 官方 `BrushFlow` V2 线（jxxghp/MoviePilot-Plugins，该线末版 5.2.3） | V2 线本地魔改刷流的**最终版源码存档**，含复活区、站点数据刷新、任务级限速、空间不足删种、删种条件增强、TTGL 折扣、全局动态删种双条件等特性。`system_version` 限定为 `>=2.14.6,<3.0.0`，因此在 V3 宿主上不会作为可安装候选出现；仅 V2 环境可用。 |
| `updatewechatipv2` | 动态企微可信IP(V2 定制旧版) | 5.1.5 | 书小白 `UpdateWeChatIp`（thshu/MoviePilot-Plugins，上游索引版 1.0.8） | V2 线的本地定制旧版企微可信 IP 插件（存档）。原名 `UpdateWeChatIp` 与**官方插件同名**，在 MP 里会被官方来源遮蔽而不生效，故改为独立 ID；其 V3 魔改后继版本为 `updatewechatipmodern`（当前实际运行的定制版）。 |

## 来源与 AI 修改标注

本仓库插件均为**本地定制版**。每个插件的 GitHub Release 说明里都标注了：

- **上游插件 / 上游仓库 / 上游作者**
- **定制基准**（从哪个上游版本定制而来）
- **本地定制内容**与版本说明
- **AI 修改标注**：由 **Hermes Agent（AI）** 在用户指导下修改与整理，未经上游作者审核；上游代码版权归原作者所有，本仓库仅作个人自用存档与分发

插件源码里的 `plugin_author` 同样带「（本地定制 · AI 修改）」标注。索引 `package.vN.json` 的 `description` 末尾附有一行来源摘要。

## 仓库结构

```
package.v3.json          # V3 插件索引（插件 ID → 元数据）
plugins.v3/<插件id>/      # V3 插件源码（含预构建 dist/，装完即用）
package.v2.json          # V2 插件索引（存档用）
plugins.v2/<插件id>/      # V2 插件源码存档
```

## 在 MoviePilot 里添加本仓库

1. 进入 **设置 → 插件 → 插件市场仓库地址**（对应配置项 `PLUGIN_MARKET`）
2. 在原有地址后追加本仓库地址（多个地址用英文逗号分隔；尾斜杠可有可无，MP 会归一化去重）：

   ```
   https://github.com/WUqj1998/mp-plugins/
   ```

3. 保存后回到插件市场刷新，即可看到上表中的插件。

MP 读取索引的地址为 `https://raw.githubusercontent.com/WUqj1998/mp-plugins/main/package.v3.json`（V2 线为 `package.v2.json`），插件包通过本仓库的 **GitHub Release** 资产下载（见下）。

## 发布 / 更新流程

索引条目 `release: true` 表示走 Release 安装，约定与官方一致：

- tag：`<插件id>_v<版本号>`，例如 `brushflowmodern_v6.1.2`、`BrushFlow_v5.5.7`
- 资产：`<tag 全小写>.zip`，例如 `brushflowmodern_v6.1.2.zip`、`brushflow_v5.5.7.zip`
- **zip 根目录即插件文件**（`__init__.py` 等直接位于 zip 根，不额外套一层目录）
- 同时更新对应 `package.vN.json` 里该插件的 `version` 与 `history`

推送后运行 `/opt/data/scripts/publish-mp-plugins.py` 自动建 release 并上传 zip（幂等，可反复执行）。用户侧在市场里看到新版本即可升级。

## 备注

- 本仓库为个人自用，插件针对本机环境的 PT 站点与下载器（qBittorrent）定制，不保证通用性。
- 插件代码中不含任何账号凭证；所有密钥（企微 Cookie、站点 Cookie、API Key 等）均通过 MoviePilot 的插件配置保存在本机数据库。
- **定制官方插件时必须 fork 成独立 ID**（如 `webdavbackup` → `webdavbackupmodern`）：MP V3 按插件的可信来源在启动时同步 payload，同 ID 会被在线源覆盖、改动会在重启后消失；fork 版需同时改类名/`plugin_name`/`plugin_config_prefix`/服务 ID，避免与官方版互相覆盖或服务重名。
- 直接改容器内 `/app/app/plugins/<id>/` 的源码**无效**——重启即被还原。要落地定制只能走本仓库发布流程。