# fnos-openclaw-compat

飞牛 OpenClaw 兼容助手，由 **X.Zoe** 维护和发布。

保留飞牛原生管理入口，将受管内核固定为 **OpenClaw 2026.10.5**。提供版本检查、完整备份、兼容补丁、浏览器组件和回滚。它是独立的配置工具，不是第二份 OpenClaw，也不是官方扩展。

## 下载与安装

安装包：[fnos-openclaw-compat-1.0.1-x86.fpk](dist/fnos-openclaw-compat-1.0.1-x86.fpk)。

1. 从飞牛应用中心安装并启用原版“飞牛 OpenClaw” **0.1.5**。
2. 在应用中心手动安装本项目的 FPK。
3. 安装向导默认 **仅检查**，不会升级或重启原应用。
4. 需要升级时选择 **应用兼容补丁并固定 2026.10.5**。此操作会停止服务、完整备份、下载固定依赖并迁移数据。
5. 安装后通过兼容助手的应用设置再次检查、应用补丁或指定备份回滚。继续通过原飞牛 OpenClaw 入口使用应用。

助手没有独立桌面网页或常驻服务。卸载助手不会回退内核，也不会删除原 OpenClaw 的数据或备份。

## 支持范围

| 项目 | 支持条件 |
| --- | --- |
| 架构 | x86_64 |
| 原管理平台 | trim.openclaw 0.1.5，源码构建 SHA-256 必须匹配清单 |
| 实例 | 单个 default 受管实例 |
| 固定内核 | 2026.10.5 |
| 微信插件 | 已安装时升级到 2.4.10 |
| 专用运行时 | Node.js 24.20.0，优先复制同版本本机二进制，缺失时从官方站点下载并校验 |
| 浏览器 | Playwright 1.64.1 下载 Chromium revision 1248，无界面运行并保留沙箱 |

不支持未知管理构建、多实例、独立系统模式，或自动降级比 2026.10.5 更新的内核。不是跟随上游最新版自动升级的工具。

## 兼容调整

- 过滤新版已删除的旧配置字段，使用官方 `doctor --fix` 迁移数据库和会话。
- 更新旧微信插件，并将旧源码补丁限定在 2.4.1。
- 修正代理已解压正文仍保留压缩标记的问题。
- 允许同源 iframe 嵌入，保留其他页面安全策略。
- 在浏览器签名前通过官方令牌 URL fragment 提供凭据，修正设备签名失效问题。
- 允许终端需要的 WebAssembly 编译，不启用通用 JavaScript `unsafe-eval`。
- 配置专用 Chromium，关闭内核自动升级，并固定管理平台更新/重装目标版本。

新浏览器仍需正常设备配对；脚本不会自动批准浏览器。Chromium 不会自动接管用户电脑上的浏览器。

## 检查、日志与回滚

在宿主机的 root 终端中执行：

```bash
python3 /var/apps/fnos-openclaw-compat/target/compat.py check
```

操作日志：`/var/apps/fnos-openclaw-compat/var/compat-jobs.log`。

完整备份：`/var/apps/trim.openclaw/home/compat-backups/时间戳/`。安装阶段的完整异常保存在该备份目录的 `install-error.log`，即使助手安装失败也可查阅。

回滚可以在助手设置中选择操作并填写备份目录，也可执行：

```bash
python3 /var/apps/fnos-openclaw-compat/target/compat.py rollback /var/apps/trim.openclaw/home/compat-backups/时间戳
```

请替换示例中的时间戳。备份含配置、凭据和聊天数据，必须留在本机妥善保管。SQLite 升级后，降级需要完整恢复备份，不能只替换旧版 npm 包。

## 构建和测试

需要飞牛官方 `fnpack`、Python 3.9+。在仓库根目录运行：

```bash
bash build.sh
```

产物位于 `dist/`，同时生成 SHA-256 校验文件。开发测试：

```bash
cd app
python3 -m unittest -v tests.py
```

直接运行脚本时也支持 `install --dry-run`、`install --without-browser`、`check` 和 `rollback`。实际安装、检查和回滚应在飞牛宿主机 root 终端进行，不能在普通容器内执行。

## 验证与限制

1.0.1 已在一台 fnOS NAS 新装的 OpenClaw 2026.5.4 上完成脚本升级到 2026.10.5、健康检查、页面资源检查和 Chromium 启动验证；通过 9 项自动化测试，FPK 的“仅检查”模式已通过应用中心实际安装。此前同机验证过终端 Bash 渲染、历史会话读取和微信插件加载。尚未覆盖其他设备、架构和全部使用场景。

飞牛原应用升级或重装可能覆盖补丁。未知构建会拒绝自动套用，请不要绕过源码校验。

代理 Fake-IP DNS 可能导致部分公网 URL 被内核策略拦截；本项目不关闭 SSRF 防护，也不修改宿主机 DNS。浏览器能启动不代表任意网站都能访问。

安装使用 root 生命周期权限完成备份和修补；OpenClaw 和 Chromium 使用原应用用户运行。本助手不新增 root 网络服务。

## 1.0.1 修复

- 优先复用合适的本机 Node.js，避免运行时准备阶段的重复网络下载。
- 在完整备份目录保存异常堆栈，避免 FPK 安装回退后丢失错误详情。
- 添加“不触发网络下载”的运行时回归测试。

本仓库只包含通用脚本、补丁、版本清单和构建产物，不包含本机环境变量、数据库、登录信息或聊天记录，也不分发原飞牛管理平台的完整源码。
