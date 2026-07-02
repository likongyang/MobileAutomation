# 移动端 UI 自动化测试框架 v0.2

> Android 优先，架构预留 iOS 扩展空间。

---

## 功能特性

- ✅ **多设备并行执行** — 每台设备独立进程、独立 Appium Server、独立产物
- ✅ **环境自检** — 执行前检查 Python/pytest/Node.js/Appium/ADB/SDK/设备连接
- ✅ **TestContext 统一接口** — 业务用例只需 `context.click(text="...")`
- ✅ **弹性等待** — 元素查找默认带等待，失败时自动截图 + page source
- ✅ **用例隔离** — 可配置用例前后重启/清数据/授权
- ✅ **CPU/内存采集** — 后台线程实时采集，输出 JSONL
- ✅ **Logcat 采集** — 可选启动，故障时自动保存日志片段
- ✅ **Crash/ANR 检测** — 自动识别 FATAL EXCEPTION / ANR 并标记用例失败
- ✅ **结构化报告** — `summary.json`、`device_report.json`、`case.json`、JUnit XML
- ✅ **远程上报** — 支持 HTTP 上报，失败不影响本地报告
- ✅ **日志脱敏** — 自动脱敏 token、手机号、邮箱、身份证

---

## 快速开始

### 1. 安装依赖

```bash
cd /Users/ian/Developer/PythonProject/MobileAutomation
pip install -e .
```

### 2. 安装 Appium

```bash
npm install -g appium
appium driver install uiautomator2
```

### 3. 环境检查

```bash
python -m automation_framework check
```

### 4. 连接设备并运行

```bash
# 自动发现所有在线设备
python -m automation_framework run

# 指定设备
python -m automation_framework run --devices emulator-5554,R5CT123ABC

# 只跑 smoke 标签
python -m automation_framework run --markers smoke

# 失败重试 2 次
python -m automation_framework run --reruns 2
```

---

## 目录结构

```
automation_framework/
  __main__.py               # CLI 入口
  config/
    default.yaml            # 默认配置
  lib/
    adb/client.py           # ADB 命令封装
    appium/
      client.py             # Appium UI 操作封装
      server.py             # Appium Server 生命周期
      ports.py              # 端口分配
      health.py             # 健康检查
    context/test_context.py # TestContext（业务用例主入口）
    device/
      discovery.py          # 设备发现
      info.py               # 设备信息采集
      worker.py             # DeviceWorker（单设备执行）
      scheduler.py          # DeviceScheduler（多设备并行）
    performance/
      cpu.py                # CPU 采集
      memory.py             # 内存采集
    report/
      schema.py             # 报告数据结构
      case_report.py        # case.json 写入与上报
      aggregator.py         # summary.json / JUnit XML 聚合
    log/
      logcat.py             # logcat 采集
      crash_detector.py     # Crash/ANR 检测
      sanitizer.py          # 日志脱敏
    environment/checker.py  # 环境检查
    artifacts/manager.py    # 产物目录管理
    utils/
      config_loader.py      # 配置加载（YAML + CLI 覆盖）
      file.py               # 文件工具
      time.py               # 时间工具
      retry.py              # 重试工具
pages/
  base_page.py              # Page Object 基类
tests/
  conftest.py               # pytest fixtures + hooks
  test_demo.py              # demo 用例
```

---

## 配置文件

`automation_framework/config/default.yaml`：

```yaml
app:
  package: com.transsnet.store
  activity: com.afmobi.palmplay.home.MainActivity
  restart_before_case: true   # 每个用例前重启 App

logcat:
  enabled: false              # 改为 true 开启 logcat 采集

performance:
  enabled: true               # CPU/内存采集
  interval_seconds: 2

wait:
  default_timeout_seconds: 10 # 元素等待默认超时
```

完整配置见 [config/default.yaml](automation_framework/config/default.yaml)。

配置优先级：**命令行 > 环境变量 > YAML 配置文件 > 框架默认值**

