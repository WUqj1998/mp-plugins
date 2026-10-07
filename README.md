# MoviePilot 本地定制插件仓库

自用的 MoviePilot **V3** 插件仓库（与官方仓库结构一致），存放本地定制/魔改插件，供 MoviePilot 通过插件市场直接安装与升级。

## 插件清单

| 插件 ID | 名称 | 版本 | 说明 |
| --- | --- | --- | --- |
| `brushflowmodern` | 站点刷流(本地版) | 6.1.2 | 以官方 V3 `brushflow` 6.1.2 为基线的**独立本地版**（独立 ID，避免被在线源覆盖）。含本地魔改特性：复活区、站点数据自动刷新、站点下载量控制、任务级限速、空间不足删种、删种条件增强（老种下限/只删已完成/最短做种时间/无活跃跳过）、TTGL 积分商店折扣。 |
| `updatewechatipmodern` | 动态企微可信IP(魔改) | 5.1.5 | 企业微信应用可信 IP 自动更新的本地魔改版（Cookie 惰性获取、执行日志、立即运行、IP 缓存优化）。 |
| `sitedailystatisticmodern` | 站点每日数据统计(本地版) | 4.0 | 每日汇总各 PT 站的做种/下载/分享率等数据。 |
| `siteuserdatasync` | 站点数据同步 | 1.0.0 | 将站点用户数据同步给其它插件/服务使用。 |
| `UpdateWeChatIp` | 动态企微可信IP(V2 遗留) | 5.1.5 | ⚠️ **仅作存档**。其 ID 经规范化后与官方内置插件 `updatewechatip` 同名，V3 下会被官方来源遮蔽而不生效，请改用 `updatewechatipmodern`。 |

## 仓库结构

```
package.v3.json          # V3 插件索引（插件 ID → 元数据）
plugins.v3/<插件id>/      # 各插件源码（含预构建 dist/，装完即用）
```

## 在 MoviePilot 里添加本仓库

1. 进入 **设置 → 插件 → 插件市场仓库地址**（对应配置项 `PLUGIN_MARKET`）
2. 在原有地址后追加本仓库地址（多个地址用英文逗号分隔，**地址不加结尾斜杠**）：

   ```
   https://github.com/WUqj1998/mp-plugins
   ```

3. 保存后回到插件市场刷新，即可看到上表中的插件。

MP 读取索引的地址为 `https://raw.githubusercontent.com/WUqj1998/mp-plugins/main/package.v3.json`，插件包通过本仓库的 **GitHub Release** 资产下载（见下）。

## 发布 / 更新流程

索引条目 `release: true` 表示走 Release 安装，约定与官方一致：

- tag：`<插件id>_v<版本号>`，例如 `brushflowmodern_v6.1.2`
- 资产：`<tag 全小写>.zip`，例如 `brushflowmodern_v6.1.2.zip`
- **zip 根目录即插件文件**（`__init__.py` 等直接位于 zip 根，不额外套一层目录）
- 同时更新 `package.v3.json` 里该插件的 `version` 与 `history`

用户侧升级：插件市场里该插件出现新版本即可一键升级。

## 备注

- 本仓库为个人自用，插件针对本机环境的 PT 站点与下载器（qBittorrent）定制，不保证通用性。
- 插件代码中不含任何账号凭证；所有密钥（企微 Cookie、站点 Cookie、API Key 等）均通过 MoviePilot 的插件配置保存在本机数据库。
