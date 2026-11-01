# ✨ BA 工具箱 | Blue Archive Toolbox (AstrBot Plugin)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-v4.29.3%2B-brightgreen)
![License](https://img.shields.io/badge/License-GPL--3.0-orange)
![views](https://count.getloli.com/get/@astrbot_plugin_bluearchive_toolbox?theme=booru-jaypee)

## ✅ 简介 | Introduction

<table>
  <tr>
    <td width="190" align="center">
      <img width="160" alt="BA 工具箱 Logo" src="logo.png" />
    </td>
    <td>
      <strong>碧蓝档案活动 / 千里眼 / 攻略查询工具箱</strong><br />
      学生数据查询 · 游戏信息卡片 · 每日轮询归档 · AstrBot 原生 WebUI<br />
      <sub>Blue Archive data lookup, card rendering, daily archives, and native AstrBot WebUI.</sub>
    </td>
  </tr>
</table>

**BA 工具箱**是一个为 AstrBot 开发的《碧蓝档案》信息查询与卡片渲染插件
插件整合学生静态数据、运营活动信息和攻略图片，并提供可视化设置、群聊表情回应菜单及每日轮询记录

[![Release](https://img.shields.io/github/v/release/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox?include_prereleases&color=blueviolet&label=最新版本)](https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox/releases/latest)
[![License](https://img.shields.io/badge/许可证-GPL%203.0-orange)](https://www.gnu.org/licenses/gpl-3.0.txt)
[![Issues](https://img.shields.io/github/issues/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox?color=darkgreen&label=反馈)](https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox/issues)
[![Pull Requests](https://img.shields.io/github/issues-pr/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox?color=blue&label=请求)](https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox/pulls)
[![QQ Group](https://img.shields.io/badge/点击加入QQ群-12B7F5?logo=tencentqq&logoColor=white&label=问题反馈和建议)](https://qm.qq.com/q/H3uUCEZKgg)
[![GitHub Stars](https://img.shields.io/github/stars/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox?color=yellow&label=标星)](https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox/stargazers)

## ✅ 功能列表 | Features

- **学生详情卡片**：查询学生资料、技能、属性、装备、适应性和立绘
- **活动日程与卡池千里眼**：查询国服、国际服、日服或全服排期，支持卡片和文本回复
- **生日列表与学生攻略**：展示全服学生生日，或查询 Arona 上的学生攻略图片
- **每日轮询归档**：定时生成日程、生日和千里眼卡片，按日期保存并在 Dashboard 查看；只保存记录，不会主动向群聊推送
- **表情回应菜单**：群聊中发送菜单关键词，通过表情或回复序号/菜单项执行预先配置的固定指令
- **AstrBot 原生 WebUI**：查看功能、缓存统计、轮询记录和数据来源，并管理插件设置
- **缓存与资源管理**：缓存 SchaleDB 数据、图片和生成卡片；中文完整字体可选下载，默认使用内置字体

<img width="600" alt="亮色主题" src="docs/images/lighttheme.png" />

<img width="600" alt="暗色主题" src="docs/images/darktheme.png" />

<img width="600" alt="日富美详情1" src="docs/images/hifumi_1.png" />

<img width="600" alt="日富美详情2" src="docs/images/hifumi_2.png" />

<img width="600" alt="学生生日" src="docs/images/birthday.png" />

<img width="600" alt="日程表" src="docs/images/schedule.png" />

<img width="600" alt="千里眼" src="docs/images/pool.png" />

## 📥 安装 | Installation

在 AstrBot 插件市场搜索“BA 工具箱”安装，或在 AstrBot 的 `data/plugins` 目录克隆仓库：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox
```

- 卡片渲染需要 Playwright Chromium

- 若当前 Python 环境没有 Chromium，请运行：

```bash
python -m playwright install chromium
```

## ⚙️ 配置 | Configuration

配置入口：

```text
AstrBot WebUI -> 插件 -> BA 工具箱 -> 插件配置
```

插件 Dashboard 中也可编辑、保存本插件设置

| 配置项 | 说明 |
| --- | --- |
| `message_mode` | 回复方式：`card`（默认）、`text` 或 `mixed` |
| `server_region` | 活动/卡池默认区域：`cn`、`global`、`jp` 或 `all` |
| `student_data_language` | 学生详情及生日列表的名称/文本语言；留空时按会话区域或默认语言处理 |
| `umo_groups` | 按完整 UMO 设置会话区域，例如 `aiocqhttp:GroupMessage:123456:jp` |
| `menu_settings` | 菜单关键词、有效期、防抖、表情池和菜单项；菜单项支持 `label`、`command`、`face_id` |
| `polling_settings` | 轮询开关、执行时间、服务器、类别和归档保留天数；执行时间使用 AstrBot 主机本地时区 |
| `cache_settings` | 控制生成卡片图片缓存的数量、容量和过期时间；SchaleDB 缓存单独管理 |
| `download_full_font` | 是否下载完整中文字体；默认关闭，使用随插件附带的精简字体 |
| `schaledb_base_url` | SchaleDB 兼容镜像根地址；留空使用默认站点 |
| `api_base_url` | GameKee 兼容镜像根地址；留空使用默认站点 |

- 默认每日 08:00 轮询全服日程、生日和千里眼，保留 30 天

- 轮询记录和卡片存放在 AstrBot 分配给插件的数据目录中，与普通卡片缓存分开

## 📖 使用说明 | Usage

普通查询指令可直接发送，也支持 `/` 前缀

默认触发词可在插件配置中修改

| 功能 | 命令示例 |
| --- | --- |
| 学生详情 | `学生 日富美`、`学生 日富美（赛车）` |
| 学生攻略 | `攻略 日富美`、`攻略 日富美（赛车）` |
| 活动日程 | `活动`、`活动 jp`、`活动 all` |
| 卡池千里眼 | `卡池`、`卡池 global`、`卡池 all` |
| 全服生日 | `生日` |

- 学生详情与攻略都必须提供学生名称

- 详情查询生成学生资料卡，攻略查询获取 Arona 图片；名称缺失时会显示用法

- 若详情名称匹配多个学生，插件会列出候选项，请提供更完整的版本名

- 活动和卡池的区域参数支持 `cn`、`global`、`jp`、`all`

- 未指定区域时，插件优先使用当前会话在 `umo_groups` 中配置的区域，再使用 `server_region`

- 参数 `all` 会分别查询三个服务器并标注来源

- 生日数据为全服通用，不接受区域参数

---

### 表情回应菜单

在群聊发送配置的菜单关键词（默认 `菜单` 或 `ba菜单`）打开菜单

- 菜单项执行固定命令，不支持点击后继续输入参数；学生详情/攻略菜单项应填写完整指令，例如：

```text
学生详情 | 学生 日富美
学生攻略 | 攻略 日富美（赛车）
```

- 菜单项可配置 `face_id` 覆盖自动分配的表情 ID

- 每条菜单指令在配置时即确定，不会自动选择“最新攻略集”或按当前服务器追加查询参数

## 🛠️ 问题排查 | Troubleshooting

- **卡片渲染失败**：确认 AstrBot 使用的 Python 环境已安装插件依赖及 Playwright Chromium；可执行 `python -m playwright install chromium` 安装浏览器
- **查询数据失败**：确认运行环境可访问相应数据源；需要镜像时，在插件配置中填写 `api_base_url` 或 `schaledb_base_url`
- **学生名称未匹配**：详情查询会先查找最新日服数据，再回退到所选区域；尝试使用完整名称或版本名，例如 `日富美（赛车）`
- **中文字符显示不全**：默认使用精简字体；可启用 `download_full_font` 让插件尝试下载完整字体；无法访问上游时可关闭该选项并使用内置字体

## 🤝 开发构建 | Development  

本地离线测试：

```bash
python -m unittest discover -s tests
```

- 目录 `tests/` 包含单元测试；`test/render_test.py` 是手动真实 API 出图脚本，需要可用网络和 Playwright Chromium，生成图片及临时缓存位于 `test/output/`

## ❤️ 贡献者 | Contributors

<a href="https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox&max=300&columns=15" alt="Contributors" />
</a>

## 🙏 致谢 | Acknowledgements

### 数据来源

| 数据 | 来源 |
| --- | --- |
| 学生资料、生日及学生资源图片 | [SchaleDB](https://schaledb.com/) |
| 活动日程与卡池 | [GameKee](https://www.gamekee.com/) |
| 学生攻略图片 | [Arona](https://arona.diyigemt.com/) |

- 学生详情优先使用 SchaleDB 最新日服记录，再按配置语言合并名称与文本；若日服尚无该学生，则回退到所选区域数据

- 学生立绘、头像、武器和物品图标按需下载并缓存

- GameKee 和 Arona 查询结果不会在插件内持久缓存

---

### 素材与灵感

- [BlueArchiveUI · ywhdzrb](https://github.com/ywhdzrb/BlueArchiveUI)：参考第三方 Qt6 UI 组件与视觉风格
- [奇怪的蔚蓝档案资料库 · RedBeanN](https://github.com/RedBeanN/BlueArchive)：参考游戏数据资源，并提供完整中文字体下载来源
- [蔚蓝档案攻略查询 · nicocatxzc](https://github.com/nicocatxzc/astrbot-plugin-BlueArchive)：指令式攻略、活动与卡池查询组织，以及 GameKee 图文获取和失败回退思路
- [学生生日提醒 · laopanmemz](https://github.com/laopanmemz/astrbot_plugin_babirthday)：生日列表、学生数据更新和生日提醒交互设计灵感
- [攻略查询 · anka](https://github.com/anka-afk/astrbot_plugin_blue_archive)：Arona 攻略图片查询及基于资源哈希复用本地图片缓存的思路
- [活动日历 · AyaseNIA](https://github.com/AyaseWNA/astrbot_plugin_blue_archive_auto_calendar)：多服务器活动日历、会话区域选择和定时任务设计灵感

> [!IMPORTANT]
> Dashboard 欢迎卡片使用透明动画 WebP 展示阿洛娜与普拉娜，不加载 Spine runtime、场景背景模型或 WebGL
> 角色模型素材参考 Steam 创意工坊的[原始模型](https://steamcommunity.com/sharedfiles/filedetails/?id=2959252463)及[中文版本](https://steamcommunity.com/sharedfiles/filedetails/?id=3655252545)
> 游戏及角色相关权利归各自权利人所有；本项目为非商业同人项目

---

> Made for AstrBot ❤️
