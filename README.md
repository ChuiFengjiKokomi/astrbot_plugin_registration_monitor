# 赛事报名监控

AstrBot 学校报名监控插件。定时读取兼容的 JSON 接口，发现新增或取消报名后向配置的QQ群发送变化学校、目标学校和关注位次的学校排名，支持手动查询和播报。

赛事名称、数据接口、消息来源链接、目标学校、QQ群和关注位次都可在插件设置中修改。默认不绑定任何站点、学校或群。

> **适用范围：** 学校报名名单及网络赛排名，接口须符合下文的数据格式。不是任意网页抓取工具，也不会自动识别不同站点的字段。换站点后若数据结构不同，需要修改 `ranking.py` 的 `parse_payload()` 或提供格式转换接口。当前仅支持 OneBot v11 的QQ群。

## 链接修改位置

所有需要替换的链接都按编号标注，可在项目中搜索 `LINK-`。

| 标记 | 修改位置 | 填什么 | 何时修改 |
| --- | --- | --- | --- |
| **LINK-1** | AstrBot 插件设置 → `api_url` | 实际返回 JSON 的 HTTP/HTTPS 数据接口 | 安装后必填 |
| **LINK-2** | AstrBot 插件设置 → `page_url` | 给用户查看的报名网页，显示在播报底部 | 可选，留空隐藏来源 |
| **LINK-3** | `metadata.yaml` → `repo` | GitHub 仓库地址 | 发布到自己的仓库时修改 |

`api_url` 用来请求数据，`page_url` 只用来展示来源。二者独立配置，更换接口时请同时核对来源页面，避免消息显示旧站点链接。

若想发布一个默认指向指定站点的版本，只需修改 `_conf_schema.json` 中 `api_url.default` 和 `page_url.default`；默认赛事名称可改 `event_name.default`。使用者仍可通过 WebUI 覆盖这些值。`main.py` 的 `LINK-1`、`LINK-2` 注释标出了实际读取位置，无需改 Python 代码。

配置示例，以下 `example.org` 地址仅为占位，须替换：

```json
{
  "event_name": "ICPC 示例站",
  "api_url": "https://example.org/registration/api.json",
  "page_url": "https://example.org/registration/",
  "target_school": "示例大学",
  "group_ids": ["123456789"],
  "cutoff_position": 95
}
```

这是设置示例，不是独立配置文件；实际使用时在 AstrBot 插件设置中填写。配置结构的定义在 `_conf_schema.json`，不要将该文件整体替换为上面的示例。

## 发布到 GitHub

当前仓库的作者和仓库地址已配置为 `ChuiFengjiKokomi/astrbot_plugin_registration_monitor`。以下步骤适用于将源码发布到自己的新仓库。

1. 新建仓库，建议命名 `astrbot_plugin_registration_monitor`。
2. 修改 `metadata.yaml` 的 `author`（`PUBLISH-1`）和 `repo`（`LINK-3`）。例如仓库属于用户 `alice`，则填 `author: alice`，`repo: "https://github.com/alice/astrbot_plugin_registration_monitor"`。若更改插件名，同步修改 `metadata.yaml` 的 `name` 与本文安装目录。
3. `LICENSE` 使用 MIT 许可。衍生版本可补充自己的版权署名，并须保留已有版权及许可声明。
4. 将**本目录内的文件**作为仓库根目录内容提交，保证 `main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt`、`ranking.py` 和 `monitor.py` 直接位于仓库根目录。保留 `.gitignore`、测试和 `.github/workflows/tests.yml`。
5. 运行下方验证命令。仓库内附 GitHub Actions 测试配置，推送后会在 Python 3.10 和 3.12 上执行测试及格式检查。

作者和仓库地址的修改位置分别以 `PUBLISH-1`、`LINK-3` 标注。接口和来源链接默认留空，发布通用版时无需预设它们。QQ群、平台 ID 和目标学校请由安装者在运行时填写。

发布目录不包含原香港站版本的更新脚本、虚拟环境、缓存或实时抓取数据。`dist/` 为生成产物，不应提交到源码仓库。

## 安装

要求 AstrBot `>=4.9.2,<5`，接入 OneBot v11（例如 NapCat、Lagrange）。机器人须已加入接收群并有发送权限。与 AstrBot 一起部署时使用其 Python 环境和依赖管理方式。

- GitHub 安装：在 AstrBot WebUI 的插件管理中使用你在 **LINK-3** 填写的实际仓库链接安装。
- 本地安装：将源码放入 `data/plugins/astrbot_plugin_registration_monitor/`，安装 `requirements.txt` 的依赖后重载插件。
- ZIP 安装：将本目录中的运行文件打包到 ZIP 根目录，使用 WebUI 的本地插件上传功能安装。

随后在 WebUI → 插件 → **赛事报名监控** → 设置中填写 **LINK-1** 和接收群。保存设置后重载插件。

