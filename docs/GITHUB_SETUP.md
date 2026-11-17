# 在 GitHub 上新建仓库并推送

## 一、当前状态（已完成）
- 本地已 `git init`，**初始提交已完成**：`1c00cb0 feat: Virtuoso AI 自动化能力库初始版本`
- 分支：`master`（可改为 `main`）
- 已写入 `.gitignore`（排除 `out/ sim/ runs/ *.psf` 与两个 30MB+ 的生成注册表）
- 暂存规模：**390 个文件 / 15 MB**（GitHub 完全可接受）

## 二、阻塞点（需要你操作）
本机**没有 GitHub 凭据**：
- 未安装 `gh` CLI；
- 无 `GITHUB_TOKEN` / `.netrc` / `.git-credentials`；
- SSH 到 github.com 返回 `Permission denied (publickey)` —— 现有密钥未在 GitHub 注册。

## 三、你来做（2 步）

### 1) 在 GitHub 网页新建空仓库
https://github.com/new
- 仓库名建议：`virtuoso-ai-automation`（或你喜欢的名字）
- **不要**勾选 "Add a README / .gitignore / license"（本地已有内容）
- 可见性按需选 Private/Public

### 2) 回到本机执行（二选一）

**用 SSH（推荐，先注册公钥）**
```bash
cat ~/.ssh/id_ed25519_lihong.pub        # 把它加到 GitHub → Settings → SSH keys
cd /home/lihong/projects/RFIC-GPT/vl
git branch -M main
git remote add origin git@github.com:<你的用户名>/virtuoso-ai-automation.git
git push -u origin main
```

**用 HTTPS + PAT**
```bash
cd /home/lihong/projects/RFIC-GPT/vl
git branch -M main
git remote add origin https://github.com/<你的用户名>/virtuoso-ai-automation.git
git push -u origin main     # 用户名 + Personal Access Token（不是密码）
```

## 四、后续提交规范（建议）
```bash
git add -A
git commit -m "feat: <一句话>"
git push
```
- 大文件（`audit/*/registry/api_registry*.json` 30MB+）已在 `.gitignore`，
  需要时用 Release 附件或 Git LFS 单独管理；
- 每次真机实验后，把 `audit/<date>/REPORT*.md` 与关键小的 JSON 提交，
  原始 PSF 留在本地/持久存储。
