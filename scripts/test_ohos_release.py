#!/usr/bin/env python3
"""Release metadata, module content and SDK signature verification regressions."""

import importlib.util
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load('verify_release', ROOT / 'scripts/verify-ohos-release.py')


class ReleaseMetadataTest(unittest.TestCase):
    def test_release_metadata_fails_closed(self):
        valid = dict(debug=False, buildMode='release', versionCode=67)
        VERIFY._check_release_semantics(valid, 'entry.hap')
        for field, value in (('debug', True), ('debug', None), ('buildMode', 'debug'),
                             ('buildMode', None), ('versionCode', 0)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY._check_release_semantics({**valid, field: value}, 'entry.hap')


class ModuleContentTest(unittest.TestCase):
    """A HAP that ships the Web-only wakelock asset fails the AppGallery scan."""

    def build_module(self, directory, members):
        path = Path(directory) / 'entry-publish.hap'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return path

    def test_rejects_bundled_wakelock_web_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            hap = self.build_module(directory, {
                'module.json': '{}',
                VERIFY.WAKELOCK_WEB_ASSET: '(function () { return {}; })();',
            })
            with self.assertRaisesRegex(VERIFY.VerificationError, r'entry-publish\.hap.*no_sleep\.js'):
                VERIFY._check_module_content(hap, 'entry-publish.hap')

    def test_accepts_module_with_legitimate_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            hap = self.build_module(directory, {
                'module.json': '{}',
                'resources/rawfile/flutter_assets/AssetManifest.json': '{}',
                'resources/rawfile/flutter_assets/NOTICE': 'third party notices',
                'resources/rawfile/flutter_assets/fonts/MaterialIcons-Regular.otf': 'font bytes',
                'resources/rawfile/flutter_assets/assets/icon.png': 'png bytes',
                'resources/rawfile/flutter_assets/packages/wakelock_plus/LICENSE': 'license text',
            })
            VERIFY._check_module_content(hap, 'entry-publish.hap')


@unittest.skipUnless(all(os.environ.get(key) for key in (
    'OHOS_RELEASE_TEST_APP', 'OHOS_RELEASE_TEST_PROFILE', 'OHOS_RELEASE_TEST_SIGN_TOOL',
)), 'requires a real signed APP, release profile and SDK signing tool')
class RealReleaseSignatureTest(unittest.TestCase):
    def setUp(self):
        self.app = Path(os.environ['OHOS_RELEASE_TEST_APP']).resolve()
        self.profile = Path(os.environ['OHOS_RELEASE_TEST_PROFILE']).resolve()
        self.jar = Path(os.environ['OHOS_RELEASE_TEST_SIGN_TOOL']).resolve()
        self.java = VERIFY._resolve_java(os.environ.get('OHOS_RELEASE_TEST_JAVA'))
        self.directory = tempfile.TemporaryDirectory(prefix='rustdesk-release-test-')
        self.addCleanup(self.directory.cleanup)

    def test_accepts_real_outer_signature_and_native_payload(self):
        report = VERIFY.verify_release(self.app, self.profile, self.java, self.jar)
        self.assertTrue(any('App Pack signature OK' in line for line in report))
        self.assertTrue(any('native arm64-v8a payload verified' in line for line in report))

    def test_rejects_unsigned_container_named_signed(self):
        target = Path(self.directory.name) / 'unsigned-signed.app'
        with zipfile.ZipFile(self.app) as source, zipfile.ZipFile(target, 'w') as destination:
            for info in source.infolist():
                destination.writestr(info, source.read(info))
        with self.assertRaisesRegex(VERIFY.VerificationError, 'verify-app rejected'):
            VERIFY.verify_release(target, self.profile, self.java, self.jar)

    def test_rejects_tampering_with_signed_content(self):
        target = Path(self.directory.name) / 'tampered-signed.app'
        shutil.copyfile(self.app, target)
        with zipfile.ZipFile(target) as archive:
            info = archive.getinfo('pack.info')
        with target.open('r+b') as stream:
            stream.seek(info.header_offset + 26)
            name_length, extra_length = struct.unpack('<HH', stream.read(4))
            stream.seek(info.header_offset + 30 + name_length + extra_length)
            offset = stream.tell()
            original = stream.read(1)
            self.assertEqual(len(original), 1)
            stream.seek(offset)
            stream.write(bytes([original[0] ^ 1]))
        with self.assertRaisesRegex(VERIFY.VerificationError, 'verify-app rejected'):
            VERIFY.verify_release(target, self.profile, self.java, self.jar)


if __name__ == '__main__':
    unittest.main()