---

## 编写测试用例

```python
# tests/test_login.py
import pytest

def test_login_success(context):
    context.app.restart()
    context.click(id="com.transsnet.store:id/btn_login")
    context.input_text(id="com.transsnet.store:id/et_username", value="user@example.com")
    context.input_text(id="com.transsnet.store:id/et_password", value="password")
    context.click(text="登录")
    context.assert_text_exists("首页", timeout=15)

def test_search_app(context):
    context.click(accessibility_id="search_button")
    context.input_text(xpath="//android.widget.EditText[1]", value="WhatsApp")
    context.assert_element_exists(contains_text="WhatsApp", timeout=10)
```

### TestContext 主要 API

```python
# 点击
context.click(id="...")
context.click(text="登录")
context.click(contains_text="确认", timeout=15)
context.click(accessibility_id="...")
context.click(xpath="//android.widget.Button[1]")

# 输入
context.input_text(id="...", value="text")
context.clear_text(id="...")

# 查找
el = context.find(text="...")
els = context.find_all(class_name="android.widget.TextView")
context.scroll_find(text="退出登录", direction="vertical", max_swipes=5)

# 等待
context.wait_for_gone(text="加载中", timeout=20)
context.wait_for_page_stable()

# 断言
context.assert_text_exists("首页")
context.assert_element_exists(id="...", timeout=10)
context.assert_element_not_exists(text="弹窗")

# 截图
context.screenshot("step_01")

# 滑动
context.swipe_up()
context.swipe_down()
context.swipe_left()
context.swipe_right()

# App 操作
context.app.restart()
context.app.clear_data()
context.app.background(seconds=5)
context.allow_permission_if_present()

# 系统
context.back()
context.home()
context.hide_keyboard()

# ADB 扩展
rc, stdout, stderr = context.run_adb("shell", "getprop", "ro.build.version.release")
```

---

## Page Object 模式

```python
# pages/store_home_page.py
from pages.base_page import BasePage

class StoreHomePage(BasePage):
    PKG = "com.transsnet.store"

    def search(self, keyword: str) -> None:
        self.context.click(accessibility_id="search_icon")
        self.context.input_text(id=f"{self.PKG}:id/et_search", value=keyword)

    def is_displayed(self) -> bool:
        return self.context._appium.is_present(text="为你推荐", timeout=5)

# tests/test_home.py
from pages.store_home_page import StoreHomePage

def test_search(context):
    page = StoreHomePage(context)
    assert page.is_displayed()
    page.search("WhatsApp")
```

---

## 产物目录

```
artifacts/<run_id>/
  environment_check.json
  summary.json
  junit.xml
  devices/
    <device_id>/
      device_info.json
      appium/
        server.log
        ports.json
      logs/
        logcat.raw.log
      performance/
        cpu.jsonl
        memory.jsonl
      report/
        device_report.json
      cases/
        <case_name>/
          case.json
          screenshots/
            failure.png
          page_source/
            failure.xml
          logs/
            failure_window.log
```

---

## 退出码

| 退出码 | 说明 |
|-------|------|
| `0` | 所有用例通过 |
| `1` | 存在失败/error/unknown |
| `2` | 环境检查失败 |
| `3` | 没有可执行设备 |

---

## CLI 参数

```
python -m automation_framework run [options]

  --devices              设备序列号，逗号分隔（空=所有在线设备）
  --config               YAML 配置文件路径
  --tests                测试目录或文件
  --markers              pytest mark 表达式
  --keyword              pytest -k 表达式
  --reruns               失败重试次数
  --collect-performance  true/false
  --artifacts-dir        产物输出目录
  --upload-url           远程上报 URL
  --app-package          Android 包名
  --app-activity         启动 Activity
```

---

## 技术栈

- Python 3.10+
- pytest + pytest-rerunfailures
- Appium Python Client (UiAutomator2)
- PyYAML / requests
