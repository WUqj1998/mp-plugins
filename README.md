# MoviePilot 本地定制插件仓库

自用的 MoviePilot 插件仓库（与官方仓库结构一致），存放本地定制/魔改插件，供 MoviePilot 通过插件市场直接安装与升级。

## 插件清单

**V3 线**（`plugins.v3/` + `package.v3.json`）

| 插件 ID | 名称 | 版本 | 来源（定制基准） | 说明 |
| --- | --- | --- | --- | --- |
| `brushflowmodern` | 站点刷流(本地版) | 6.1.2 | 官方 `BrushFlow` V3 6.1.2（jxxghp/MoviePilot-Plugins） | 以官方 V3 6.1.2 为基线的**独立本地版**（独立 ID，避免被在线源覆盖）。含本地魔改特性：复活区、站点数据自动刷新、站点下载量控制、任务级限速、空间不足删种、删种条件增强（老种下限/只删已完成/最短做种时间/无活跃跳过）、TTGL 积分商店折扣。 |
| `updatewechatipmodern` | 动态企微可信IP(魔改) | 5.1.5 | 书小白 `UpdateWeChatIp`（thshu/MoviePilot-Plugins，上游索引版 1.0.8） | 企业微信应用可信 IP 自动更新的本地魔改版（Cookie 惰性获取、执行日志、立即运行、IP 缓存优化）。 |
| `sitedailystatisticmodern` | 站点每日数据统计(本地版) | 4.0 | Xiang `SiteDailyStatistic` 4.0（xiangt920/MoviePilot-Plugins） | 每日汇总各 PT 站的做种/下载/分享率等数据。**本地定制：新增「关注站点」配置（多选），仅通知/推送勾选站点的当日数据，留空则推送全部站点。** |

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
