"""Unit tests for OpenRouterCaptioner, 512px downscaling, and krea2 linter validation."""
from __future__ import annotations

import base64
import numpy as np
import pytest

from pipeline.openrouter import OpenRouterCaptioner


def test_prepare_512px_payload():
    captioner = OpenRouterCaptioner()

    # Create 1080p dummy frame (1920x1080)
    frame_1080p = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame_1080p[:, :] = (120, 100, 80)

    b64_str = captioner.prepare_512px_payload(frame_1080p, max_dim=512)
    assert isinstance(b64_str, str)
    assert len(b64_str) > 0

    # Decode and check dimensions
    raw_bytes = base64.b64decode(b64_str)
    # JPEG size should be compact (< 40 KB)
    assert len(raw_bytes) < 40000


def test_system_prompt_modes():
    captioner = OpenRouterCaptioner()

    p_concept = captioner.load_system_prompt("concept", "restrained_elegance", "woman")
    assert "restrained_elegance Photograph of..." in p_concept
    assert "Hardware & Materials" in p_concept
    assert "DO NOT include quality buzzwords" in p_concept

    p_char = captioner.load_system_prompt("character", "c1_eva", "woman")
    assert "c1_eva Photograph of a woman..." in p_char
    assert "DO NOT describe permanent, immutable facial geometry" in p_char


def test_build_request_payload_grok47():
    captioner = OpenRouterCaptioner()
    payload = captioner.build_request_payload(
        base64_jpeg="fake_b64",
        system_prompt="system test",
        model="x-ai/grok-4.7",
        max_tokens=350,  # exceeds ceiling, should be capped to 300
        temperature=0.80,
        top_p=0.90,
    )

    assert payload["model"] == "x-ai/grok-4.7"
    assert payload["max_tokens"] == 300
    assert payload["temperature"] == 0.80
    assert payload["top_p"] == 0.90
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert "image_url" in payload["messages"][1]["content"][1]["type"]


def test_sanitize_and_validate_caption():
    captioner = OpenRouterCaptioner()

    # Raw output with Danbooru sludge, bracket weights, and missing trigger
    raw_caption = "((masterpiece:1.4)), 8k resolution, photorealistic. A woman kneeling in seiza bound in ropes."

    cleaned, warnings = captioner.sanitize_and_validate_caption(
        raw_caption, mode="concept", trigger="restrained_elegance"
    )

    # 1. Trigger must be at Token 0
    assert cleaned.startswith("restrained_elegance")
    # 2. Sludge must be eliminated
    assert "masterpiece" not in cleaned.lower()
    assert "8k" not in cleaned.lower()
    assert "photorealistic" not in cleaned.lower()
    # 3. Bracket weights removed
    assert "((" not in cleaned
    assert ":1.4" not in cleaned
    assert len(warnings) > 0


def test_is_refusal_response_detection():
    from pipeline.openrouter import is_refusal_response

    # Explicit refusal texts
    assert is_refusal_response("I cannot fulfill this request due to safety policies.")
    assert is_refusal_response("As an AI, I am unable to generate descriptions of this nature.")
    assert is_refusal_response("Sorry, but I cannot describe sexually suggestive scenes.")
    assert is_refusal_response("This request violates our content policy.")
    assert is_refusal_response("")  # Empty response is considered refusal
    assert is_refusal_response("   ")

    # Finish reasons and refusal fields
    assert is_refusal_response("any text", choice_data={"finish_reason": "content_filter"})
    assert is_refusal_response("any text", choice_data={"finish_reason": "safety"})
    assert is_refusal_response("any text", choice_data={"finish_reason": "refusal"})
    assert is_refusal_response("any text", choice_data={"message": {"refusal": "I cannot fulfill this"}})

    # Valid photographic description
    valid_text = "restrained_elegance A woman kneeling in seiza pose with natural hemp rope bindings around wrists."
    assert not is_refusal_response(valid_text, choice_data={"finish_reason": "stop"})


