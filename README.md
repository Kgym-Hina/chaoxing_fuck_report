# Chaoxing Report Auto Filler

基于 Playwright 的 CQCET的 超星日报/周报自动填写脚本，支持调用 DeepSeek 生成内容并提交。

## 环境要求

- Python 3.9+
- 已安装浏览器驱动：`python -m playwright install`

## 安装依赖

```bash
pip install -r requirements.txt
```

## 必要环境变量

- `DEEPSEEK_API_KEY`：DeepSeek API Key
- `DEEPSEEK_BASE_URL`（可选，默认 `https://api.deepseek.com`）
- `DEEPSEEK_MODEL`（可选，默认 `deepseek-chat`）
- `CHAOXING_PHONE`（可选，登录页自动填充手机号）
- `CHAOXING_PWD`（可选，登录页自动填充密码）

## 运行方式

日报：

```bash
REPORT_KIND=daily python main.py
```

周报：

```bash
REPORT_KIND=weekly python main.py
```

全部（先日报后周报）：

```bash
REPORT_KIND=all python main.py
```

首次运行会打开登录页，请完成登录。登录状态会保存到 `storage_state.json`，下次可复用。

## 产物说明

- `storage_state.json`：浏览器登录态（包含 cookie 等敏感信息）
- `unsubmitted.json`：本次处理的未提交条目记录
