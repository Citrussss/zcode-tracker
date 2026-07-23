# 在群晖 NAS 上定期检查并下载 ZCode 安装包

本指南让群晖 DSM 7.x 的**任务计划**每天自动检查 ZCode 是否有新版本，
有则下载到 `/volume2/SoulCenter/安装包/zcode`，并用官方 SHA512 校验。

复用仓库里的 `check_zcode.py`（纯 Python 标准库，无需 pip 装包），
状态文件单独存放在 NAS 上，与 GitHub 上的 CI 互不干扰。

---

## 工作流程图

```
群晖任务计划（每天定时）
   └─ python3 check_zcode.py
        --download-dir  /volume2/SoulCenter/安装包/zcode
        --state-file    /volume2/SoulCenter/安装包/zcode/.state.json
        --catalog-file  /volume2/SoulCenter/安装包/zcode/.catalog.json
        ↓
   抓取 zcode.z.ai/cn → 比对 .state.json
        ↓ 有新版本
   下载 exe → SHA512 校验 → 存入 安装包/zcode/
        ↓
   更新 .state.json（记住已处理版本）
```

退出码：`0`=下载了新版本 / `2`=无新版本（正常）/ `1`=出错。
任务计划对退出码不敏感，但日志会记录全过程。

---

## 第 1 步：装 Python3（DSM 7.x）

1. 打开 **套件中心** → 搜索 **Python 3** → 安装（官方套件，名为 "Python3"）。
2. 确认可用。SSH 进 NAS（或用任务计划跑一次）：

   ```sh
   python3 --version
   # 应输出 Python 3.8 或更高
   ```

   > 注意：群晖的 Python 是 `python3`，不是 `python`。

---

## 第 2 步：准备脚本

把仓库里的 `zcode-tracker/check_zcode.py` 放到 NAS 上。建议放在共享文件夹里方便管理：

```sh
# 假设你已通过 File Station 或 SSH 连上 NAS
mkdir -p /volume2/SoulCenter/zcode-tracker
```

然后把本仓库的 `zcode-tracker/check_zcode.py` 上传到：
```
/volume2/SoulCenter/zcode-tracker/check_zcode.py
```

上传方式任选其一：
- **File Station**：把文件拖进 `SoulCenter/zcode-tracker` 文件夹
- **SSH/SCP**（从电脑端）：
  ```sh
  scp zcode-tracker/check_zcode.py 你的用户名@NAS地址:/volume2/SoulCenter/zcode-tracker/
  ```

> 以后脚本要更新（比如修了 bug），重新上传这一个文件即可。

---

## 第 3 步：创建下载目录

```sh
mkdir -p "/volume2/SoulCenter/安装包/zcode"
```

`安装包` 这个中文目录名完全没问题，脚本用绝对路径处理。

---

## 第 4 步：手动测试一次（强烈建议）

SSH 进 NAS，先手动跑一次，确认网络、权限、下载都正常：

```sh
python3 /volume2/SoulCenter/zcode-tracker/check_zcode.py \
  --download-dir  "/volume2/SoulCenter/安装包/zcode" \
  --state-file    "/volume2/SoulCenter/安装包/zcode/.state.json" \
  --catalog-file  "/volume2/SoulCenter/安装包/zcode/.catalog.json"
```

首次运行（state 为空）会下载当前最新版到 `安装包/zcode/`，约 120MB，需要几分钟。
看到 `[zcode-tracker] Done.` 和 `sha512 OK` 即成功。

检查结果：
```sh
ls -lh "/volume2/SoulCenter/安装包/zcode/"
# 应看到 ZCode-3.4.2-win-x64.exe + .state.json + .catalog.json
```

再跑一次，应显示 `No new version. Up to date.`（不会重复下载）。

---

## 第 5 步：配置任务计划（每天自动）

1. 打开 **控制面板 → 任务计划**（DSM 7.x 在"系统"分类下）。
2. 点 **新增 → 计划任务 → 用户定义的脚本**。
3. **常规** 选项卡：
   - 任务名称：`ZCode 版本检查`
   - 用户：**root**（或一个对 `/volume2/SoulCenter/` 有读写权限的账号；root 最省心）
   - 勾选 **已启用**
4. **计划** 选项卡：
   - 运行日期：每天
   - 运行时间：例如 `10:00`（按你习惯，建议选个 NAS 空闲时段）
   - 频率：每天
5. **任务设置** 选项卡 → **运行命令** 框，填入：

   ```sh
   python3 /volume2/SoulCenter/zcode-tracker/check_zcode.py \
     --download-dir  "/volume2/SoulCenter/安装包/zcode" \
     --state-file    "/volume2/SoulCenter/安装包/zcode/.state.json" \
     --catalog-file  "/volume2/SoulCenter/安装包/zcode/.catalog.json"
   ```

   - （可选）在"发送运行详情到"填你的邮箱，出错时收到通知。
6. **确定** 保存。

立即手动触发测试：选中这个任务 → 点 **运行**，然后看 **操作 → 查看结果** 的输出日志。

---

## 目录最终结构

```
/volume2/SoulCenter/
├── zcode-tracker/
│   └── check_zcode.py              # 脚本（从仓库复制）
└── 安装包/
    └── zcode/
        ├── ZCode-3.4.2-win-x64.exe  # 下载的安装包（每个版本一个）
        ├── .state.json               # 已处理版本记忆（去重用）
        └── .catalog.json             # 完整版本档案（URL/sha512/日期）
```

`.state.json` / `.catalog.json` 用点开头，File Station 里默认不显眼，但确实存在。

---

## 关于代理（如 NAS 需要翻墙访问 zcode.z.ai）

如果 NAS 直接连不上 `zcode.z.ai`，在任务脚本的运行命令前加一行代理：

```sh
export http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890
python3 /volume2/SoulCenter/zcode-tracker/check_zcode.py \
  --download-dir  "/volume2/SoulCenter/安装包/zcode" \
  --state-file    "/volume2/SoulCenter/安装包/zcode/.state.json" \
  --catalog-file  "/volume2/SoulCenter/安装包/zcode/.catalog.json"
```

（把 `7890` 换成你 NAS 上代理客户端的实际端口。）
如果 NAS 上没有代理，可跳过此节——`zcode.z.ai` 通常国内可直连。

---

## 与 GitHub Actions 的关系

两套是**完全独立**的：
- **GitHub Actions**：下载到 GitHub 的构建产物（保留 90 天），更新仓库 README/catalog
- **NAS 任务计划**：下载到你的 NAS（永久保存）

它们各自维护自己的 state.json，互不影响。NAS 上这份是你**自己永久归档**的副本，
不受 GitHub artifact 90 天过期限制。

---

## 排错

| 现象 | 原因 / 解决 |
|------|------------|
| `command not found: python3` | 没装 Python3 套件；或改用完整路径 `/var/packages/Python3/target/bin/python3` |
| `Permission denied` 写文件 | 任务的用户对目标目录无写权限；改用 root 运行，或给目录加权限 |
| `URLError: connection refused/timed out` | NAS 连不上 `zcode.z.ai`；检查网络/代理（见上节） |
| 重复下载同一版本 | `.state.json` 被删或路径变了；确认 `--state-file` 指向固定位置 |
| `sha512 mismatch` | 下载不完整（重跑）或官方重新发布；脚本会拒绝记录损坏的文件 |

脚本本身无需在 NAS 上配置凭据，纯只读下载公开页面。
