# AgentC（预览版）

[English](README.md) | 简体中文

AgentC 是一个用 Python 编写的跨平台 Agent 插件编译器。它可以读取你指定的插件源码目录，为 Cursor、Codex 和 Claude Code 生成对应的产物，并将打包后的插件安装到你指定的本地目录。

当前版本是 **预览版**（`0.1.0a0`），不是 1.0 正式版。项目采用 [Apache-2.0 许可证](LICENSE)。本仓库不包含内部宿主插件。各阶段的开发记录见 [history.md](history.md)。

## 从现有插件目录开始

如果你已经在 Cursor、Codex 或 Claude Code 的工作区中维护插件，**直接把那个插件目录交给 AgentC**。无需先运行 `agentc init`，也无需把文件搬到另一个“编译器项目”。除源码根目录下的 `.git/` 外，AgentC 会按该目录中的内容进行编译；编译失败时，应修改原目录中的文件后重试。

### 1. 安装 CLI

需要 Python 3.10 或更新版本。在克隆本仓库后，从仓库根目录执行：

```bash
python3 -m pip install .
agentc doctor --format protocol
```

`doctor` 用于确认命令及本机宿主工具的状态。安装下面的 Skill **不会**自动安装 CLI。尚未安装本仓库时，若直接运行 `python3 -m agentc`，需要先把 `src:scripts` 放进 `PYTHONPATH`。

### 2. 编译你的插件目录

例如，编译当前工作区中的 `my-plugin`：

```bash
agentc workspace build ./my-plugin --targets cursor,codex,claude-code --format protocol
```

`./my-plugin` 是插件源码目录，不是应用仓库的根目录。`--targets` 指定目标宿主；只需要 Cursor 时可写成 `--targets cursor`。命令会返回机器可读的协议结果，并生成 `workspace-build-report.json`。请以协议结果、报告中的诊断和产物路径为准，不要靠解析文本摘要判断成功与否。

如果编译失败，报告会指出原目录中的路径。本次失败不会产生可安装的新 ZIP；修改原文件后，再运行同一条命令。

### 3. 安装或更新到指定目录

编译成功后，使用本轮生成的对应宿主 ZIP。写入前，先用相同的 `--kind` 和 `--root` 查看安装状态：

```bash
agentc status --kind codex --name NAME --root /chosen/codex/root --format protocol
```

未注册时安装；已注册且没有 `AGC-P5-0008` 文件漂移时，使用 `sync` 更新：

```bash
agentc install codex --from /path/to/codex.zip --root /chosen/codex/root --yes --format protocol
agentc sync codex --from /path/to/codex.zip --root /chosen/codex/root --yes --format protocol
```

这两条命令是**按状态二选一**，不要连续执行。操作后再次运行上面的 `status`。如报告 `AGC-P5-0008`，说明已安装文件被修改，应先处理差异，不要直接安装或同步。Codex 和 Claude Code 必须指定 `--root`；Cursor 可以省略，默认安装根目录为 `~/.cursor/plugins/local`。新安装或更新后，还应在对应宿主中确认插件已被加载。

### 4. 让聊天 Agent 调用

仓库提供一份共用的 [AgentC workspace Skill](integrations/agentc-workspace/skills/agentc-workspace/SKILL.md)。它会指导 Cursor、Codex 或 Claude Code 中的 Agent 调用 `agentc doctor`、`workspace build`、`status`、`install` 和 `sync`。三端使用同一份 Skill 正文，但各端的插件清单和安装目录可能不同。

先安装 CLI，再从仓库根目录构建并打包该 Skill：

```bash
agentc build integrations/agentc-workspace
agentc package integrations/agentc-workspace
```

