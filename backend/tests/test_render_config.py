# JobServe search still drives a real browser, so both Render services must install Chromium.


def test_render_yaml_installs_playwright_chromium_for_web_and_worker() -> None:
    render = open("render.yaml", encoding="utf-8").read()

    assert "pip install -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium && cd frontend && npm install && npm run build" in render
    assert "pip install -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=0 python -m playwright install chromium" in render
    assert render.count("PLAYWRIGHT_ENABLED") >= 2
    assert render.count('value: "true"') >= 2
    assert render.count("PLAYWRIGHT_BROWSERS_PATH") >= 2
    assert render.count('value: "0"') >= 2
