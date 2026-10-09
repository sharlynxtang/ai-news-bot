"""Functional checks for optional X and image-rich Feishu delivery."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src import config
from src.collectors.base import BaseCollector
from src.collectors.rss_collector import _entry_image_url
from src.collectors.x_collector import XCollector
from src.pusher.feishu import FeishuPusher, build_card
from src.pusher.images import FeishuImageUploader, _public_https_url
from src.summarizer import LLMSummarizer


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class MediaXTests(unittest.TestCase):
    def test_rss_image_is_carried_into_digest_and_card(self):
        image_url = _entry_image_url(
            {"media_content": [{"type": "image/jpeg", "url": "https://example.org/photo.jpg"}]},
            "https://example.org/feed",
        )
        item = BaseCollector.make_item(
            title="AI model launch", url="https://example.org/story", source="techcrunch",
            summary="A new AI model launched.", category="product",
            published_at="2026-10-09T02:00:00Z", image_url=image_url,
        )
        digest = LLMSummarizer(dry_run=True).summarize([item])
        self.assertIn("🖼 [原文配图](https://example.org/photo.jpg)", digest)
        plain_card = build_card(digest)
        self.assertIn("原文配图", str(plain_card))
        image_card = build_card(digest, image_keys={image_url: "img_key_example"})
        self.assertTrue(any(e.get("tag") == "img" and e.get("img_key") == "img_key_example"
                            for e in image_card["card"]["elements"]))

    def test_curated_x_post_is_selected_with_author_and_media(self):
        payload = {
            "includes": {
                "users": [{"id": "1", "username": "karpathy"}],
                "media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/photo.jpg"}],
            },
            "data": [
                {"id": "123", "author_id": "1", "created_at": "2026-10-09T02:00:00Z",
                 "text": "New AI model evaluation results", "attachments": {"media_keys": ["m1"]},
                 "public_metrics": {"like_count": 100, "retweet_count": 20}},
                {"id": "124", "author_id": "1", "created_at": "2026-10-09T02:00:00Z",
                 "text": "Dinner was nice", "public_metrics": {"like_count": 200}},
            ],
        }
        window = (datetime(2026, 10, 8, 2, 30, tzinfo=timezone.utc),
                  datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc))
        with patch.object(config, "X_ACCOUNTS", ["karpathy"]), \
             patch.object(config, "get_time_window", return_value=window), \
             patch.object(XCollector, "_request", return_value=_Response(payload)):
            items = XCollector(bearer_token="test-token").collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://x.com/karpathy/status/123")
        self.assertEqual(items[0]["image_url"], "https://pbs.twimg.com/photo.jpg")
        self.assertEqual(items[0]["category"], "x_post")

    def test_llm_selection_retains_curated_x_section(self):
        x_item = BaseCollector.make_item(
            title="@karpathy：AI model update", url="https://x.com/karpathy/status/123",
            source="x", summary="AI model update", category="x_post",
            published_at="2026-10-09T02:00:00Z",
        )
        summarizer = LLMSummarizer(dry_run=True)
        with patch.object(summarizer, "_call_final", return_value={"items": [], "focus_analysis": ""}):
            digest = summarizer._llm_pipeline([x_item])
        self.assertIn("━━ X 精选观点 ━━", digest)
        self.assertIn("https://x.com/karpathy/status/123", digest)

    def test_image_fetch_rejects_private_targets(self):
        self.assertFalse(_public_https_url("http://example.org/a.jpg"))
        self.assertFalse(_public_https_url("https://127.0.0.1/a.jpg"))
        self.assertFalse(_public_https_url("https://localhost/a.jpg"))

    def test_pusher_uploads_marked_image_before_sending(self):
        image_url = "https://example.org/photo.jpg"
        digest = f"日报\n\n━━ AI 产品与工具 ━━\n• 新闻 [来源](https://example.org/story)\n🖼 [原文配图]({image_url})\n"
        with patch.object(config, "FEISHU_APP_ID", "app-id"), \
             patch.object(config, "FEISHU_APP_SECRET", "app-secret"), \
             patch("src.pusher.feishu.FeishuImageUploader") as uploader, \
             patch.object(FeishuPusher, "_send", return_value=True) as send:
            uploader.return_value.upload_many.return_value = {image_url: "img-key"}
            self.assertTrue(FeishuPusher(webhook_url="https://open.feishu.cn/hook/test").push(digest))
            uploader.return_value.upload_many.assert_called_once_with([image_url])
            self.assertTrue(any(e.get("img_key") == "img-key" for e in send.call_args.args[0]["card"]["elements"]))

    def test_feishu_image_upload_uses_image_key_from_api(self):
        class ImageResponse:
            headers = {"Content-Type": "image/png", "Content-Length": "4"}
            is_redirect = False

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"\x89PNG"

            def close(self):
                pass

        class APIResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        class Session:
            def __init__(self):
                self.upload_files = None

            def get(self, *args, **kwargs):
                return ImageResponse()

            def post(self, url, **kwargs):
                if url.endswith("/tenant_access_token/internal"):
                    return APIResponse({"code": 0, "tenant_access_token": "tenant-token"})
                self.upload_files = kwargs["files"]
                self.asserted_auth = kwargs["headers"]["Authorization"]
                return APIResponse({"code": 0, "data": {"image_key": "img_uploaded"}})

        session = Session()
        with patch.object(config, "FEISHU_APP_ID", "app-id"), \
             patch.object(config, "FEISHU_APP_SECRET", "app-secret"), \
             patch("src.pusher.images._public_https_url", return_value=True):
            keys = FeishuImageUploader(session=session).upload_many(["https://example.org/photo.png"])
        self.assertEqual(keys, {"https://example.org/photo.png": "img_uploaded"})
        self.assertEqual(session.asserted_auth, "Bearer tenant-token")
        self.assertEqual(session.upload_files["image"][1], b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
