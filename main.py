from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://passport2.chaoxing.com/login?fid=&newversion=true"
DAILY_URL = "https://cqcet.dgsx.chaoxing.com/form/mobile/reportManage?type=2&pageId=933158&wfwfid=356&websiteId=485506"
WEEKLY_URL = "https://cqcet.dgsx.chaoxing.com/form/mobile/reportManage?type=0&wfwfid=356&pageId=933158&websiteId=485506"
OUTPUT_PATH = Path("unsubmitted.json")
STORAGE_STATE_PATH = Path("storage_state.json")

API_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
API_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


def _try_auto_login(page) -> None:
    phone = os.getenv("CHAOXING_PHONE", "").strip()
    pwd = os.getenv("CHAOXING_PWD", "").strip()
    if not phone or not pwd:
        return

    # 尝试多种常见选择器
    phone_input = page.locator("input[name='phone'], input[name='uname'], input[type='text']").first
    pwd_input = page.locator("input[name='pwd'], input[type='password']").first
    if phone_input.count() == 0 or pwd_input.count() == 0:
        return

    phone_input.fill(phone)
    pwd_input.fill(pwd)

    # 尝试点击登录按钮，找不到就回车提交
    login_btn = page.locator("button:has-text('登录'), .btn-big-blue, .btn_login, .login_btn").first
    if login_btn.count() > 0:
        login_btn.click()
    else:
        pwd_input.press("Enter")


def wait_for_login(page) -> None:
    page.goto(LOGIN_URL, wait_until="load")
    print("已打开登录页，等待登录…")
    _try_auto_login(page)
    while True:
        try:
            page.wait_for_url(lambda url: "passport2.chaoxing.com/login" not in url, timeout=1000)
            break
        except PlaywrightTimeoutError:
            time.sleep(0.5)
    print(f"检测到离开登录页，当前地址：{page.url}")


