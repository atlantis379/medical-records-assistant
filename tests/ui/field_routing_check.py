"""Drives the real editor page in Playwright's bundled Chromium and checks field routing end to end.

Needs Playwright, which the offline package's Python has:
    dist\\<package>\\runtime\\python\\python.exe -X utf8 tests\\ui\\field_routing_check.py [screenshot.png]
tests/test_fields_js.py runs it when that Python is available, and skips otherwise.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = (Path(__file__).resolve().parents[2] / "extension" / "editor.html").as_uri()
failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def chips(page):
    return page.eval_on_selector_all(".field-chip", "els => els.map(e => ({text: e.textContent, cls: e.className}))")


def draft(page):
    return page.input_value("#draft")


def review_titles(page):
    return page.eval_on_selector_all("#riskList .risk-item strong", "els => els.map(e => e.textContent)")


def main(screenshot=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(PAGE)
        page.wait_for_timeout(1200)

        labels = [c["text"] for c in chips(page)]
        check("field chips are shown", "主诉" in labels and "现病史" in labels, labels)
        check("infectious-disease field present by default", "流行病学史" in labels)
        check("routing is on by default", page.is_checked("#fieldRoutingToggle"))

        # dictation with spoken field names
        page.evaluate("mergeText('主诉，反复咳嗽三年。现病史，患者三年前出现咳嗽。')")
        check("dictation is placed under the spoken fields",
              draft(page) == "主诉：反复咳嗽三年。\n现病史：患者三年前出现咳嗽。", draft(page))
        classes = {c["text"]: c["cls"] for c in chips(page)}
        check("filled fields are marked", "filled" in classes["主诉"] and "filled" in classes["现病史"])
        check("the last spoken field is current", "current" in classes["现病史"])
        check("current field is announced", "现病史" in page.inner_text("#fieldCurrent"))

        # more speech without a field name continues in the current field
        page.evaluate("mergeText('夜间加重。')")
        check("speech without a field name continues in the current field",
              draft(page).endswith("现病史：患者三年前出现咳嗽。夜间加重。"), draft(page))

        # choosing a field by hand creates its heading and receives the next dictation
        page.click(".field-chip[data-key='past_history']")
        check("clicking a field creates its heading", "既往史：" in draft(page), draft(page))
        page.evaluate("mergeText('高血压十年。')")
        check("dictation goes to the field that was clicked", draft(page).endswith("既往史：高血压十年。"), draft(page))

        # clicking a field again releases it
        page.click(".field-chip[data-key='past_history']")
        check("clicking the current field releases it", "current" not in {c["text"]: c["cls"] for c in chips(page)}["既往史"])

        # the caret decides the current field
        page.evaluate("document.getElementById('draft').setSelectionRange(5, 5)")
        page.click("#draft", position={"x": 10, "y": 10})
        page.evaluate("document.getElementById('draft').setSelectionRange(5, 5); followCaretToField()")
        check("the caret decides the current field", "current" in {c["text"]: c["cls"] for c in chips(page)}["主诉"])

        # undo restores the draft before the routed insertion
        before = draft(page)
        page.evaluate("mergeText('现病史，三天。')")
        page.click("#undoButton")
        check("undo reverts a routed insertion", draft(page) == before, draft(page))

        # review panel
        page.fill("#draft", "床号3\n主诉：咳嗽\n现病史：\n既往史：高血压")
        page.dispatch_event("#draft", "input")
        titles = review_titles(page)
        check("review panel flags unassigned text", "有内容未归入任何字段" in titles, titles)
        check("review panel flags empty fields", "字段标题下没有内容" in titles, titles)
        page.fill("#draft", "主诉：咳嗽\n现病史：三天")
        page.dispatch_event("#draft", "input")
        titles = review_titles(page)
        check("a tidy draft raises neither", "有内容未归入任何字段" not in titles and "字段标题下没有内容" not in titles, titles)

        # routing off: the old behaviour
        page.uncheck("#fieldRoutingToggle")
        page.fill("#draft", "")
        page.evaluate("mergeText('主诉，咳嗽。')")
        check("with routing off the text is inserted as before", draft(page) == "主诉，咳嗽。", draft(page))
        page.check("#fieldRoutingToggle")

        # nothing chosen and no command: legacy insertion, nothing lost
        page.fill("#draft", "")
        page.evaluate("currentFieldKey = null; mergeText('患者咳嗽三天。')")
        check("text with no field and no command is kept as before", draft(page) == "患者咳嗽三天。", draft(page))

        # field sets follow the doctor's specialty
        page.evaluate("selectedSpecialties = ['orthopedics']; renderFieldBar()")
        labels = [c["text"] for c in chips(page)]
        check("orthopedics gets the specialty exam field", "专科检查" in labels and "流行病学史" not in labels, labels)
        page.fill("#draft", "")
        page.evaluate("currentFieldKey = null; mergeText('专科检查，左髋部压痛。')")
        check("orthopedic field name is a command", draft(page) == "专科检查：左髋部压痛。", draft(page))

        # English interface
        page.select_option("#uiLanguageSelect", "en-US")
        page.wait_for_timeout(300)
        english = [c["text"] for c in chips(page)]
        check("chips follow the interface language", "Chief complaint" in english, english)

        page.select_option("#uiLanguageSelect", "zh-CN")
        page.wait_for_timeout(200)

        # ---- stop-service button (the service is mocked: a file:// page cannot send the real extension origin)
        seen = {}
        page.route("**/health", lambda route: route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                   body='{"status":"ok","model_loaded":false,"hotword_count":10,"active_hotword_count":10}'))
        def shutdown(route):
            seen["method"] = route.request.method
            seen["control"] = route.request.headers.get("x-bingli-control")
            route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"}, body='{"ok":true}')
        page.route("**/service/shutdown", shutdown)
        page.evaluate("checkService()")
        page.wait_for_timeout(500)
        check("stop button is shown while the service is running", page.is_visible("#stopServiceButton"))
        check("status says the service is connected", "已连接" in page.inner_text("#serviceStatus"), page.inner_text("#serviceStatus"))

        dialogs = []
        page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.click("#stopServiceButton")
        page.wait_for_timeout(300)
        check("cancelling the confirmation stops nothing", not seen and dialogs and "停止" in dialogs[0], dialogs)

        page.once("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.click("#stopServiceButton")
        page.wait_for_timeout(500)
        check("confirming sends the guarded shutdown request", seen.get("method") == "POST" and seen.get("control") == "1", seen)
        check("the doctor is told how to start again", "桌面" in page.inner_text("#feedback"), page.inner_text("#feedback"))

        page.unroute("**/health")
        page.route("**/health", lambda route: route.abort())
        page.evaluate("checkService()")
        page.wait_for_timeout(500)
        check("offline message points to the desktop icon", "桌面" in page.inner_text("#serviceDetail"), page.inner_text("#serviceDetail"))
        check("stop button is hidden when the service is down", not page.is_visible("#stopServiceButton"))

        if screenshot:
            page.select_option("#uiLanguageSelect", "zh-CN")
            page.evaluate("selectedSpecialties = ['infectious_disease']; currentFieldKey = null")
            page.fill("#draft", "")
            page.evaluate("mergeText('主诉，反复咳嗽三年。现病史，患者三年前出现咳嗽，夜间加重。既往史，高血压十年。')")
            page.click(".field-chip[data-key='auxiliary_exam']")
            page.wait_for_timeout(200)
            page.screenshot(path=screenshot)

        check("no page errors", not errors, errors)
        browser.close()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
