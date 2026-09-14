"""Non-production browser audit: real project storage, fixture editor/model state, no paid calls.

Creates and deletes only its own temporary project. Existing tasks/sessions are never mutated.
Run with uv and the existing Playwright/Chrome installation; no dependencies are installed here.
"""

import argparse
import base64
import json
import struct
import zlib
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from smoke_refinement_workbench import SESSION_ID, FixtureAPI, open_session

ROOT = Path(__file__).resolve().parents[1]


def data_uri(data):
    return "data:image/png;base64," + base64.b64encode(data).decode()


def mask(value):
    # A tiny synthetic grayscale fixture, generated without adding imaging dependencies.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = (b"\x00" + bytes([value]) * 64) * 64
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 0, 0, 0, 0))
    return data_uri(data + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/browser-project-recovery")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = FixtureAPI()
    errors, blocked, writes = [], [], []
    project_id = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"]
        )

        def route(request):
            path = urlsplit(request.request.url).path
            if path.startswith("/api/v1/projects"):
                if request.request.method not in {"GET", "HEAD", "OPTIONS"}:
                    writes.append({"method": request.request.method, "path": path})
                request.continue_()
                return
            if (
                path.startswith("/api/v1/refinement/")
                or path.startswith("/__ui-fixture/")
                or path == "/api/v1/ai/models"
                or "fonts." in request.request.url
            ):
                fixture.route(request)
                return
            if request.request.method not in {"GET", "HEAD", "OPTIONS"}:
                blocked.append(path)
                request.abort()
                return
            request.continue_()

        def context():
            ctx = browser.new_context(viewport={"width": 1600, "height": 1050})
            ctx.route("**/*", route)
            return ctx

        def ready(page):
            page.wait_for_function(
                """async()=>{const {useProjectStore:p}=await import('/src/features/projects/projectStore.ts');return p.getState().status==='idle' && (!localStorage.getItem('personaweave-current-project') || p.getState().id===localStorage.getItem('personaweave-current-project')); }""",
                timeout=120000,
            )

        def state(page):
            return page.evaluate(
                """async()=>{const {studioSnapshot:s}=await import('/src/features/projects/projectSnapshot.ts');const {useWorkbenchStore:w}=await import('/src/features/refinement/workbenchStore.ts');const {useProjectStore:p}=await import('/src/features/projects/projectStore.ts');return {studio:s(),selections:w.getState().selections,project:p.getState().id};}"""
            )

        ctx = context()
        page = ctx.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            ready(page)
            image, reference = data_uri(fixture.image), data_uri(fixture.reference)
            seed = {
                "image": image,
                "reference": reference,
                "current": mask(128),
                "undo": mask(0),
                "redo": mask(255),
                "session": SESSION_ID,
            }
            page.evaluate(
                """async(v)=>{const {useStudioStore:s}=await import('/src/stores/useStudioStore.ts');s.setState({
              sourcePeople:[{id:7,name:'工程恢复测试人物'}],activeSourcePersonId:7,
              sourceGallery:[{id:'ref',personId:7,kind:'manual',name:'冻结参考',imageData:v.reference}],
              targetImages:[{id:'ui-only',name:'工程恢复浏览器验证',url:v.image,processedUrl:v.image,status:'SUCCESS',imageWidth:1067,imageHeight:1600,
                faces:[{index:2,global_person_id:9,bbox:[200,100,300,250]}],refinementSessionId:v.session,
                manualMask:{dataUrl:v.current,undoStack:[null,v.undo],redoStack:[v.redo]}}],
              parameters:{...s.getState().parameters,identity_blend_strength:.67},brushSize:47,isEraser:true,
              viewMode:'BRUSH',splitPosition:37,showFaceMarkers:false,
              personMappings:[{person_index:9,target_reference_person_index:2,target_reference_url:v.image,source_asset_ids:[],source_manual_images:[v.reference],source_mode:'gallery'}]
            });}""",
                seed,
            )
            page.wait_for_timeout(1000)
            ready(page)
            project_id = state(page)["project"]
            assert project_id
            open_session(page)
            page.get_by_role("button", name="查看V1", exact=True).click()
            page.get_by_role("button", name="以此版继续", exact=True).click()
            page.get_by_role("button", name="开始检查问题", exact=True).click()  # Strictly fixture-only discussion.
            page.get_by_role("button", name="确认分析，填写意见", exact=True).click()
            page.get_by_role("textbox", name="精修意见").fill("保留 V1 作为修改基图；这是尚未提交的工程草稿。")
            page.get_by_role("button", name="查看V2", exact=True).click()
            page.wait_for_timeout(1000)
            ready(page)
            before = state(page)
            page.screenshot(path=str(args.output / "before-refresh.png"), full_page=True)
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_label("工程保存").get_by_role("status")).to_have_text("已保存到服务器", timeout=120000)
            page.wait_for_function(
                """async(id)=>{const {useProjectStore:p}=await import('/src/features/projects/projectStore.ts');return p.getState().id===id;}""",
                arg=project_id,
                timeout=120000,
            )
            ready(page)
            expect(page.get_by_test_id("view-version")).to_contain_text("V2")
            expect(page.get_by_test_id("base-version")).to_contain_text("V1")
            expect(page.get_by_role("textbox", name="精修意见")).to_have_value(
                "保留 V1 作为修改基图；这是尚未提交的工程草稿。"
            )
            after = state(page)
            (args.output / "before-state.json").write_text(json.dumps(before, ensure_ascii=False))
            (args.output / "after-state.json").write_text(json.dumps(after, ensure_ascii=False))
            assert before["studio"] == after["studio"], "Studio state changed after refresh: " + str(
                [k for k in before["studio"] if before["studio"][k] != after["studio"].get(k)]
            )
            assert before["selections"] == after["selections"], "Refinement draft changed after refresh"
            page.screenshot(path=str(args.output / "refinement-restored.png"), full_page=True)
            # No browser-local pointer or sessionStorage is carried to this fresh context.
            second = context()
            fresh = second.new_page()
            fresh.on("pageerror", lambda e: errors.append(str(e)))
            fresh.goto(args.url, wait_until="domcontentloaded")
            ready(fresh)
            fresh.get_by_role("button", name="历史", exact=True).click()
            fresh.get_by_role("button", name="恢复工程 " + project_id, exact=True).click()
            ready(fresh)
            expect(fresh.get_by_test_id("base-version")).to_contain_text("V1")
            assert state(fresh)["studio"] == before["studio"]
            assert state(fresh)["selections"].get(SESSION_ID) == before["selections"][SESSION_ID]
            fresh.get_by_role("button", name="准备与换脸", exact=True).click()
            fresh.wait_for_timeout(500)
            fresh.screenshot(path=str(args.output / "preparation-restored.png"), full_page=True)
            # The actual undo/redo controls use the restored history, not only a matching final mask.
            fresh.get_by_role("button", name="撤销遮罩编辑", exact=True).click()
            assert state(fresh)["studio"]["targetImages"][0]["manualMask"]["dataUrl"] == seed["undo"]
            fresh.get_by_role("button", name="重做遮罩编辑", exact=True).click()
            assert state(fresh)["studio"]["targetImages"][0]["manualMask"]["dataUrl"] == seed["current"]
            fresh.wait_for_timeout(1000)
            ready(fresh)
            fresh.set_viewport_size({"width": 390, "height": 844})
            fresh.get_by_role("button", name="历史", exact=True).click()
            fresh.wait_for_timeout(500)
            assert fresh.evaluate("document.documentElement.scrollWidth <= innerWidth")
            fresh.screenshot(path=str(args.output / "mobile-history.png"), full_page=True)
            assert not blocked, blocked
            assert not errors, errors
            assert not any(r.get("mode") == "generate" for r in fixture.requests)
            report = {
                "same_tab_refresh": True,
                "fresh_browser_history_restore": True,
                "all_studio_fields_equal": True,
                "version_base_draft_confirmation_preserved": True,
                "brush_undo_redo_verified": True,
                "mobile_no_overflow": True,
                "real_project_storage": True,
                "real_model_calls": 0,
                "simulated_discussions": 1,
                "page_errors": errors,
                "project_id": project_id,
                "blocked_mutations": blocked,
                "project_requests": writes,
            }
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False))
        except BaseException:
            page.screenshot(path=str(args.output / "failure.png"), full_page=True)
            print("Page errors:", errors, "Blocked:", blocked)
            print(page.locator("body").inner_text()[:8000])
            raise
        finally:
            # Only this script's project is removed, including its fake fixture-session association.
            if project_id:
                response = page.request.get(args.url + "/api/v1/projects/" + project_id)
                if response.ok:
                    revision = response.json()["data"]["revision"]
                    page.request.delete(args.url + f"/api/v1/projects/{project_id}?expected_revision={revision}")
            browser.close()


if __name__ == "__main__":
    main()