def call_deepseek(report_kind: str) -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量。")

    is_weekly = report_kind == "weekly"
    if is_weekly:
        prompt = (
            "请用中文生成两段文本，输出 JSON。\n"
            "字段: summary(实习报告内容摘要，简短总结，不少于30字), "
            "biweekly(双周记，不少于100字，服务器维护岗位视角)。"
        )
    else:
        prompt = (
            "请用中文生成两段简短文本（每段不少于30个字），输出 JSON。\n"
            "字段: feeling(今日收获与感受，服务器维护岗位视角), "
            "work(今日主要工作、遇到的问题及如何解决的)。"
        )

    payload = {
        "model": API_MODEL,
        "messages": [
            {"role": "system", "content": "只输出 JSON，字段 feeling 和 work。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }

    base_url = API_BASE_URL.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    resp = requests.post(
        f"{base_url}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    if not content:
        raise RuntimeError("AI 返回内容为空。")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            return json.loads(match.group(0))
        raise RuntimeError(f"AI 返回非 JSON 内容：{content[:200]}")


def ensure_list_expanded(page) -> None:
    arrows = page.locator(".submit_arrow")
    count = arrows.count()
    for i in range(count):
        arrows.nth(i).click()
        time.sleep(0.2)


def _find_date_input(page, label_candidates: list[str]):
    for label_text in label_candidates:
        widget = page.locator("li.fsw-ul-item", has=page.locator("label", has_text=label_text)).first
        if widget.count() == 0:
            continue
        input_locator = widget.locator("input.el-input__inner").first
        if input_locator.count() > 0:
            return input_locator
    # 回退：尝试找到第一个日期输入
    input_locator = page.locator("input.el-input__inner[placeholder='年-月-日']").first
    if input_locator.count() > 0:
        return input_locator
    return None


def _extract_date(date_text: str) -> str | None:
    date_text = date_text.strip()
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", date_text)
    if not match:
        return None
    return match.group(1)


def set_date_input(page, date_text: str, label_candidates: list[str]) -> None:
    input_locator = _find_date_input(page, label_candidates)
    if input_locator is None:
        print("未找到日期输入框，跳过日期填写。")
        return

    input_locator.wait_for(timeout=8000)
    input_locator.click()

    parsed_date = _extract_date(date_text)
    if not parsed_date:
        print(f"未识别到可用日期：{date_text}，跳过日期填写。")
        return
    try:
        target_year, target_month, target_day = [int(x) for x in parsed_date.split("-")]
    except ValueError as exc:
        raise RuntimeError(f"日期格式不正确：{parsed_date}") from exc

    panel_id = input_locator.get_attribute("aria-controls")
    if not panel_id:
        raise RuntimeError("未获取到日期面板 ID。")
    panel = page.locator(f"#{panel_id} .el-date-picker")
    panel.wait_for(timeout=8000)

    def _read_year_month() -> tuple[int, int]:
        labels = panel.locator(".el-date-picker__header-label")
        year_text = labels.nth(0).inner_text().strip()
        month_text = labels.nth(1).inner_text().strip()
        year = int(year_text.replace("年", "").strip())
        month = int(month_text.replace("月", "").strip())
        return year, month

    max_steps = 240
    for _ in range(max_steps):
        year, month = _read_year_month()
        if (year, month) == (target_year, target_month):
            break
        if (year, month) < (target_year, target_month):
            panel.locator(".el-picker-panel__icon-btn.arrow-right").click()
        else:
            panel.locator(".el-picker-panel__icon-btn.arrow-left").click()
        time.sleep(0.1)
    else:
        raise RuntimeError("无法切换到目标年月，已超过最大步数。")

    # 选择当月日期（排除上/下月）
    day_cell = panel.locator(
        "td.available:not(.prev-month):not(.next-month) .el-date-table-cell__text",
        has_text=str(target_day),
    ).first
    if day_cell.count() == 0:
        raise RuntimeError(f"未找到日期：{date_text}")
    day_cell.click()


def fill_richtext_by_label(page, label_text: str, content: str) -> None:
    widget = page.locator("li.fsw-ul-item", has=page.locator("label", has_text=label_text)).first
    iframe = widget.locator("iframe").first
    iframe.wait_for(timeout=8000)
    iframe_handle = iframe.element_handle()
    if iframe_handle is None:
        raise RuntimeError(f"无法获取编辑器 iframe 元素：{label_text}")
    frame = iframe_handle.content_frame()
    if frame is None:
        raise RuntimeError(f"无法获取编辑器 iframe：{label_text}")
    # 先清空再通过键盘输入，确保触发编辑器事件
    frame.evaluate("document.body.innerHTML = '<p></p>';")
    frame.locator("body").click()
    frame.locator("body").press("Control+A")
    frame.locator("body").type(content, delay=10)
    # 触发编辑器内部状态更新（模拟一次空格/退格）
    frame.locator("body").press(" ")
    frame.locator("body").press("Backspace")
    frame.evaluate(
        """() => {
            document.body.dispatchEvent(new Event('input', { bubbles: true }));
            document.body.dispatchEvent(new Event('change', { bubbles: true }));
            document.body.dispatchEvent(new Event('blur', { bubbles: true }));
        }"""
    )


def fill_text_input_by_label(page, label_text: str, content: str) -> None:
    widget = page.locator("li.fsw-ul-item", has=page.locator("label", has_text=label_text)).first
    input_locator = widget.locator("input[type='text']").first
    input_locator.wait_for(timeout=8000)
    input_locator.fill(content)


def fill_contenteditable_by_label(page, label_text: str, content: str) -> None:
    widget = page.locator("li.fsw-ul-item", has=page.locator("label", has_text=label_text)).first
    editor = widget.locator("[contenteditable='true']").first
    editor.wait_for(timeout=8000)
    editor.click()
    editor.press("Control+A")
    editor.type(content, delay=10)


def collect_unsubmitted_entries(page) -> list[dict]:
    items = page.locator(".submit_list >> li")
    item_count = items.count()
    entries: list[dict] = []
    for i in range(item_count):
        item = items.nth(i)
        if item.locator(".lineGray", has_text="未提交").count() > 0:
            date_text = item.locator("dd").first.inner_text().strip()
            onclick = item.get_attribute("onclick") or ""
            entries.append({"index": i, "date": date_text, "onclick": onclick})
    return entries


def run_onclick(page, onclick: str) -> None:
    code = onclick.strip()
    if code.startswith("javascript:"):
        code = code[len("javascript:") :].strip()
    if code.startswith("return "):
        code = code[len("return ") :].strip()
    if code.endswith(";"):
        code = code[:-1]
    if not code:
        raise RuntimeError("onclick 为空，无法进入详情页。")
    page.evaluate(
        """(js) => {
            eval(js);
        }""",
        code,
    )


def process_unsubmitted(page, report_kind: str, target_url: str) -> list[dict]:
    print(f"进入表单列表页（{report_kind}）…")
    page.goto(target_url, wait_until="load")
    print("页面已进入，继续执行…")
    ensure_list_expanded(page)

    def _ensure_len(text: str, min_len: int) -> str:
        text = text.strip()
        if len(text) >= min_len:
            return text
        return (text + "。") * ((min_len // max(len(text), 1)) + 1)

    processed: list[dict] = []
    print("收集未提交条目…")
    entries = collect_unsubmitted_entries(page)
    if not entries:
        print("没有未提交条目，结束。")
        return processed

    total = len(entries)
    for idx, entry in enumerate(entries, start=1):
        print(f"进度 {idx}/{total} - 打开未提交条目：index={entry['index']} date={entry['date']}")
        run_onclick(page, entry["onclick"])
        # 等待详情页加载
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            pass
        print("请求 AI 生成文本…")
        generated = call_deepseek(report_kind)
        if report_kind == "weekly":
            summary_text = _ensure_len(generated.get("summary", ""), 30)
            biweekly_text = _ensure_len(generated.get("biweekly", ""), 100)
            if not summary_text or not biweekly_text:
                raise RuntimeError("AI 输出不完整，请检查返回内容。")

            date_labels = ["当前周报日期", "周报日期", "当前日期"]
            set_date_input(page, entry["date"], date_labels)

            print("填写：实习报告内容摘要")
            fill_text_input_by_label(page, "实习报告内容摘要", summary_text)
            print("填写：双周记（字数不少于100）")
            fill_contenteditable_by_label(page, "双周记（字数不少于100）", biweekly_text)
        else:
            feeling_text = _ensure_len(generated.get("feeling", ""), 30)
            work_text = _ensure_len(generated.get("work", ""), 30)
            if not feeling_text or not work_text:
                raise RuntimeError("AI 输出不完整，请检查返回内容。")

            date_labels = ["当前日报日期", "日报日期", "当前日期"]
            set_date_input(page, entry["date"], date_labels)
            print("填写：今日收获与感受")
            fill_richtext_by_label(page, "今日收获与感受", feeling_text)
            print("填写：今日主要工作、遇到的问题及如何解决的")
            fill_richtext_by_label(page, "今日主要工作、遇到的问题及如何解决的", work_text)

        print("提交表单…")
        page.locator("button.appyl_btm_submit").click()
        # 等待提交成功提示后，直接回到列表页 URL
        try:
            page.locator("text=提交成功").wait_for(timeout=8000)
        except PlaywrightTimeoutError:
            pass
        page.goto(target_url, wait_until="load")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            pass

        print("返回列表页…")
        page.goto(target_url, wait_until="load")
        page.wait_for_timeout(800)
        print(f"完成进度 {idx}/{total}，继续下一条。")

        entry["report_kind"] = report_kind
        processed.append(entry)

    return processed


def main() -> None:
    report_kind = os.getenv("REPORT_KIND", "daily").strip().lower()
    if report_kind not in {"daily", "weekly", "all"}:
        raise RuntimeError("REPORT_KIND 仅支持 daily、weekly 或 all。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = (
            browser.new_context(storage_state=str(STORAGE_STATE_PATH))
            if STORAGE_STATE_PATH.exists()
            else browser.new_context()
        )
        page = context.new_page()

        wait_for_login(page)
        context.storage_state(path=str(STORAGE_STATE_PATH))
        data: list[dict] = []
        if report_kind in {"daily", "all"}:
            data.extend(process_unsubmitted(page, "daily", DAILY_URL))
        if report_kind in {"weekly", "all"}:
            data.extend(process_unsubmitted(page, "weekly", WEEKLY_URL))

        OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已处理 {len(data)} 条未提交，输出到 {OUTPUT_PATH}")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
