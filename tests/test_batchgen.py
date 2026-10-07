"""Offline tests (python -m unittest): the HTTP call is faked, so no key or credit is used."""
import base64
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import urllib.error
import zlib
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import batchgen  # noqa: E402


def png(width, height):
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + b"IHDR" + header + struct.pack(">I", zlib.crc32(b"IHDR" + header))
    return b"\x89PNG\r\n\x1a\n" + ihdr


def jpeg(width, height):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHH", 11, 8, height, width) + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def api_returning(*images):
    payload = {"data": [{"b64_json": base64.b64encode(image).decode()} for image in images]}
    return FakeResponse(json.dumps(payload).encode())


def http_error(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://x/v1/images/generations", code, "err", headers, io.BytesIO(b'{"error":"x"}'))


CONFIG = {
    "model": "gpt-image-2",
    "size": "1024x1024",
    "style": "Flat vector icon.",
    "avoid": "text",
    "images": {"cup": "a coffee cup", "poster": {"prompt": "a 16:9 poster of a lighthouse", "size": "3840x2160", "count": 2}},
}


class PlanTests(unittest.TestCase):
    def test_tiers_and_costs_follow_the_longest_edge(self):
        cup, poster = batchgen.plan(CONFIG)
        self.assertEqual((cup.planned_tier, cup.planned_cost), ("1K", 0.015))
        self.assertEqual((poster.planned_tier, poster.planned_cost), ("4K", 0.04))
        self.assertEqual(batchgen.tier(2048), "2K")
        self.assertEqual(batchgen.tier(2560), "4K")

    def test_prompt_gets_style_avoid_and_the_ratio_in_words(self):
        cup, poster = batchgen.plan(CONFIG)
        self.assertEqual(cup.prompt, "Flat vector icon.\n\na coffee cup\n\nAvoid: text\n\n1:1 composition.")
        self.assertNotIn("composition.", poster.prompt)  # the prompt already names 16:9
        self.assertEqual(batchgen.ratio_of("2048x878"), "21:9")

    def test_only_filters_and_bad_values_stop_early(self):
        self.assertEqual([job.name for job in batchgen.plan(CONFIG, {"cup"})], ["cup"])
        with self.assertRaises(SystemExit):
            batchgen.plan({"images": {"x": {"prompt": "y", "count": 8}}})
        with self.assertRaises(SystemExit):
            batchgen.plan({"images": {"x": {"prompt": "y", "size": "large"}}})

    def test_csv_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
            handle.write("name,prompt,size,quality,count\nhero,a hero banner,2048x1152,high,2\n")
        config = batchgen.load_config(handle.name)
        os.unlink(handle.name)
        (job,) = batchgen.plan(config)
        self.assertEqual((job.size, job.quality, job.count), ("2048x1152", "high", 2))


class ImageSizeTests(unittest.TestCase):
    def test_png_and_jpeg_headers(self):
        self.assertEqual(batchgen.image_size(png(3840, 2160)), (3840, 2160, "png"))
        self.assertEqual(batchgen.image_size(jpeg(1152, 2048)), (1152, 2048, "jpg"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.client = batchgen.Client("key", "https://example.test/v1", retries=2)

    @mock.patch("batchgen.time.sleep")
    def test_429_is_retried_then_files_and_cost_come_from_the_returned_pixels(self, sleep):
        job = batchgen.plan(CONFIG, {"poster"})[0]
        replies = [http_error(429, retry_after=3), api_returning(png(3840, 2160), png(1024, 1024))]
        with mock.patch("batchgen.urllib.request.urlopen", side_effect=replies) as urlopen:
            record = batchgen.run_job(self.client, job, self.out)
        sleep.assert_called_once_with(3.0)
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual((body["n"], body["size"], body["response_format"]), (2, "3840x2160", "b64_json"))
        self.assertEqual([f["tier"] for f in record["files"]], ["4K", "1K"])
        self.assertEqual(record["cost"], 0.035)
        self.assertTrue(os.path.exists(os.path.join(self.out, "poster_001.png")))
        self.assertTrue(batchgen.already_done(job, self.out))

    @mock.patch("batchgen.time.sleep")
    def test_a_400_is_not_retried_and_lands_in_the_record(self, sleep):
        job = batchgen.plan(CONFIG, {"cup"})[0]
        with mock.patch("batchgen.urllib.request.urlopen", side_effect=[http_error(400)]) as urlopen:
            record = batchgen.run_job(self.client, job, self.out)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("HTTP 400", record["error"])
        sleep.assert_not_called()

    def test_dry_run_and_budget_guard_send_nothing(self):
        path = os.path.join(self.out, "batch.json")
        with open(path, "w") as handle:
            json.dump(CONFIG, handle)
        with mock.patch("batchgen.urllib.request.urlopen") as urlopen, mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(batchgen.main([path, "--dry-run", "--output", self.out]), 0)
            with mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(batchgen.main([path, "--max-cost", "0.01", "--output", self.out]), 2)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