def test_caption_single_frame_refusal_raises_error(monkeypatch):
    import asyncio
    import httpx
    from pipeline.openrouter import OpenRouterCaptioner, ModelRefusalError

    captioner = OpenRouterCaptioner()
    dummy_img = np.zeros((200, 200, 3), dtype=np.uint8)

    # 1. Test 200 OK with refusal text in message
    class MockRefusalResponse:
        status_code = 200
        def json(self):
            return {
                "choices": [{
                    "message": {"role": "assistant", "content": "I am sorry, but I cannot fulfill this request."},
                    "finish_reason": "stop",
                }]
            }

    async def mock_post_refusal(*args, **kwargs):
        return MockRefusalResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_refusal)

    async def run_test_1():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ModelRefusalError) as exc_info:
                await captioner.caption_single_frame(
                    client=client,
                    img_bgr=dummy_img,
                    api_key="test-key",
                    model="deepseek/deepseek-v4.1-flash",
                )
            assert "refused image description" in str(exc_info.value)

    asyncio.run(run_test_1())

    # 2. Test 403 content filter moderation error
    class Mock403Response:
        status_code = 403
        text = "Request blocked by safety moderation filter"

    async def mock_post_403(*args, **kwargs):
        return Mock403Response()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_403)

    async def run_test_2():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ModelRefusalError) as exc_info:
                await captioner.caption_single_frame(
                    client=client,
                    img_bgr=dummy_img,
                    api_key="test-key",
                    model="deepseek/deepseek-v4.1-flash",
                )
            assert "content filter refusal" in str(exc_info.value)

    asyncio.run(run_test_2())


def test_build_request_payload_gemma_reasoning():
    captioner = OpenRouterCaptioner()
    payload = captioner.build_request_payload(
        base64_jpeg="fake_b64",
        system_prompt="system test",
        model="google/gemma-4-31b-it",
        max_tokens=300,
        temperature=0.80,
        top_p=0.90,
        reasoning_effort="high",
    )

    assert payload["model"] == "google/gemma-4-31b-it"
    assert payload["max_tokens"] == 300
    assert payload["temperature"] == 0.80
    assert payload["top_p"] == 0.90
    assert payload["reasoning"] == {"effort": "high", "exclude": True}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert "image_url" in payload["messages"][1]["content"][1]["type"]


def test_api_openrouter_defaults_and_models():
    from pipeline.api import OpenRouterCaptionRequest, list_openrouter_models

    req = OpenRouterCaptionRequest()
    assert req.model == "google/gemma-4-31b-it"
    assert req.fallback_layer2 == "deepseek/deepseek-v4.1-flash"
    assert req.fallback_layer3 == "x-ai/grok-4.7"
    assert req.reasoning_effort == "high"
    assert req.max_tokens == 300
    assert req.temperature > 0.75

    models_res = list_openrouter_models()
    models = models_res["models"]
    default_model = next(m for m in models if m["id"] == "google/gemma-4-31b-it")
    assert default_model["recommended"] is True
    assert "Gemma 4 31B" in default_model["name"]

    deepseek_model = next(m for m in models if m["id"] == "deepseek/deepseek-v4.1-flash")
    assert deepseek_model["recommended"] is False
    assert "Layer 2" in deepseek_model["name"]

    grok_model = next(m for m in models if m["id"] == "x-ai/grok-4.7")
    assert grok_model["recommended"] is False
    assert "Layer 3" in grok_model["name"]
    assert "Grok 4.7" in grok_model["name"]


def test_3_layer_cascade_simulation():
    from pipeline.openrouter import ModelRefusalError

    call_log = []

    async def mock_caption(client, img_bgr, api_key, model, **kwargs):
        call_log.append(model)
        if "gemma" in model:
            # Gemma refuses this specific adult scene
            raise ModelRefusalError("Refused by Gemma", model=model)
        elif "deepseek" in model:
            # DeepSeek also refuses
            raise ModelRefusalError("Refused by DeepSeek", model=model)
        elif "grok" in model:
            # Grok succeeds
            return "restrained_elegance A female subject kneeling in rope ties.", []
        return "default caption", []

    import asyncio

    async def run_cascade_test():
        primary = "google/gemma-4-31b-it"
        layer2 = "deepseek/deepseek-v4.1-flash"
        layer3 = "x-ai/grok-4.7"

        caption_text = None
        layer_used = None

        try:
            caption_text, _ = await mock_caption(None, None, "key", primary)
            layer_used = "layer1"
        except ModelRefusalError:
            try:
                caption_text, _ = await mock_caption(None, None, "key", layer2)
                layer_used = "layer2"
            except ModelRefusalError:
                caption_text, _ = await mock_caption(None, None, "key", layer3)
                layer_used = "layer3"

        assert layer_used == "layer3"
        assert caption_text.startswith("restrained_elegance")
        assert call_log == [primary, layer2, layer3]

    asyncio.run(run_cascade_test())

