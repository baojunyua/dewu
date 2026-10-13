# 得物决策助手 · 半自动搬砖决策

给货号或图片 → 控制雷电模拟器里的得物 → 查价结果通过 **WebSocket 推送到前端**。

## 环境要求

- Python 3.10+
- 雷电模拟器已启动，得物已登录
- ADB 可用（默认：`D:\leidian\LDPlayer9\adb.exe`）

## 安装

```powershell
cd dewu
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m uiautomator2 init
```

## 启动

```powershell
python run.py
```

浏览器打开：http://127.0.0.1:8765

货号查询会先推送各码预计收入，再自动采集商家版 7 日销量。
主货号和页面明确列出的辅助货号均按完整货号校验，不接受部分匹配。
未采集的预计收入或销量保留为空，不用商品售价或最近购买样本代替；
未填写成本时不会给出购买建议。同一时间只允许一个查询。

查询结果包含商品详情主图（点击放大）、各尺码预计收入与商家版销量截图。
页面最多保留最近三次查询，通过标签切换；尺码表每页显示八行。
查询状态条仅在任务进行时显示，完成后自动隐藏，并显示短暂完成提示。
结果标题旁的声音开关可启用完成提示音，默认关闭。
未填成本、销量查询中和销量缺失分别显示对应状态，不会误判为“观望”。

## 回归测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests\test_frontend.cjs
```

单元测试不操作模拟器。端到端查价仍需已登录的得物与雷电模拟器。

## 使用

1. **货号查价**：输入货号点「查价」，脚本自动搜索并解析价格。
2. **图片查价**：上传图片后点「识图查价」；脚本会把图推进模拟器并尝试打开识图，**可能需要你在模拟器里手动选一次图片**，选完后自动读价并推送前端。

实时进度与最终结果会出现在「执行过程」和「查价结果」中。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/status` | 设备与连接状态 |
| POST | `/api/query/sku` | `{"sku":"DJ0950-001"}` |
| POST | `/api/query/image` | `multipart/form-data` 字段 `file` |
| WS | `/ws` | 推送 `job_started` / `progress` / `price_result` / `result` |

## 说明

- 得物 UI 改版后，点击坐标可能需微调：编辑 `backend/dewu_bot.py`。
- 查询串行执行，避免多任务抢控件。
- 本工具仅供个人辅助查价，请遵守得物用户协议，勿用于高频爬取或商业滥用。