然后安装你所使用宿主的 ZIP。以下以 Codex 为例；其他宿主的完整命令见 [英文 README 的 Quick start](README.md#quick-start)：

```bash
agentc install codex \
  --from integrations/agentc-workspace/build/dist/codex/agentc-workspace.zip \
  --root /chosen/codex/root --yes --format protocol
agentc status --kind codex --name agentc-workspace \
  --root /chosen/codex/root --format protocol
```

如果宿主校验器或 `claude` 命令缺失，三端构建可能返回 `AGC-P2-0003`。应根据诊断准备相应宿主环境；ZIP 文件存在也不代表宿主已经加载了 Skill。

## 从零创建 Agent 项目

`agentc init` 创建的是 **AgentC 的可移植源码项目**，不是直接安装到 Cursor、Codex 或 Claude Code 的插件。`agentc create skill` 在该项目中添加一个 Skill 模板。已有插件目录的用户可以跳过这一节，直接使用 `workspace build`。

```bash
agentc init ./my-agent --id acme --name Acme --targets cursor
agentc create skill hello --project ./my-agent
agentc validate ./my-agent
agentc build ./my-agent
agentc package ./my-agent
```

你也可以在 AgentC 项目中创建 Rule 或使用 `agentc build DIR --profile HOST/ID` 指定宿主配置。`init` 会生成一个示例 GitHub Actions 工作流，但这个示例不会替你安装 `agentc`；放到 GitHub CI 前，需要先在工作流中配置 CLI 安装步骤。

## 可选的一次性迁移

`import` 用于把已有宿主目录**一次性转换**为 AgentC 的 `agent-project`，不是日常编译的前置步骤。`inspect` 只读取并识别来源；`import` 不会安装到宿主配置目录。

```bash
agentc inspect ./cursor-plugin
agentc import cursor --from ./cursor-plugin --output ./my-agent --yes
```

也支持 `cursor-workspace`、`cursor-user`、`codex`、`claude-code` 等来源。`import` 不带 `--yes` 时只预览，不创建输出目录。完整的来源、合并命令及错误码见[英文 README 的 Advanced migration](README.md#advanced-migration)。

## 三端能力边界

运行 `agentc matrix` 可查看仓库内置的宿主能力表：

```bash
agentc matrix
agentc matrix --format json
```

| 能力 | Cursor | Codex | Claude Code |
| --- | --- | --- | --- |
| Skill | supported | supported | supported |
| Rule | supported | partial | partial |
| Command | supported | partial | supported |
| Agent | supported | partial | partial |
| Hook、MCP | unsupported | unsupported | unsupported |

`supported` 表示默认模板在该宿主上受到支持；`partial` 表示 AgentC 仍会输出文件，但宿主可能拒绝，不能当作成功兼容；`unsupported` 表示当前不支持。编译前请以 `agentc matrix` 的实际输出和诊断为准。

## 输出与安全检查

所有子命令支持 `--format text|json|protocol`，默认是 `text`。给 Agent 或自动化调用时建议使用 `--format protocol`：它输出一个包含 `protocolVersion`、`status`、`summary`、`diagnostics`、`artifacts`、`confirmation` 六个字段的 JSON 对象。`--format json` 输出的是**诊断数组**，不是同一个协议对象。完整字段说明和 `AGC-*` 诊断码表见[英文 README](README.md#output-formats)。

发布前可以只读扫描指定目录：

```bash
agentc audit --for-public ./my-agent
```

`audit` 不会默认扫描当前目录或用户主目录，也不会修改传入的目录。它会检查密钥、内部宿主信息等阻塞项，并检查目录中已有的 ZIP；发现阻塞项时返回错误。不要把未审核的私有资料上传到公开仓库。

## 测试与项目状态

本仓库的 [GitHub Actions CI](.github/workflows/ci.yml) 会尝试运行单元测试、构建 wheel，并在 Ubuntu、Windows、macOS 上做安装后冒烟测试。构建 wheel 与测试是否通过是两件事：完整三端校验还依赖宿主侧工具，缺少 Codex 官方校验脚本或 Claude Code 命令时，相关测试可能失败。请查看实际的 [Actions 运行结果](https://github.com/mars-xp/agentc/actions)，不要把工作流已启动等同于全部通过。

本地需要测试依赖时，可从仓库根目录运行：

```bash
python3 -m pip install -r requirements/plugin-validator.txt
```

项目仍处于预览阶段；更详细的 CLI 命令、诊断码、覆盖层和宿主配置说明见[英文 README](README.md)。
