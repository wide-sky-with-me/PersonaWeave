"""Non-production browser regression: local and batch failures must open persistent dialogs.

All API/model requests are intercepted. This checks the real UI without creating user jobs.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from smoke_refinement_workbench import FixtureAPI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3011")
    parser.add_argument("--output", type=Path, default=Path("outputs/browser-swap-errors"))
    parser.add_argument(
        "--queue", action="store_true", help="Verify nine-image upload and memory waiting instead of errors"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = FixtureAPI()
    calls = []
    project_id = "b" * 32
    queue = {"released": False, "ids": []}

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path == "/api/v1/health":
            route.fulfill(json={"status": "ok", "pipeline_ready": True, "ai_refinement_ready": True})
        elif path == "/api/v1/swap-sync":
            calls.append(path)
            route.fulfill(status=400, json={"detail": "人物 #1：目标为极端侧脸，但素材库缺少同方向侧脸参考"})
        elif path == "/api/v1/batch-tasks":
            calls.append(path)
            queue["ids"] = [item["client_target_id"] for item in route.request.post_data_json["batch_images"]]
            route.fulfill(json={"code": 200, "data": {"task_id": "ui-batch"}})
        elif path == "/api/v1/batch-tasks/ui-batch" and args.queue:
            done = queue["released"]
            route.fulfill(
                json={
                    "code": 200,
                    "data": {
                        "task_id": "ui-batch",
                        "status": "COMPLETED" if done else "PROCESSING",
                        "progress": 1 if done else 0,
                        "total_images": 9,
                        "processed_images": 9 if done else 0,
                        "success_count": 9 if done else 0,
                        "failure_count": 0,
                        "has_partial_failure": False,
                        "waiting_reason": None if done else "等待可用内存：请关闭其他占内存程序。",
                        "created_at": "2026-09-23T00:00:00Z",
                        "results": [
                            {
                                "client_target_id": id,
                                "status": "SUCCESS",
                                "faces_detected": 1,
                                "faces_swapped": 1,
                                "processed_url": "/__ui-fixture/original.png",
                            }
                            for id in queue["ids"]
                        ]
                        if done
                        else [],
                    },
                }
            )
        elif path == "/api/v1/batch-tasks/ui-batch":
            route.fulfill(
                json={
                    "code": 200,
                    "data": {
                        "task_id": "ui-batch",
                        "status": "FAILED",
                        "progress": 1,
                        "total_images": 2,
                        "processed_images": 2,
                        "success_count": 0,
                        "failure_count": 2,
                        "has_partial_failure": True,
                        "created_at": "2026-09-23T00:00:00Z",
                        "results": [
                            {"client_target_id": "t1", "status": "FAILED", "error": "缺少同方向侧脸参考"},
                            {"client_target_id": "t2", "status": "FAILED", "error": "未检测到可用人脸"},
                        ],
                    },
                }
            )
        elif path == "/api/v1/detect-faces":
            route.fulfill(
                json={
                    "code": 200,
                    "data": {
                        "faces": [{"index": 1, "global_person_id": 1, "bbox": [20, 20, 150, 180]}],
                        "image_width": 512,
                        "image_height": 512,
                    },
                }
            )
        elif path == "/api/v1/cluster-faces":
            count = len(route.request.post_data_json["image_urls"])
            route.fulfill(
                json={
                    "code": 200,
                    "data": {
                        "images": [
                            {
                                "image_index": i,
                                "image_width": 512,
                                "image_height": 512,
                                "faces": [{"index": 1, "global_person_id": 1, "bbox": [20, 20, 150, 180]}],
                            }
                            for i in range(count)
                        ]
                        if args.queue
                        else []
                    },
                }
            )
        elif path.startswith("/api/v1/projects"):
            body = route.request.post_data_json if route.request.method in {"POST", "PUT"} else {}
            if path.endswith("/assets"):
                data = {"$asset": fixture.digest}
            elif route.request.method == "GET":
                data = []
            else:
                data = {
                    "id": project_id,
                    "revision": 1,
                    "title": "界面验证",
                    "document": (body or {}).get("document", {}),
                }
            route.fulfill(json={"code": 200, "data": data})
        elif path.startswith("/api/"):
            route.fulfill(json={"code": 200, "data": []})
        else:
            fixture.route(route)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/google/chrome/chrome", headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.route("**/*", route_request)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""(() => {
              const original = window.createImageBitmap.bind(window);
              window.bitmapProbe = {active:0, peak:0, closed:0};
              window.createImageBitmap = async (...args) => {
                window.bitmapProbe.active++; window.bitmapProbe.peak = Math.max(window.bitmapProbe.peak, window.bitmapProbe.active);
                const bitmap = await original(...args), close = bitmap.close.bind(bitmap);
                bitmap.close = () => {window.bitmapProbe.active--; window.bitmapProbe.closed++; close();};
                return bitmap;
              };
            })()""")
            page.goto(args.url, wait_until="networkidle")
            if args.queue:
                page.evaluate("""async () => {
                  const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
                  useStudioStore.setState({sourcePeople:[{id:1,name:'测试人物'}],activeSourcePersonId:1,
                    sourceGallery:[{id:'r1',name:'参考',kind:'manual',personId:1,imageData:'/__ui-fixture/reference.png'}]});
                }""")
                page.locator('input[type="file"][multiple][accept="image/*"]').last.set_input_files(
                    [
                        {"name": f"队列测试-{i + 1}.png", "mimeType": "image/png", "buffer": fixture.image}
                        for i in range(9)
                    ]
                )
                start = page.get_by_role("button", name="启动批量换脸 (9 张)", exact=True)
                expect(start).to_be_enabled(timeout=15000)
                probe = page.evaluate("window.bitmapProbe")
                assert probe == {"active": 0, "peak": 1, "closed": 9}, probe
                start.click()
                expect(page.get_by_text("等待可用内存", exact=False).first).to_be_visible()
                page.wait_for_timeout(350)
                page.screenshot(path=str(args.output / "memory-waiting.png"))
                queue["released"] = True
                expect(page.get_by_label("任务状态栏")).to_contain_text("已完成 9 / 9", timeout=15000)
                assert calls == ["/api/v1/batch-tasks"] and not errors, (calls, errors)
                (args.output / "queue-report.json").write_text(
                    json.dumps(
                        {
                            "bitmap_probe": probe,
                            "batch_submissions": len(calls),
                            "real_model_requests": 0,
                            "browser_errors": errors,
                        },
                        indent=2,
                    )
                )
                return

            page.evaluate("""async () => {
              const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
              const reference = '/__ui-fixture/reference.png', original = '/__ui-fixture/original.png';
              useStudioStore.setState({sourcePeople:[{id:1,name:'测试人物'}], activeSourcePersonId:1,
                sourceGallery:[{id:'r1',name:'参考',kind:'manual',personId:1,previewUrl:reference,imageData:reference}],
                targetImages:[{id:'t1',name:'侧脸测试.jpg',url:original,status:'IDLE',faces:[{index:1,global_person_id:1,bbox:[20,20,150,180]}]}],
                activeTargetIndex:0,personMappings:[{person_index:1,target_reference_url:original,target_reference_person_index:1,source_manual_images:[reference],source_asset_ids:[]}]});
            }""")
            page.get_by_role("button", name="开始单图换脸", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_contain_text("缺少同方向侧脸参考")
            page.wait_for_timeout(5500)
            expect(dialog).to_be_visible()
            page.screenshot(path=str(args.output / "single-failure-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            expect(dialog.get_by_role("button", name="知道了")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(args.output / "single-failure-mobile.png"))
            dialog.get_by_role("button", name="知道了").click()
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("""async () => {
              const {useStudioStore} = await import('/src/stores/useStudioStore.ts');
              const first = useStudioStore.getState().targetImages[0];
              useStudioStore.setState({targetImages:[{...first,status:'IDLE'}, {...first,id:'t2',name:'遮挡测试.jpg',status:'IDLE'}]});
            }""")
            page.get_by_role("button", name="启动批量换脸 (2 张)", exact=True).click()
            expect(dialog).to_contain_text("侧脸测试.jpg：缺少同方向侧脸参考")
            expect(dialog).to_contain_text("遮挡测试.jpg：未检测到可用人脸")
            page.wait_for_timeout(350)  # Wait for the dialog entrance spring before the visual assertion.
            page.screenshot(path=str(args.output / "batch-failure.png"))
            dialog.get_by_role("button", name="知道了").click()
            page.wait_for_timeout(1500)
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert not errors, errors
            assert calls == ["/api/v1/swap-sync", "/api/v1/batch-tasks"], calls
            (args.output / "report.json").write_text(
                json.dumps({"requests": calls, "paid_requests": 0, "browser_errors": errors}, indent=2)
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
