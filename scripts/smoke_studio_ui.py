"""Non-production UI audit with fixture images and API interception; no real jobs or model calls.

Run through uv with the existing Playwright environment and a Vite development server.
Project autosave and session deletion use in-memory offline fixtures only; unrecognized
API writes still fail the audit. Screenshots and the scope report are written to --output.
"""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from smoke_refinement_workbench import FixtureAPI, open_session


def main():
    """Capture preparation, result, history and refinement at desktop and phone sizes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3010")
    parser.add_argument("--output", type=Path, default=Path("/tmp/personaweave-ui-audit"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = FixtureAPI()
    errors = []
    project_requests = []
    project = {"id": "c" * 32, "revision": 0, "title": "离线界面工程", "updated_at": "2026-09-30T00:00:00Z"}
    project_path = "/api/v1/projects/" + project["id"]
    project_images = {
        "/__ui-fixture/original.png": fixture.image,
        "/__ui-fixture/reference.png": fixture.reference,
        **{f"/__ui-fixture/version-{index}.png": fixture.image for index in range(1, 4)},
    }

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/health"):
            route.fulfill(json={"status": "ok", "pipeline_ready": True, "ai_refinement_ready": True})
        elif path == "/api/v1/task-history/batch-tasks":
            route.fulfill(
                json={
                    "code": 200,
                    "data": [
                        {
                            "task_id": "example-batch-20260910",
                            "status": "COMPLETED",
                            "total_images": 2,
                            "processed_images": 2,
                            "viewable_results": 2,
                            "created_at": "2026-09-10T10:00:00Z",
                        }
                    ],
                }
            )
        elif path == "/api/v1/projects" and route.request.method == "POST":
            project_requests.append({"path": path, "method": "POST"})
            route.fulfill(json={"data": project})
        elif path == project_path + "/assets" and route.request.method == "POST":
            # Accept only this audit's known images. Do not blanket-allow project writes
            # or fetch arbitrary inputs through a running backend.
            body = route.request.post_data_json
            assert set(body) == {"image"} and body["image"] in project_images, "Unexpected fixture image upload"
            digest = hashlib.sha256(project_images[body["image"]]).hexdigest()
            project_requests.append({"path": path, "method": "POST"})
            route.fulfill(json={"data": [{"$asset": digest}]})
        elif path == project_path and route.request.method == "PUT":
            body = route.request.post_data_json
            assert body["expected_revision"] == project["revision"], "Unexpected fixture project revision"
            assert body["document"]["schema"] == 1, "Unexpected fixture project schema"
            project.update(revision=project["revision"] + 1, title=body["title"])
            project_requests.append({"path": path, "method": "PUT"})
            route.fulfill(json={"data": project})
        elif path == "/api/v1/projects" and route.request.method == "GET":
            route.fulfill(json={"data": [project] if project["revision"] else []})
        elif path.startswith("/api/") and not (
            path.startswith("/api/v1/refinement/sessions") or path == "/api/v1/ai/models"
        ):
            if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                fixture.forbidden.append(path)
            route.fulfill(json={"code": 200, "data": []})
        else:
            fixture.route(route)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/google/chrome/chrome", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        page.screenshot(path=str(args.output / "desktop-empty.png"))
        page.evaluate("""async () => {
            const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
            const photo = new Image(); photo.src = '/__ui-fixture/original.png'; await photo.decode();
            useStudioStore.setState({ sourcePeople:[{id:1,name:'示例人物'}],activeSourcePersonId:1,
              sourceGallery:[{id:'ref',name:'人物参考',kind:'manual',personId:1,previewUrl:'/__ui-fixture/reference.png',imageData:'/__ui-fixture/reference.png'}],
              targetImages:[1,2].map(n => ({id:'target'+n,name:'林间合影 · '+n+'.png',url:'/__ui-fixture/original.png',status:'IDLE',imageWidth:photo.naturalWidth,imageHeight:photo.naturalHeight,
                faces:[{index:1,global_person_id:1,bbox:[photo.naturalWidth*.20,photo.naturalHeight*.53,photo.naturalWidth*.28,photo.naturalHeight*.62]}]})), activeTargetIndex:0,
              personMappings:[{person_index:1,label:'示例人物',target_reference_url:'/__ui-fixture/original.png',target_reference_person_index:1,source_manual_images:['/__ui-fixture/reference.png'],source_asset_ids:[]}]
            });
            const {saveProject} = await import('/src/features/projects/projectController.ts');
            await saveProject();
        }""")
        for width, height in [(1440, 900), (1024, 768), (390, 844), (320, 740)]:
            page.set_viewport_size({"width": width, "height": height})
            page.screenshot(path=str(args.output / f"prepare-{width}.png"))
            if args.verify:
                expect(page.get_by_role("button", name="准备与换脸", exact=True)).to_be_visible()
                for button in page.get_by_role("group", name="画布工具").get_by_role("button").all():
                    assert button.bounding_box()["height"] <= 44
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                footer = page.get_by_role("contentinfo", name="任务状态栏")
                expect(footer.get_by_role("button")).to_have_count(0)
            if width == 390:
                page.get_by_role("button", name="打开换脸控制面板").click()
                page.screenshot(path=str(args.output / "mobile-controls.png"))
                page.get_by_role("button", name="关闭换脸控制面板").click()
        page.evaluate("""async () => {
            const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
            useStudioStore.getState().updateTargetImageById('target1', {status:'SUCCESS', processedUrl:'/__ui-fixture/version-1.png', costMs:1500});
        }""")
        for width in [1440, 390, 320]:
            page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
            page.screenshot(path=str(args.output / f"result-{width}.png"))
            if args.verify:
                for name in ["AI 精修当前图", "下载当前结果"]:
                    button = page.get_by_role("button", name=name, exact=True)
                    expect(button).to_be_visible()
                    box = button.bounding_box()
                    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (width, name, box)
        for width in [1440, 390]:
            page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
            page.get_by_role("button", name="历史", exact=True).click()
            expect(page.get_by_role("heading", name="历史与定稿")).to_be_visible()
            page.screenshot(path=str(args.output / f"history-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        open_session(page)
        page.screenshot(path=str(args.output / "refinement-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.output / "refinement-mobile.png"))
        page.get_by_role("button", name="问题与意见", exact=True).click()
        page.screenshot(path=str(args.output / "refinement-composer-mobile.png"))

        # Delete a linked accepted fixture through the real UI. Its server URL is not
        # an independently saved image: the workspace must expose the loss and block export.
        page.set_viewport_size({"width": 1440, "height": 900})
        fixture.session["target_id"] = "target1"
        fixture.session["accepted_version_id"] = "2" * 32
        fixture.session["accepted_image_url"] = "/__ui-fixture/version-2.png"
        page.evaluate(
            """async session => {
                const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
                useStudioStore.getState().updateTargetImageById('target1', {
                    refinementSessionId:session.id, refinedUrl:session.accepted_image_url,
                    acceptedRefinement:{sessionId:session.id,versionId:session.accepted_version_id,revision:session.revision}
                });
            }""",
            fixture.session,
        )
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="删除当前精修会话", exact=True).click()
        page.wait_for_function("""async () => {
            const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
            return useStudioStore.getState().targetImages[0].refinementResultUnavailable === true;
        }""")
        assert fixture.deleted
        page.get_by_role("button", name="准备与换脸", exact=True).click()
        warning = "已接受的精修结果已不可用；当前显示本地初稿，不能作为最终精修结果导出。"
        for width in [1440, 390, 320]:
            page.set_viewport_size({"width": width, "height": 900 if width == 1440 else 844})
            if args.verify:
                expect(page.get_by_text(warning, exact=True)).to_be_visible()
                download = page.get_by_role("button", name="下载当前结果", exact=True)
                expect(download).to_be_visible()
                expect(download).to_be_disabled()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(args.output / f"deleted-result-{width}.png"))
        page.evaluate("""async () => {
            const {saveProject} = await import('/src/features/projects/projectController.ts');
            await saveProject();
        }""")
        assert not errors, errors
        assert not fixture.forbidden, fixture.forbidden
        report = {
            "passed": True,
            "scope": "Vite development frontend with intercepted offline APIs; not native Windows or real backend acceptance",
            "page_errors": errors,
            "production_mutations": 0,
            "ai_requests": 0,
            "fixture_project_mutations": len(project_requests),
            "fixture_session_deletions": sum(request.get("method") == "DELETE" for request in fixture.requests),
            "deleted_result_checks": [1440, 390, 320] if args.verify else [],
            "screenshots": len(list(args.output.glob("*.png"))),
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        browser.close()


if __name__ == "__main__":
    main()