此通用版使用独立插件名称，原香港站版本的监控记录不会自动迁移。若在同一个机器人上监控相同站点和群，启用本版前先关闭原版的自动监控，避免两份插件分别播报相同变化。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 自动监控开关，关闭后仍可手动查询和播报 |
| `event_name` | `赛事` | 消息标题中的赛事名称 |
| `api_url` | 空 | **LINK-1**，必填，返回兼容 JSON 的数据接口 |
| `page_url` | 空 | **LINK-2**，可选，消息底部来源链接 |
| `target_school` | 空 | 完整中文校名、英文校名或学校 ID |
| `group_ids` | `[]` | 接收QQ群号列表，每项一个纯数字群号 |
| `platform_id` | 空 | 单个 OneBot v11 连接时自动识别；多个时填写平台实例 ID |
| `check_interval_seconds` | `300` | 每轮结束后等待的秒数，最低60秒 |
| `cutoff_position` | `95` | 按网络赛排名排序后的关注位次 |
| `notify_on_first_run` | `false` | 首次是否把现有名单作为新增播报；默认仅建立基线 |
| `request_timeout` | `60` | HTTP 请求超时秒数，范围5至120 |

未填群号时自动监控等待配置，不建立基线。不填目标学校仍可监控变化。不填或填错接口时查询返回设置提示，自动检测保留原记录并在下一轮重试。

## 命令

| 命令 | 权限 | 行为 |
| --- | --- | --- |
| `/报名排名` | 所有用户 | 在当前会话预览目标学校和关注位次的学校 |
| `/报名播报` | AstrBot 管理员 | 向设置中的全部QQ群发送当前摘要 |
| `/报名状态` | AstrBot 管理员 | 查看基线、最近检查、待发队列和异常 |

AstrBot 管理员以机器人设置为准，QQ群管理员身份不自动授予跨群播报权限。手动播报不更改自动去重记录，失败群会列在命令回复中。

## 接口格式

```json
{
  "success": true,
  "data": [
    {
      "id": "school-001",
      "nameZh": "示例大学",
      "nameEn": "Example University",
      "rank": 12
    }
  ],
  "timestamp": 1792932019000
}
```

- `success` 必须是布尔值 `true`；`data` 必须是非空数组。
- `id` 必须是稳定唯一的非空字符串或整数。不同轮次不可重新编号，否则会误判新增和取消。
- `nameZh`、`nameEn` 至少一项为非空字符串。播报优先中文，否则使用英文。
- `rank` 为正整数或正整数字符串；缺失、`null`、空字符串、`0` 表示暂无排名。布尔值、负数和其他文本会被拒绝。
- `timestamp` 可省略，为 Unix **毫秒**时间戳，消息按北京时间（UTC+8）显示。省略时显示接口未提供更新时间。

插件只认识这个结构。HTML 页面、需要登录或自定义认证头的接口、使用其他字段名的接口，需要先提供适配或修改代码；当前配置仅提供 GET 地址。

## 监控规则

- 报名位次按网络赛排名升序从1计数，同名次沿用接口顺序；无排名的学校排最后。数据范围完全取决于配置接口，不预设地区或学校范围。
- 目标学校按完整中英文名匹配，忽略大小写、空白和全半角差异，也可填学校 ID；不做模糊匹配。未报名学校的网络赛排名无法从名单推断。
- 首次成功检测默认只建基线。后续发现历史未见过的 ID 时通知新增；与上次成功名单相比消失的 ID 通知取消，使用上次保存的排名。
- 曾经出现过的 ID 再次出现不重报新增；重新出现后再次消失仍会通知取消。同一轮新增与取消可合并，每10所变化学校分一条消息，每条附目标学校、关注位次及可选来源链接。
- 仅名称、排名或关注位次变化不自动发消息，可手动查询。名单不足关注位次时会显示暂无对应学校。
- 基线、历史 ID 和待发队列存入 AstrBot 插件 KV 数据，重启或重载后保留。修改学校、群号、标题、来源页面不会清空基线。已排队消息保留入队时的文案。
- **更改 `api_url` 会在下一次有效监控轮次重新建基线，并丢弃旧接口的待发消息。** 如果接口内容换了赛事但 URL 没变，请先在 AstrBot 中备份并清理本插件 KV 数据，再启用新赛事，避免沿用旧基线。
- HTTP 失败、无效 JSON、空名单或重复 ID 不更新成功基线。空名单会被视为异常，不会把全部学校误报为取消。
- 自动发送失败按群持久化重试，成功群不重复发送。移除群号会丢弃该群待发消息。平台发送与本地存储无法构成原子事务，在发送成功而保存记录前中断时，恢复后可能补发一次。
- 使用标准 HTTP/HTTPS 请求及默认证书校验，不读取系统代理。运行 AstrBot 的主机需能直接访问配置的接口。

## 开发验证

在仓库根目录运行：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python scripts/build_release.py
```

测试使用合成学校数据和本地 HTTP 服务，不请求真实报名站点；覆盖解析、关注位次、批次、取消检测、重启去重、多群重试、存储失败和通用配置。AstrBot 与真实QQ群发送需安装后配置并通过 `/报名播报` 验证。

打包脚本生成 `dist/astrbot_plugin_registration_monitor.zip`，源码文件直接位于 ZIP 根目录，包含测试及 GitHub 工作流，可用于本地插件安装，也可解压后作为 GitHub 仓库内容。脚本只收录明确列出的源码文件，不包含缓存或虚拟环境。修改发布署名和仓库链接后，请重新运行脚本生成对应版本的 ZIP。

## 许可

MIT，见 `LICENSE`。
