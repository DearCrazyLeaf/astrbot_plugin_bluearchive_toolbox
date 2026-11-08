# ✨ BA 工具箱 | Blue Archive Toolbox (AstrBot Plugin)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-v4.29.3%2B-brightgreen)
![License](https://img.shields.io/badge/License-GPL--3.0-orange)

![views](https://count.getloli.com/get/@ba_toolbox&theme=minecraft)

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
- **生日月历与学生攻略**：按月查看学生生日和头像，或查询 Arona 上的学生攻略图片
- **每日轮询与推送**：定时生成日程、生日和千里眼卡片，推送到 `umo_groups` 配置的群聊，并按日期归档卡片和各群发送结果，方便在 Dashboard 查看；没有配置群聊时只保存归档
- **表情回应菜单**：群聊中发送菜单关键词，通过表情或回复序号/菜单项执行预先配置的固定指令
- **AstrBot 原生 WebUI**：查看功能、缓存统计、轮询记录和数据来源，并管理插件设置
- **缓存与资源管理**：缓存 SchaleDB 数据、图片和生成卡片；中文完整字体可选下载，默认使用内置字体

<img width="600" alt="亮色主题" src="docs/images/lighttheme.png" />

<img width="600" alt="暗色主题" src="docs/images/darktheme.png" />

<img width="600" alt="学生详情" src="docs/images/mika.png" />

<img width="600" alt="学生生日" src="docs/images/birthday_card.png" />

<img width="600" alt="日程表" src="docs/images/schedule_card.png" />

<img width="600" alt="千里眼" src="docs/images/pool_card.png" />

## 🧭 插件可以干什么？ | Functions & Workflow

**简单来说，`BA 工具箱` 就是一个在 AstrBot 里查 `《碧蓝档案》` 信息的小帮手**

- 你可以直接在聊天里查`学生资料`、`活动日程`、`卡池千里眼`、`生日`和`学生攻略`，也可以按`国服`、`国际服`或`日服`筛选`活动`和`卡池信息`；查到的内容会按配置用图片卡片或文字发回来

- 平时不想记太多指令，也可以用表情回应菜单快速触发预先配置好的查询；管理员还能设置定时任务，把信息卡片推送到配置的群聊，并在插件 `Dashboard` 里查看卡片和发送记录

> [!NOTE]
> 定时轮询会把生成的卡片推送到 `umo_groups` 配置的群聊，同时保存卡片和每个群的发送结果，之后可以在 `Dashboard` 查看，如果没有配置就不会推送，但是依旧会存档到后台，除非关闭轮询功能

### 一次查询会经过哪些步骤？

**大致流程如下**：

  1. 直接查询时：发出指令 -> 插件识别查询类型 -> 查询数据 -> 返回结果

  2. 定时轮询时：到达设定时间 -> 生成卡片 -> 推送到配置的群聊 -> 把卡片和发送结果归档到 Dashboard

> [!NOTE]
> 学生详情和攻略需要带上学生名称，如果没写名称，插件会提示你怎么输入，按照要求填写即可

```mermaid
flowchart TD
    U[用户发送命令] --> R[AstrBot 将消息交给 BA 工具箱]
    R --> T{识别查询类型}

    T -->|学生详情| S{是否提供学生名称}
    T -->|学生攻略| G{是否提供学生名称}
    T -->|活动或卡池| A[解析区域参数或会话默认区域]
    T -->|生日| B[读取全服生日数据]
    T -->|反应菜单| M[展示预设菜单并等待选择]

    S -->|否| P1[提示用法和名称示例]
    S -->|是| F[跨区域模糊匹配学生]
    G -->|否| P2[提示用法和名称示例]
    G -->|是| F

    F --> Z{匹配结果}
    Z -->|无匹配或数据错误| E[返回明确提示]
    Z -->|识别主体学生| C[列出该学生的全部形态]
    C -->|选择形态| Q{原始查询类型}
    C -->|其他相似主体| R2[按推荐名称重新查询]
    C -->|取消查询| X[关闭该候选菜单]

    Q -->|学生详情| D[准备学生资料并渲染详情卡]
    Q -->|学生攻略| I[向攻略图片接口查询]
    A --> H[查询活动或卡池数据]
    B --> J[生成生日列表]

    D --> O[按配置返回图片卡片或文本]
    I --> O
    H --> O
    J --> O
    M --> K[执行菜单中配置的固定命令]
    K --> R

    P[到达每日轮询时间] --> C2[生成已启用的日程、生日和千里眼卡片]
    C2 --> D2{是否配置推送群聊}
    D2 -->|是| S2[推送到 umo_groups 配置的群聊]
    D2 -->|否| A2[卡片归档到 Dashboard]
    S2 --> A2[卡片和每个群的发送结果归档到 Dashboard]
```

1. 发送查询指令，可以使用默认指令或自己配置的触发词，也可以在前面加 `/`
2. 查学生详情或攻略时，带上学生名字；只发指令的话，插件会给出输入示例
3. 插件会先读取本地缓存的 SchaleDB 学生表；同一份数据在运行期间只整理一次搜索索引，之后复用索引进行名称匹配；资料优先用国服，其次国际服，最后日服，显示名称则按语言设置选择
4. 插件先通过学生身份字段和 PathName 的主体路径归组，再识别同一主体记录中名称相对基础名新增的形态描述；已知形态别名也会辅助识别输入，形态菜单只展示主体自己的形态，匹配不确定时会要求用户确认
5. 菜单可以继续使用，点取消则立即关闭；群内成员都能点表情选择，发起查询的人也可以回复菜单输入序号或名称
6. 查活动或卡池时，可以指定服务器区域；没指定就按会话配置和默认区域查询，生日列表则是全服通用
7. 查询结果会按消息模式发送图片、文字或两者；定时轮询生成的卡片会推送到 `umo_groups` 配置的群聊，并将卡片及每个群的发送结果存档到 Dashboard

> [!NOTE]
> 即使主体名称匹配成功，插件也会列出几个相似主体作为备选；模糊度较高时会先要求你确认主体，不会直接把猜测当成最终查询结果

### 接下来想做什么？

下面这些还在规划中，目前版本还没有提供这些功能（主要看目前使用的API支不支持相关查询）

- [ ] 给不带名称的学生详情和攻略指令加一个互动向导，让你可以先选查询类型，再输入名称
- [ ] 研究 Boss 资料查询，先确认数据从哪里来、有哪些字段，再设计单独的 Boss 信息卡片
- [ ] 查清攻略图片接口是否支持 Boss 或关卡，确认支持后再考虑加入菜单
- [ ] 为互动向导补上独立会话、发起者权限、并发限制、超时、取消和异常处理，并补齐自动化测试

### 已经做好的内容

| 版本 | 做了什么 |
| --- | --- |
| `v1.0.0` | 建立学生详情、生日、活动日程、卡池千里眼和学生攻略查询能力；接入 SchaleDB、GameKee 与攻略图片服务；提供区域查询、互动菜单、定时归档、Dashboard 和资源缓存 |
| `v1.0.1` | 完善学生模糊搜索及跨区域候选优先级；增加可复用、可取消的互动候选菜单及有效期/并发配置；修复菜单路由和表情映射问题；将同步卡片渲染移至工作线程，并在启动时检测/安装 Playwright Chromium |
| `v1.0.2` | 优化学生主体与形态匹配：缓存的 SchaleDB 学生表会整理成可复用搜索索引，数据更新后自动重建；结合学生身份字段、`PathName` 主体路径及同主体基础名/形态名关系识别形态，不再只依赖固定形态词表；支持配置学生别名，并可选让大模型只对本地筛出的最多 5 个主体排序，模型异常时仍可使用原候选菜单 |

## 📥 安装 | Installation

在 AstrBot 插件市场搜索“BA 工具箱”安装，或在 AstrBot 的 `data/plugins` 目录克隆仓库：

```bash
cd /AstrBot/data/plugins
git clone https://github.com/DearCrazyLeaf/astrbot_plugin_bluearchive_toolbox
```

>[!NOTE]
>首次启动时插件会检查 Playwright Chromium；若缺失则会使用 AstrBot 当前 Python 环境中的 Playwright 自动安装匹配的浏览器版本。若渲染时仍遇到浏览器可执行文件缺失，插件还会尝试安装并重试一次；安装需要网络连接和浏览器缓存目录的写入权限；失败原因及使用当前 Python 手动安装的命令会记录在 AstrBot 后台日志中

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
| `umo_groups` | 按完整 UMO 设置会话区域，例如 `aiocqhttp:GroupMessage:123456:jp`；其中的群聊会话也作为定时轮询推送目标 |
| `menu_settings` | 回应菜单与学生候选菜单的全部设置：风格、标题/说明、时限、并发上限和表情池；菜单项支持 `label`、`command`、`face_id` |
| `student_search_settings` | 学生别名和可选的大模型辅助匹配设置 |
| `polling_settings` | 轮询开关、执行时间、服务器、类别和归档保留天数；卡片推送到 `umo_groups` 中的群聊，执行时间使用 AstrBot 主机本地时区 |
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
| 学生生日月历 | `生日`（当月）、`生日 1月`（指定月份） |
| 查询单个学生生日 | `生日 星野` |

- 学生详情与攻略支持输入部分学生名称进行模糊匹配，版本名可以放在前面或后面，例如`泳装星野`、`星野（泳装）`；不带名称时会提示用法和输入示例

- 在 `student_search_settings -> custom_aliases` 中每行填写 `别名=标准学生名`，例如 `啊露=爱露`；可以修改预置别名，也可以继续添加，别名只影响搜索，不会改动 SchaleDB 数据

- 大模型辅助匹配默认关闭；开启 `llm_enabled` 后，候选菜单会多一个可选入口。模型只会在插件从本地数据筛出的最多 5 个主体中排序，插件会校验返回名单，不会让模型直接生成学生或形态；建议使用响应快的非思考型小模型。Provider ID 留空时使用当前会话模型，超时或返回内容无效时，原候选菜单仍可继续使用

- 详情查询生成学生资料卡，攻略查询先从 SchaleDB 匹配学生，再将选中的名称交给现有攻略图片接口查询

- 详情或攻略名称匹配到多个候选时，群聊会显示互动候选菜单；群内任何人都可点击表情查询候选，查询发起者可回复对应菜单并输入序号/候选名称；选择候选后菜单继续有效，可重复选择；点击“取消查询”后关闭
- 回应菜单和学生候选菜单的配置都集中在 `menu_settings`：两种菜单共用分隔线；学生候选菜单的标题、底部说明、有效期、每群并发上限和专用表情池分别由 `student_choice_title`、`student_choice_footer_text`、`student_choice_timeout_seconds`、`student_choice_max_active_menus`、`student_choice_face_pool` 控制。说明和专用表情池留空时分别不显示、沿用通用表情池

- 学生候选菜单默认有效 120 秒、每群最多 3 个；达到上限时保留现有菜单并拒绝创建新菜单

- 学生候选会搜索缓存的国服、国际服和日服数据并按学生 ID 去重；学生表更新后索引会自动重建，正常查询会复用当前索引；主体和形态优先依据学生身份字段、PathName 主体路径及同语言基础名/形态名关系识别，预设形态别名用于补充玩家输入习惯，避免依赖一份需要不断手工补全的完整形态词表
- 资料优先使用国服记录，其次国际服，最后日服；名称和文本按 `student_data_language` 显示，缺少该语言记录时回退到可用语言

- 活动和卡池的区域参数支持 `cn`、`global`、`jp`、`all`

- 未指定区域时，插件优先使用当前会话在 `umo_groups` 中配置的区域，再使用 `server_region`

- 参数 `all` 会分别查询三个服务器并标注来源

- 生日数据为全服通用，不接受区域参数；`生日` 默认展示当月月历，`生日 <月份>` 展示指定月份，`生日 <学生名>` 返回该学生的文字生日信息

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

- **卡片渲染失败**：确认 AstrBot 运行环境可访问网络并能写入 Playwright 浏览器缓存目录。插件会用当前 Playwright 包所需的 revision 自动安装 Chromium；不要手动替换 revision 目录。若需手动安装，请使用日志提供的命令，确保使用 AstrBot 实际运行的同一个 Python 环境
- **查询数据失败**：确认运行环境可访问相应数据源；需要镜像时，在插件配置中填写 `api_base_url` 或 `schaledb_base_url`
- **学生名称未匹配**：详情和攻略查询会跨国服、国际服和日服数据搜索；可输入部分名称，或尝试版本名，例如 `日富美（赛车）` 出现多个候选时，群内任何人都可点击候选表情查询；发起者也可回复对应候选菜单输入序号；每群并发上限和过期时间可在 `menu_settings` 中配置
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

- 学生候选跨 SchaleDB 国服、国际服和日服数据搜索并按 ID 去重；学生资料按国服、国际服、日服顺序回退，显示语言按插件配置优先，并回退到该学生实际可用的语言记录

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
