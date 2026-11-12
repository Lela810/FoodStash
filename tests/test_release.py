import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts.check_release import ROOT, check_release
from scripts import check_image_absent


class ReleaseTests(unittest.TestCase):
    def test_current_metadata_and_valid_tag(self):
        version, errors = check_release()
        self.assertEqual([], errors)
        self.assertEqual([], check_release(tag=f"v{version}")[1])

    def test_invalid_and_mismatched_tags(self):
        version, _ = check_release()
        for tag in [f"v{version}.1", "v01.1.0", f"v{version}-rc.1", version, "main", f"v{version}\nbad"]:
            with self.subTest(tag=tag):
                self.assertTrue(check_release(tag=tag)[1])

    def test_each_metadata_surface_is_enforced(self):
        version, _ = check_release()
        different = version + ".1"
        for filename in ["VERSION", "README.md", "compose.yaml", "CHANGELOG.md"]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                for source in ["VERSION", "README.md", "compose.yaml", "CHANGELOG.md"]:
                    text = (ROOT / source).read_text(encoding="utf-8")
                    (root / source).write_text(text.replace(version, different) if source == filename else text,
                                               encoding="utf-8")
                self.assertTrue(check_release(root)[1])

    def test_workflow_build_is_tag_only(self):
        checks = (ROOT / ".github" / "workflows" / "checks.yml").read_text()
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        self.assertNotIn("build-push-action", checks)
        self.assertNotIn("docker build", checks)
        self.assertIn('tags: ["v*.*.*"]', release)
        self.assertNotIn("workflow_dispatch", release)
        self.assertNotIn("pull_request:", release)
        self.assertIn("needs: validate", release)
        self.assertIn('check_release.py --tag "$RELEASE_TAG"', release)
        self.assertLess(release.index("check_image_absent.py"), release.index("build-push-action"))


class ImageGateTests(unittest.TestCase):
    @patch.dict("os.environ", {"IMAGE": "lela810/foodstash", "RELEASE_VERSION": "0.1.0",
                              "GITHUB_ACTOR": "test", "GHCR_TOKEN": "test-only"})
    @patch("scripts.check_image_absent.urlopen")
    def test_registry_gate(self, mocked):
        token = b'{"token":"test-token"}'
        mocked.side_effect = [io.BytesIO(token), HTTPError("url", 404, "Missing", {}, None)]
        self.assertEqual(0, check_image_absent.main())
        mocked.side_effect = [io.BytesIO(token), io.BytesIO(b"")]
        self.assertEqual(1, check_image_absent.main())
        for failure in [HTTPError("url", 403, "Denied", {}, None), URLError("offline")]:
            mocked.side_effect = [io.BytesIO(token), failure]
            with self.assertRaises(type(failure)):
                check_image_absent.main()
