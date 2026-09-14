"""Non-production browser regression for the real workbench with strictly mocked model/session APIs.

Run: uv run --no-sync --with playwright==1.62.0 scripts/smoke_refinement_workbench.py
Uses existing Chrome and fixture photos. No production session, rating, publication
or model request is written. Backend persistence/security is tested separately.
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "a" * 32
TITLE = "多人精修 · 界面回归样本"
STAMP = "2026-09-09T00:00:00Z"


class FixtureAPI:
    """Model responses here are explicit UI fixtures, never claims about real picture quality."""

    def __init__(self):
        self.image = (ROOT / "tests/fixtures/group-01.png").read_bytes()
        self.reference = (ROOT / "tests/fixtures/source-01.png").read_bytes()
        self.digest = hashlib.sha256(self.image).hexdigest()
        self.requests = []
        self.forbidden = []
        self.models_ready = True
        self.deleted = False
        self.session = {
            "id": SESSION_ID,
            "target_id": "ui-only",
            "title": TITLE,
            "revision": 1,
            "event_cursor": 0,
            "status": "idle",
            "original_url": "/__ui-fixture/original.png",
            "baseline_version_id": "1" * 32,
            "accepted_version_id": None,
            "accepted_image_url": None,
            "active_turn_id": None,
            "created_at": STAMP,
            "updated_at": STAMP,
            "people": [
                {
                    "person_id": 2,
                    "face_index": 2,
                    "person_locator": "左起第二位戴帽人物（界面样本）",
                    "reference_urls": ["/__ui-fixture/reference.png"],
                    "reference_status": "available",
                }
            ],
            "versions": [self.version(index, None if index == 1 else str(index - 1) * 32) for index in range(1, 4)],
            "turns": [],
        }

    def version(self, index, parent):
        return {
            "id": str(index) * 32,
            "parent_version_id": parent,
            "digest": self.digest,
            "image_url": f"/__ui-fixture/version-{index}.png",
            "kind": "baseline" if index == 1 else "candidate",
            "created_at": STAMP,
            "model": "offline-ui-fixture",
            "task_id": "f" * 32,
        }

    def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.hostname in {"fonts.googleapis.com", "fonts.gstatic.com"}:
            # External font availability must not stall an offline UI regression.
            route.fulfill(status=204)
            return
        path = parsed.path
        if path.startswith("/__ui-fixture/"):
            route.fulfill(
                body=self.reference if path.endswith("reference.png") else self.image, content_type="image/png"
            )
            return
        if path == "/api/v1/ai/models":
            route.fulfill(
                status=200 if self.models_ready else 503,
                json={
                    "default_alias": "personaweave-refine-default",
                    "models": [
                        {
                            "alias": "personaweave-refine-default",
                            "provider": "litellm",
                            "model": "offline-ui-fixture",
                            "display_name": "离线界面测试模型",
                            "description": "不调用真实模型",
                            "is_default": True,
                        }
                    ],
                },
            )
            return
        if path == "/api/v1/refinement/sessions":
            summary = {
                key: self.session[key]
                for key in (
                    "id",
                    "target_id",
                    "title",
                    "revision",
                    "status",
                    "accepted_version_id",
                    "active_turn_id",
                    "created_at",
                    "updated_at",
                )
            }
            route.fulfill(json={"code": 200, "data": [] if self.deleted else [summary]})
            return
        if path.startswith(f"/api/v1/refinement/sessions/{SESSION_ID}"):
            if self.deleted:
                route.fulfill(status=404, json={"detail": "Deleted fixture"})
                return
            if request.method == "DELETE":
                assert path == f"/api/v1/refinement/sessions/{SESSION_ID}"
                query = parse_qs(urlsplit(request.url).query)
                assert int(query["expected_revision"][0]) == self.session["revision"]
                self.deleted = True
                self.requests.append({"path": path, "method": "DELETE"})
                route.fulfill(status=204)
                return
            if path.endswith("/events"):
                route.fulfill(body=": fixture heartbeat\n\n", content_type="text/event-stream")
                return
            if request.method == "POST":
                body = request.post_data_json
                self.requests.append({"path": path, **body})
                assert body["expected_revision"] == self.session["revision"], body
                self.session["revision"] += 1
                if path.endswith("/turns"):
                    turn = {
                        "id": f"{len(self.requests):032x}",
                        **body,
                        "status": "completed",
                        "diagnosis": {
                            "assistant_message": "界面回归模拟意见：P2 帽檐与额头交界需要检查。",
                            "needs_edit": True,
                            "mapping_uncertain": False,
                            "issues": [
                                {
                                    "category": "blending",
                                    "person_id": 2,
                                    "region": "帽檐与额头交界",
                                    "description": "此处有一段需要检查的明暗断层（界面模拟）",
                                    "evidence": "模拟样本展示诊断细节，不是真实画质判断",
                                    "suggestion": "柔化交界处过渡，保留帽子纹理和原有肤色",
                                    "severity": "medium",
                                    "confidence": "high",
                                    "uncertainty": "",
                                }
                            ],
                            "people": [],
                            "edit_plan": {
                                "changes": ["修复帽檐与额头过渡"],
                                "preserve": ["保留整体光影与帽子纹理"],
                                "instructions": "结合人工意见修复交界处。",
                            },
                            "uncertainties": [],
                        },
                        "created_at": STAMP,
                        "updated_at": STAMP,
                        "assistant_message": "界面回归模拟意见：先检查 P2 帽檐与额头交界；这不是真实模型诊断。",
                    }
                    if body["mode"] == "generate":
                        confirmed = next(t for t in self.session["turns"] if t["id"] == body["diagnosis_turn_id"])
                        assert confirmed["mode"] == "discuss"
                        assert confirmed["base_version_id"] == body["base_version_id"]
                        version = self.version(len(self.session["versions"]) + 1, body["base_version_id"])
                        self.session["versions"].append(version)
                        turn.update(
                            status="review_required",
                            candidate_version_id=version["id"],
                            review_error="模拟复评失败：候选仍保留，等待人工决定。",
                        )
                    self.session["turns"].append(turn)
                elif path.endswith("/accept"):
                    assert body["candidate_digest"] == self.digest
                    self.session["accepted_version_id"] = body["version_id"]
                    self.session["accepted_image_url"] = "/__ui-fixture/accepted.png"
                else:
                    raise AssertionError(f"Unexpected mutation in fixture: {path}")
            route.fulfill(json={"code": 200, "data": copy.deepcopy(self.session)})
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.forbidden.append(path)
            route.abort()
            return
        route.continue_()


def open_session(page):
    page.get_by_role("button", name="AI 精修", exact=True).click()
    page.get_by_role("button", name=re.compile(re.escape(TITLE))).first.click()
    expect(page.get_by_role("region", name="对话式 AI 精修工作区")).to_be_visible()
    expect(page.get_by_role("button", name="查看V2", exact=True)).to_be_visible()


def assert_image_fits(page):
    """Check actual browser geometry, not just CSS class names or thumbnail visibility."""
    page.wait_for_function("""() => {
        const stage = document.querySelector('[data-testid="refinement-image-scroll"]');
        const image = stage?.querySelector('[data-testid="comparison-viewport"], [data-testid="refinement-single-image"]');
        if (!stage || !image) return false;
        const a = stage.getBoundingClientRect(), b = image.getBoundingClientRect();
        return b.width > 100 && b.height > 100 && b.left >= a.left - 1 && b.top >= a.top - 1
            && b.right <= a.right + 1 && b.bottom <= a.bottom + 1;
    }""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/browser-refinement")
    parser.add_argument("--chrome", default="/opt/google/chrome/chrome")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = FixtureAPI()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.chrome, args=["--no-sandbox"])
        try:
            context = browser.new_context(viewport={"width": 1600, "height": 1050})
            context.route("**/*", fixture.route)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="domcontentloaded")
            open_session(page)
            page.get_by_role("button", name="查看V1", exact=True).click()
            page.get_by_role("button", name="以此版继续", exact=True).click()
            page.get_by_role("button", name="查看V2", exact=True).click()
            page.get_by_role("button", name="开始检查问题", exact=True).click()
            expect(page.get_by_role("log", name="精修对话")).to_contain_text("界面回归模拟意见")
            page.wait_for_timeout(400)  # Let the spring step indicator settle for the visual artifact.
            page.screenshot(path=str(args.output / "desktop-analysis.png"), full_page=True)
            assert fixture.requests[-1]["mode"] == "discuss"
            assert fixture.requests[-1]["base_version_id"] == "2" * 32
            assert len(fixture.session["versions"]) == 3
            assert fixture.session["accepted_version_id"] is None
            page.get_by_role("button", name="确认分析，填写意见", exact=True).click()
            page.get_by_role("textbox", name="精修意见").fill("基于 V1 修复 P2 帽檐边缘，保持这版整体构图。")
            page.get_by_role("button", name="结合分析与意见生成", exact=True).click()
            expect(page.get_by_role("button", name="查看V3", exact=True)).to_be_visible()
            assert fixture.requests[-1]["base_version_id"] == "2" * 32
            assert fixture.session["versions"][-1]["parent_version_id"] == "2" * 32
            expect(page.get_by_role("log", name="精修对话")).to_contain_text("模拟复评失败")
            page.get_by_role("button", name="查看V3", exact=True).click()
            assert_image_fits(page)
            page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
            page.get_by_role("button", name="接受当前 V3", exact=True).click()
            expect(page.get_by_role("link", name="导出已接受版本")).to_be_visible()
            assert fixture.session["accepted_version_id"] == "4" * 32
            assert sum(request.get("mode") == "generate" for request in fixture.requests) == 1
            page.get_by_role("button", name="以此版继续", exact=True).click()
            expect(page.get_by_role("button", name="开始检查问题", exact=True)).to_be_visible()
            expect(page.get_by_role("textbox", name="精修意见")).not_to_be_visible()
            page.get_by_role("button", name="开始检查问题", exact=True).click()
            page.get_by_role("button", name="确认分析，填写意见", exact=True).click()
            page.get_by_role("textbox", name="精修意见").fill("第二轮只修复残留边缘，保留第一轮的肤色。")
            page.get_by_role("button", name="结合分析与意见生成", exact=True).click()
            expect(page.get_by_role("button", name="查看V4", exact=True)).to_be_visible()
            assert fixture.session["versions"][-1]["parent_version_id"] == "4" * 32
            assert fixture.session["accepted_version_id"] == "4" * 32
            assert sum(request.get("mode") == "generate" for request in fixture.requests) == 2
            page.screenshot(path=str(args.output / "second-iteration.png"), full_page=True)
            page.reload(wait_until="domcontentloaded")
            open_session(page)
            expect(page.get_by_role("link", name="导出已接受版本")).to_be_visible()
            # Refresh retains the selected base; start another analysis only after explicitly advancing it.
            expect(page.get_by_test_id("base-version")).to_contain_text("V3")
            page.get_by_role("button", name="查看V4", exact=True).click()
            page.get_by_role("button", name="以此版继续", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="问题与意见", exact=True).click()
            expect(page.get_by_role("button", name="开始检查问题", exact=True)).to_be_visible()
            page.get_by_role("button", name="开始检查问题", exact=True).click()
            expect(page.get_by_role("button", name="确认分析，填写意见", exact=True)).to_be_visible()
            page.wait_for_timeout(400)  # Capture the settled spring indicator.
            page.screenshot(path=str(args.output / "mobile-confirmation.png"), full_page=True)
            page.get_by_role("button", name="确认分析，填写意见", exact=True).click()
            for name in ("只更新分析，暂不生成", "结合分析与意见生成"):
                assert page.get_by_role("button", name=name, exact=True).bounding_box()["height"] >= 44
            page.wait_for_timeout(400)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal mobile overflow"
            page.screenshot(path=str(args.output / "mobile-conversation.png"), full_page=True)
            page.get_by_role("button", name="画布与版本", exact=True).click()
            expect(page.get_by_role("button", name="查看V3", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal mobile overflow"
            assert_image_fits(page)
            page.screenshot(path=str(args.output / "mobile-canvas.png"), full_page=True)
            page.set_viewport_size({"width": 1600, "height": 1050})
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="删除当前精修会话", exact=True).click()
            expect(page.get_by_role("button", name="删除当前精修会话", exact=True)).not_to_be_visible()
            assert fixture.deleted
            assert sum(item.get("method") == "DELETE" for item in fixture.requests) == 1
            assert not fixture.forbidden and not errors, (fixture.forbidden, errors)
            report = {
                "discussion_without_generation": True,
                "explicit_old_version_branch": True,
                "review_failure_preserves_candidate": True,
                "human_acceptance": True,
                "confirmed_session_deletion": True,
                "history_reload": True,
                "mobile_controls": True,
                "simulated_generation_requests": 2,
                "iterates_from_generated_version": True,
                "confirmation_before_feedback": True,
                "real_model_requests": 0,
                "production_mutations": 0,
                "page_errors": errors,
            }
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))
        except Exception:
            if "page" in locals():
                page.screenshot(path=str(args.output / "failure.png"), full_page=True)
                print(page.locator("body").inner_text())
                print("PAGE ERRORS", errors)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
