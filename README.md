# Agent Display Codex · 智能体实时可视化

把 Codex 的真实执行记录接到 Agent Display 战术大屏。保留原作者十款主题及切换菜单，先适配 **06 视界熔炉 FORGE** 和 **07 天工开物 WORKS**；其余八款继续展示作者原版演示。

这是基于 [cyberdaddyleo/agent-display](https://github.com/cyberdaddyleo/agent-display) 的下游适配项目。原版视觉、模型与动画由赛博老爸创作；本项目新增本机只读连接和两款主题的数据映射。详见 [来源与许可说明](NOTICE.md)。

## 可以展示什么

- 一个明确选定的 Codex 对话：轮次、执行状态、工具调用、返回与失败。
- 单智能体的多个真实工作项、公开计划及可识别的子智能体事件；不会把 MCP 工具类别伪装成多个智能体。
- 已记录的代码、命令与文件修改片段。只展示日志里已有的内容，不打开相关源文件，也不执行其中的代码。
- 日志提供的输入 / 输出 Token、缓存命中及模型信息；缺失数据保留未知状态。
- 当前任务、交付记录、时间线、内存与连接状态。
- 全屏、关闭看板、清理历史缓存。清理只影响看板缓存，不删除对话或原始记录。

原版动画保留，但装饰动画与真实完成状态分开：模型旋转、形态变化和光流不代表模型内部神经活动；只有记录里可确认的事件才进入真实任务面板。

## 环境要求

- Python **3.10 或更新版本**，服务只使用标准库，无需 `pip install`。
- 支持 WebGL 的现代浏览器；集显也可以运行，流畅程度取决于设备。
- 本机可读取的 Codex JSONL 执行记录。
- Node.js 仅用于开发验证，日常启动不需要。

## Windows 快速启动

1. 下载本仓库 ZIP 并解压，或克隆后进入项目文件夹。
2. 在 PowerShell 中运行：

```powershell
py -3 configure.py
```

按提示填入**要连接的那一个对话**的执行记录完整路径，以及看板名称。通常记录位于用户目录的 `.codex\sessions\年\月\日\` 中，文件名为 `rollout-...jsonl`。请根据目标对话和记录元信息确认文件，不要随便选择最近一个文件。程序只读取你明确选定的文件，不扫描全部聊天。

3. 双击 `start.cmd`，或在 PowerShell 中运行：

```powershell
.\start.cmd
```

默认打开 [视界熔炉](http://127.0.0.1:8773/#forge)。按页面菜单或 `T` 键切换主题；[天工开物](http://127.0.0.1:8773/#works) 也使用同一条只读连接。主题名称和交互以原版页面为准。

停止服务：在启动它的终端按 **Ctrl+C**，或使用看板里的“关闭看板”；随后关闭网页可释放页面渲染资源。只关闭终端后，已经加载的装饰动画仍可能继续播放。

## 手动启动与更换对话

```powershell
py -3 configure.py --rollout "D:\my-codex-logs\rollout-selected.jsonl" --title "我的任务"
py -3 build_live.py
py -3 server.py --open
```

记录元信息通常会自动提供对话 ID。无法识别时，使用 `--thread-id` 明确指定；如果与日志元信息不符，程序拒绝配置。配置写入被 Git 忽略的 `config.local.json`，不要上传此文件或实际聊天记录。

更换对话时先停止服务，再重新配置：

```powershell
py -3 configure.py --rollout "D:\my-codex-logs\rollout-other.jsonl" --title "另一个任务" --force
py -3 server.py --port 8774 --open
```

macOS / Linux 使用相同 Python 命令，将 `py -3` 换成 `python3`。Windows 没有 `py` 时可换成 `python`。

## 数据边界与资源

服务只监听 `127.0.0.1`，不提供公网访问。它只读所选日志；不会修改原任务、提示词或执行流程，不调用额外模型，不产生额外模型 Token 用量。原本 Codex 任务的模型用量仍按其自身规则计算。

默认读取记录末尾 8 MiB，最多保留 4 轮、每轮 160 步。必要时只在同一文件末尾最多回读 32 MiB 的公开元信息；代码缓存上限 512 KiB。只加载部分历史，因此无法保证完整重建一个很长的对话。

两款适配主题共享数据轮询：页面前台约 1 秒、后台约 5 秒。内存面板区分采集器常驻内存与浏览器 JS 估计，不包含整个浏览器、显卡或整机占用；实际资源开销受分辨率、主题、设备和任务记录影响。

代码与日志展示会尝试隐藏常见凭据和内部推理块，但自动脱敏无法识别所有敏感业务信息。请仅在自己的电脑上使用；提交问题或分享截图前自行检查内容。本仓库只包含程序、原作者素材与合成测试，不包含开发者真实聊天。

## 验证与开发

```powershell
py -3 build_live.py
py -3 -m unittest discover -v
node verify_build.cjs
node test_forge_mapping.cjs
node test_work_items.cjs
node test_theme_contracts.cjs
```

`build_live.py` 保留上游快照并生成 `web/index.html` 与构建清单，校验原版绘制函数和未适配主题没有改变。测试使用临时合成记录，不连接真实对话。

目录：`observer.py` / `live_observer.py` 负责记录读取和整理，`server.py` 提供本机接口，`web/forge-live.js` 负责主题映射，`upstream/agent-display/` 保存作者原版。

## 常见问题

**页面没变化？** 确认打开 06 / 07、配置选择了正确文件，而且该对话正在产生新记录。看板不能从记录中还原未公开的内部思考；工具间隙没有新事件是正常的。

**原版演示比真实数据丰富？** 原版使用编排好的示例任务和多智能体剧情。真实日志缺少的角色、代码、用量或交付不会凭空补造，装饰动画可继续运行。

**端口被占用？** 用 `--port` 换一个端口，不要停止来源不明的进程。

**离线能用吗？** Python 服务可本机运行，但原版 Three.js 和字体来自 CDN，首次加载需要网络。本项目尚未做完整离线资源打包。

**许可是什么？** [PolyForm Noncommercial 1.0.0](LICENSE)，保留原作者 Required Notice。商业使用须另行确认授权。
