"""Whole-image generative refinement with structured VLM diagnosis and review."""

import base64

import cv2
import numpy as np

from core.ai_gateway import AIGateway
from core.vlm_contracts import BatchFaceReview, WholeImageQualityReview


class RefinementService:
    """Keep prompts, whole-image generation, and advisory review provider-independent."""

    def __init__(self, gateway=None):
        self.gateway = gateway or AIGateway()

    @property
    def configured(self):
        return self.gateway.configured

    @staticmethod
    def encode_image(image, max_dim=1600, lossless=False):
        height, width = image.shape[:2]
        if max(height, width) > max_dim:
            scale = max_dim / max(height, width)
            image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        extension = ".png" if lossless else ".jpg"
        params = [] if lossless else [cv2.IMWRITE_JPEG_QUALITY, 90]
        ok, buffer = cv2.imencode(extension, image, params)
        if not ok:
            raise ValueError("Could not encode image for AI gateway")
        mime = "image/png" if lossless else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(buffer).decode("ascii")

    @staticmethod
    def decode_image(image_bytes):
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Image provider returned undecodable image bytes")
        return image

    async def review(self, swapped, people, alias=None, original=None):
        """Diagnose every selected person in one full-image VLM call.

        The VLM produces a concise visible locator plus a detailed defect list. No crop,
        source identity, or local geometry enters the generative refinement contract.
        """
        resolved_alias = alias or self.gateway.vision_alias("refinement")
        targets = []
        authoritative_locators = {}
        for person in people:
            focus = person.user_focus.strip() or "自动全面检查，不预设缺陷"
            locator = getattr(person, "person_locator", "").strip()
            if not locator:
                locator = f"从左到右第 {person.face_index} 张脸"
            authoritative_locators[person.person_id] = locator
            targets.append(f"PERSON #{person.person_id}: 视觉位置线索：{locator}；用户关注点：{focus}")
        target_text = "\n".join(targets)
        comparison_context = (
            "Image 1 is the complete target photo before face swapping. Image 2 is the complete current face-swapped "
            "photo. Compare the requested faces across both images: preserve the target's pose, expression, gaze, head/body "
            "geometry and scene lighting while checking whether the swap introduced implausible anatomy or blending. Identity "
            "change itself is requested and must not be reported as a defect."
            if original is not None
            else "The image is the complete current face-swapped photo; no pre-swap comparison image is available."
        )
        content = [
            {
                "type": "text",
                "text": (
                    f"{comparison_context} Use these complete images as the only source of visual evidence for "
                    "composition, person location and visible defects. Use defect-first hypothesis testing: begin by assuming "
                    "each requested face contains at least one synthetic or compositing anomaly, then actively try to confirm "
                    "or falsify that hypothesis in every inspection category. Review every requested PERSON independently in "
                    f"this one call. The requested people are:\n{target_text}\n"
                    "For each person inspect systematically: facial anatomy and proportions; bilateral feature coordination; "
                    "eye gaze, pupils and catchlights; nose, mouth, teeth and lips; expression coherence; skin texture; hairline, "
                    "ears, jaw and neck boundaries; head-to-body scale, pose and perspective; illumination, skin tone, grain, "
                    "sharpness, seams, halos, ghosting and duplicated features. Distinguish true defects from normal asymmetry, "
                    "makeup, motion blur, depth of field, sensor noise and scene lighting. Do not invent invisible issues."
                ),
            },
        ]
        if original is not None:
            content.append({"type": "image_url", "image_url": {"url": self.encode_image(original)}})
        content.append({"type": "image_url", "image_url": {"url": self.encode_image(swapped)}})
        system_prompt = """You are a senior portrait retoucher and forensic visual-quality reviewer. You may receive a complete pre-swap target followed by the complete current swap; when both are present, use their difference to detect newly introduced gaze, anatomy, pose, scale, lighting and boundary defects without treating the requested identity change as an error. Review in two mental passes: assess scene-level compatibility, then inspect each requested face in the complete image. Always report positions relative to the complete image. The supplied visual position clue is authoritative: first locate the exact row and the within-row left-to-right position stated in that clue, then inspect only that person's face. Do not reinterpret a within-row position as a global left-to-right face index, and do not move a report to a nearby person. Return ONLY one valid json object; the root JSON value MUST be an object, never an array. That object MUST contain exactly one top-level key named \"reports\", whose value is an array with exactly one report for every requested PERSON and no extra people. The required root structure is {\"reports\": [{...}]}. Do not return the reports array directly and do not wrap the JSON in Markdown code fences. Each report must contain: person_id as the JSON integer copied from the requested PERSON number (for PERSON #1 return 1, never return \"PERSON #1\"); overall_score, anatomy_score, coherence_score, naturalness_score, lighting_score (all 0-100); person_locator; issues as an array of objects with category, severity (low|medium|high), region, description, suggestion; strengths as an array of strings (use [] when there are none, never return one string); recommended_prompt; should_refine. person_locator must copy the supplied visual position clue exactly; do not append clothing, pose, age, gender, occupation, identity, ethnicity, nearby people, or other free-form identifying guesses. Score conservatively from visible evidence. anatomy_score measures plausible facial geometry and feature relationships. coherence_score measures expression, gaze, head/neck/body, perspective, scale and scene integration. naturalness_score measures skin detail, boundary blending and continuity of grain, noise, compression texture, sharpness and resolution across face edges. lighting_score measures illumination plus facial skin-tone consistency with ears, neck, hands and other visible skin under the same light. Sort issues by severity and impact; report at most six non-duplicate actionable defects per person. recommended_prompt must be one concise Chinese incremental image-edit instruction naming exact regions and operations; preserve the current person, expression, pose, hairstyle, clothing, background, text and composition; retain natural pores, grain and asymmetry; forbid over-smoothing and global redesign. If no material defect is visible, say to keep the image unchanged and set should_refine=false."""
        system_prompt += """ Use a defect-first forensic protocol. For every requested person, initialize the working hypothesis as ANOMALOUS and audit every category before considering a clean verdict: (1) geometry/anatomy, (2) bilateral feature coordination and gaze, (3) skin and micro-texture, (4) face-boundary blending, (5) lighting and color continuity, (6) head/neck/body pose, scale and perspective, and (7) synthesis artifacts such as seams, halos, ghosting, duplicated features or local resolution changes. For each category, actively search for positive anomaly evidence and only clear that category when visible evidence supports normality. Do not treat failure to notice a defect as proof that none exists. After all categories, attempt to falsify every suspected issue against benign explanations such as normal asymmetry, makeup, depth of field, motion blur, compression and scene lighting; report only issues that survive this falsification step. Apply a forensic acceptance bar, not a courtesy rating. Start each dimension at 70 and add points only for clearly visible evidence of excellence; do not default to 90+. A recommendation to keep unchanged is allowed only when all seven categories have been explicitly examined, issues is empty, and every dimension score is at least 92. Treat any visible seam, halo, double edge, warped feature, inconsistent pupil/catchlight, waxy or repeated skin texture, local blur/sharpness mismatch, color-temperature mismatch, implausible anatomy, head/neck discontinuity, or face-to-body scale/perspective mismatch as actionable. If image resolution prevents reliable inspection, score the affected dimension below 92 and request a conservative refinement rather than declaring perfection. Any issue, including low severity, requires should_refine=true and a concrete recommended_prompt."""
        system_prompt += """ Every issue must be reported with forensic specificity; vague verdicts are not acceptable output. Each issue's category MUST be exactly one of these seven strings, matching the seven audit categories above in order: "anatomy_geometry", "bilateral_symmetry_gaze", "skin_texture", "boundary_blending", "lighting_color", "pose_perspective_scale", "synthesis_artifact". The fields region, description and suggestion MUST be written in Chinese. region MUST name one precise anatomical anchor, with left/right side whenever the feature is bilateral (e.g. "右眼内眼角", "双眼瞳孔与视线方向", "下颌与颈部交界左侧", "左侧太阳穴发际线边缘", "人中与上唇交界处"); generic words alone such as "双眼"、"脸部"、"这里"、"眼睛区域"、"面部"、"皮肤" are forbidden as a complete region. description MUST state in one sentence: (a) exactly what the anomaly visually looks like -- its shape, color, edge behavior or texture pattern; (b) its approximate visible extent as a relative, visually-estimable size -- a fraction of a nearby feature's width/length, or a rough millimeter/centimeter guess -- never a bare intensity word like "轻微"、"有点"、"稍微"、"不太"、"有些" standing alone with nothing else; and (c) what it is being compared against -- the symmetric side, the surrounding skin, or plausible anatomy. Silently rewrite and never output a description that is only a generic verdict such as "看起来不自然"、"边缘处理不好"、"有瑕疵"、"不太协调"、"不自然" with no further visual detail; that phrasing alone never satisfies this field even once. suggestion MUST name the same specific region and a concrete corrective operation, not a generic instruction like "修一下"、"优化一下"、"处理得更自然". recommended_prompt MUST reuse the same precise region names introduced in issues rather than reverting to vague terms. Example of an UNACCEPTABLE issue that must never be produced: {"category": "boundary_blending", "severity": "medium", "region": "脸部边缘", "description": "边缘有点不自然", "suggestion": "修一下边缘"}. Example of an ACCEPTABLE issue at the required specificity: {"category": "boundary_blending", "severity": "medium", "region": "右耳前发际线与太阳穴交界处", "description": "该处有一条约1厘米长的浅灰色直线状色带，边缘僵硬，与周围发丝自然的不规则锯齿边缘不连续，疑似换脸贴合边界残留", "suggestion": "沿右耳前发际线与太阳穴交界处重新羽化过渡，使发丝边缘呈现自然不规则锯齿而非当前的直线色带"}. Every issue you output must reach the specificity of the acceptable example, never the unacceptable one. Before returning JSON, verify that every issue includes severity and that no required field is missing."""
        response, batch = await self.gateway.structured_vision_completion(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            contract=BatchFaceReview,
            alias=resolved_alias,
            temperature=0.1,
            response_format={"type": "json_object"},
            timeout=110,
        )
        expected = {person.person_id for person in people}
        result = {}
        for report in batch.reports:
            if report.person_id not in expected or report.person_id in result:
                raise ValueError("VLM batch review returned an unknown or duplicate person_id")
            report_data = report.model_dump()
            # Position comes from detected face geometry. Free-form VLM clothing/pose guesses
            # are not trustworthy enough to select a person for the downstream image edit.
            report_data["person_locator"] = authoritative_locators[report.person_id]
            result[report.person_id] = {**report_data, "model_used": response.model or resolved_alias}
        missing = expected - result.keys()
        if missing:
            raise ValueError(f"VLM batch review omitted person IDs: {sorted(missing)}")
        return result

    @staticmethod
    def make_context_sheet(original, swapped, max_height=720):
        """Build one labelled original/swap context image within DMX's three-image limit."""

    @staticmethod
    def build_whole_image_prompt(global_instructions, people):
        """Build one operation-first prompt using concise visible textual locators."""
        assignments = []
        for person in people:
            requests = []
            # The itemized issue list from /refinement/review is the ground truth: each entry
            # already carries a precise region + description + suggestion (see vlm_contracts.py's
            # specificity requirements). review_prompt is only a one-sentence compression of it and
            # routinely drops issues when a person has more than one -- passing issues through in
            # full is what actually gets every diagnosed defect in front of the edit model, not a
            # generic paraphrase of the first or most severe one.
            issues = list(getattr(person, "issues", None) or [])
            if issues:
                issue_lines = []
                for index, issue in enumerate(issues, start=1):
                    region = getattr(issue, "region", "").strip()
                    description = getattr(issue, "description", "").strip()
                    suggestion = getattr(issue, "suggestion", "").strip()
                    category = getattr(issue, "category", "").strip()
                    line = f"{index}. [{category}] {region}：{description}"
                    if suggestion:
                        line += f"（修复建议：{suggestion}）"
                    issue_lines.append(line)
                requests.append(
                    "AI诊断出的具体瑕疵清单（须逐项修复，不得只挑其中一条；优先于下面的笼统总结）：\n      "
                    + "\n      ".join(issue_lines)
                )
            if person.review_prompt.strip():
                requests.append(f"AI诊断总体建议：{person.review_prompt.strip()}")
            if person.user_instructions.strip() and person.user_instructions.strip() != person.review_prompt.strip():
                requests.append(f"人工修复要求：{person.user_instructions.strip()}")
            if not requests:
                requests.append("自动修复此人的可见换脸瑕疵，提高皮肤、五官和面部边缘的自然度")
            assignments.append(
                "\n".join(
                    [
                        f"PERSON #{person.person_id}",
                        *(
                            [f"  - 人物文字定位：{person.person_locator.strip()}"]
                            if person.person_locator.strip()
                            else [f"  - 可见位置线索：从左到右第 {person.face_index} 张脸"]
                        ),
                        *[f"  - {request}" for request in requests],
                    ]
                )
            )

        sections = [
            "【任务】",
            "仅编辑输入的这张无标记完整图片，一次完成下列所有人物的换脸瑕疵修复。只输出一张编辑后的完整照片。",
            "",
            "【人物映射与编辑操作】",
            *assignments,
        ]
        if global_instructions.strip():
            sections.extend(["", "【全局补充要求】", global_instructions.strip()])
        sections.extend(
            [
                "",
                "【执行规则】",
                "1. 同时完成全部 PERSON 的请求；PERSON 编号与文字定位一一对应，禁止混淆人物。",
                "2. 以完整画面协调性为目标修复请求中指出的瑕疵；允许自然重绘目标人物的脸部、头发、耳朵、颈部以及与场景融合有关的邻近区域。",
                "3. 保持画面中人物现有可见特征、表情、视线、头部姿态和整体角色连续性；本任务不执行重新换脸。",
                "",
                "【整体稳定要求】",
                "除明确修复所需的整体协调调整外，保持输入图的主体、人物数量、构图、镜头视角、身体姿势、服装、饰品、手部、道具、背景、文字、Logo 和水印稳定。不要输出框、标签、标注、局部裁剪、拼图、前后对比或说明文字。",
                "",
                "【输出】",
                "仅返回编辑后的完整图片，不附加任何框、标签、文字或其他图像。",
            ]
        )
        return "\n".join(sections)

    @staticmethod
    def normalize_to_canvas(candidate, target_shape):
        """Center-crop a provider canvas to the source aspect ratio, then restore exact source dimensions."""
        target_height, target_width = target_shape[:2]
        source_height, source_width = candidate.shape[:2]
        target_ratio = target_width / target_height
        source_ratio = source_width / source_height
        if source_ratio > target_ratio:
            crop_width = max(1, round(source_height * target_ratio))
            left = max(0, (source_width - crop_width) // 2)
            cropped = candidate[:, left : left + crop_width]
            operation = "center-crop-width"
        elif source_ratio < target_ratio:
            crop_height = max(1, round(source_width / target_ratio))
            top = max(0, (source_height - crop_height) // 2)
            cropped = candidate[top : top + crop_height, :]
            operation = "center-crop-height"
        else:
            cropped = candidate
            operation = "resize-only"
        normalized = cv2.resize(cropped, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)
        return normalized, {
            "provider_output_size": [source_width, source_height],
            "target_canvas_size": [target_width, target_height],
            "canvas_normalization": operation,
        }

    def _generation_parameters(self, model_alias, image_shape):
        """Return provider-native high-resolution output controls for the resolved alias."""
        resolved_alias = model_alias or self.gateway.default_image_alias
        config = self.gateway.image_edits.get(resolved_alias)
        if config is None:
            raise ValueError(f"Unknown image-edit model alias: {resolved_alias}")
        provider = config.provider
        policy = config.generation
        if provider == "dmx-gemini":
            height, width = image_shape[:2]
            ratio = width / height
            aspect_ratios = {
                "1:1": 1.0,
                "2:3": 2 / 3,
                "3:2": 3 / 2,
                "3:4": 3 / 4,
                "4:3": 4 / 3,
                "4:5": 4 / 5,
                "5:4": 5 / 4,
                "9:16": 9 / 16,
                "16:9": 16 / 9,
                "21:9": 21 / 9,
            }
            aspect_ratio = min(aspect_ratios, key=lambda name: abs(aspect_ratios[name] - ratio))
            return {"aspect_ratio": aspect_ratio, "image_size": policy["image_size"]}, None

        height, width = image_shape[:2]
        max_side = int(policy["max_side"])
        min_side = int(policy["min_side"])
        grid = int(policy["grid"])
        scale_limits = [max_side / max(height, width), 1.0]
        if policy.get("max_pixels"):
            scale_limits.append((int(policy["max_pixels"]) / (height * width)) ** 0.5)
        scale = min(scale_limits)
        # Floor to the configured provider grid so rounding cannot cross a
        # declared long-side or total-pixel ceiling.
        output_width = max(min_side, int(width * scale) // grid * grid)
        output_height = max(min_side, int(height * scale) // grid * grid)
        delimiter = str(policy["delimiter"])
        return {"size": f"{output_width}{delimiter}{output_height}"}, [output_width, output_height]

    async def refine_whole_image(self, swapped, people, global_instructions, model_alias=None):
        """Generate one complete edited photograph from exactly one complete image input."""
        parameters, requested_output_size = self._generation_parameters(model_alias, swapped.shape)
        prompt = self.build_whole_image_prompt(global_instructions, people)
        resolved_alias = model_alias or self.gateway.default_image_alias
        target = self.encode_image(swapped, lossless=True)
        inputs = [target]
        provider_result = await self.gateway.edit_image(inputs, prompt, alias=model_alias, parameters=parameters)
        candidate = self.decode_image(provider_result.image_bytes)
        candidate, canvas_audit = self.normalize_to_canvas(candidate, swapped.shape)
        audit = {
            "model": provider_result.model or resolved_alias,
            "provider": provider_result.provider,
            "usage": provider_result.usage or {},
            "request_id": provider_result.request_id,
            "generation_mode": "whole-image-single-call",
            "requested_output_size": requested_output_size,
            "people_count": len(people),
            "input_image_count": 1,
            **canvas_audit,
        }
        return {"candidate": candidate, "audit": audit, "prompt": prompt}

    async def review_whole_candidate(
        self,
        baseline,
        candidate,
        people,
        global_instructions="",
        original_target=None,
        alias=None,
    ):
        """Review the complete generated canvas once and return advisory evidence for the user."""
        resolved_alias = alias or self.gateway.vision_alias("refinement")
        requests = []
        for person in people:
            issue_text = "; ".join(
                f"{issue.region}: {issue.description}; {issue.suggestion}" for issue in (person.issues or [])
            )
            instructions = person.user_instructions or person.review_prompt or issue_text or "修复可见换脸瑕疵"
            requests.append(
                f"PERSON #{person.person_id}; visible locator: {person.person_locator or f'从左到右第 {person.face_index} 张脸'}; "
                f"requested correction: {instructions}; diagnosed details: {issue_text or 'none supplied'}"
            )
        request_text = "\n".join(requests)
        image_explanation = (
            "Image 1 is the complete pre-swap target, Image 2 is the complete face-swapped baseline before generative "
            "refinement, and Image 3 is the complete generated candidate. Compare all three. Identity replacement between "
            "Image 1 and Image 2 is requested; any additional pose, gaze, anatomy, body, clothing, text, background or color "
            "change in Image 3 must be treated skeptically."
            if original_target is not None
            else "Image 1 is the complete image before generative refinement. Image 2 is the complete generated candidate."
        )
        content = [
            {
                "type": "text",
                "text": (
                    f"{image_explanation} "
                    "Review the full photographs directly; do not assume any local mask or pixel preservation. Assess whether "
                    "each requested person's visible defects improved, whether the whole photograph is natural and coherent, "
                    "and whether generation introduced material unrequested changes or a new severe defect. The review is "
                    "advisory evidence for a human, not an automatic acceptance gate. Return ONLY JSON matching this shape: "
                    "overall_score, coherence_score, naturalness_score (0-100); people (one object per requested PERSON with "
                    "person_id, status resolved|improved|unchanged|worsened|unverifiable, evidence); "
                    "unrequested_changes_detected; new_high_severity_issue; recommendation accept|review|regenerate; summary; "
                    "issues; strengths. Use a skeptical photographic-quality bar and write summary, evidence, issues and "
                    "strengths in Chinese. The issues array must contain only unresolved or newly introduced visible defects; "
                    "put successful observations only in strengths. Do not claim that resolution, text, identity, or geometry "
                    "was preserved unless the compared complete images visibly support that claim. An accept recommendation is "
                    "valid only when every person is resolved or improved, no issue or unrequested change is visible, and all "
                    f"three scores are at least 92. Requested edits:\n{request_text}\nGlobal instructions: "
                    f"{global_instructions or 'none supplied'}"
                ),
            },
        ]
        if original_target is not None:
            content.append({"type": "image_url", "image_url": {"url": self.encode_image(original_target)}})
        content.extend(
            [
                {"type": "image_url", "image_url": {"url": self.encode_image(baseline)}},
                {"type": "image_url", "image_url": {"url": self.encode_image(candidate)}},
            ]
        )
        response, review = await self.gateway.structured_vision_completion(
            messages=[{"role": "user", "content": content}],
            contract=WholeImageQualityReview,
            alias=resolved_alias,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        expected = {person.person_id for person in people}
        actual = {item.person_id for item in review.people}
        if actual != expected:
            raise ValueError(
                f"Whole-image review person coverage mismatch: expected {sorted(expected)}, received {sorted(actual)}"
            )
        return {**review.model_dump(), "model_used": response.model or resolved_alias}
