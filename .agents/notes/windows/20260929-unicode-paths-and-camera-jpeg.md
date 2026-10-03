# 中文路径用内存编解码，相机 MPO/JPG 只取主图

状态：有效
日期：2026-09-29

## 背景

两个在原生 Windows 上暴露、Linux 开发环境看不到的问题：
- 中文目录下文件存在，但 `cv2.imread` 返回空，表现为 `Failed to read managed local image`、工程素材保存 422、结果写入失败。
- 相机拍的 `.JPG` 带 MPF 信息，被 Pillow 识别为 MPO；旧逻辑在模型推理前报 `Image format is not allowed`，导致检测 400、工程图片归档 422。

## 决策

- 素材、参考图和结果文件统一由 Python 打开，再用 OpenCV 内存编解码，不把路径交给 `cv2.imread/imwrite`（`utils/image_io.py`）。
- MPO 按主图读取，不拆辅助帧；工程保留原始字节，回看以 `image/jpeg` 提供；20 MiB、4000 万像素和内存检查仍然生效，不放开其他图片格式。

## 被放弃的方案

- **只在 Windows 上给路径转码或改用短路径**：绕开而不是消除问题，且遗漏写入路径。
- **放开更多图片格式或取消 MPO 限制**：扩大攻击面，只需要兼容主图。
- **把 MPO 辅助帧当成另一张目标照片**：会把一张照片误当成多张。

## 影响与验证

规则见 [windows-portable.md](../../../docs/windows-portable.md)；回归 `tests/test_image_io.py`、`tests/test_image_security.py`、`tests/test_projects.py`、`tests/test_package_boundaries.py`，诊断脚本 `scripts/diagnose_camera_mpo.py`。原生 Windows 同文件复现：5568×3712、9,978,954 字节的 MPO/JPG（SHA-256 `4380035d36dbf7dacc9a5aae2d4e4485344f1faf640e0db06f73c96532caf895`），修复前检测/聚类 400、素材归档 422，修复后检出 3 张人脸、聚类、归档、工程保存恢复与媒体回看均 200 且字节一致。中文目录复现：输入素材摘要 `4e1e10c5…4dad`，直接 `imread` 返回空、字节解码为 1024×1024，修复后读取、PNG 无损往返、CLI 参考特征读取和真实换脸均通过。
