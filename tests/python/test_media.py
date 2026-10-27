"""Private media snapshots and real HTTP range/authentication regressions."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from desktop_companion.access import AccessStore, COOKIE_NAME, RemoteSettings
from desktop_companion.companion import Bridge, CommandJournal, CompanionServer
from desktop_companion.media import MediaStore, byte_range
from test_companion import make_history


VIDEO = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 4
POSTER = b'\x89PNG\r\n\x1a\nprivate-poster-fixture'
CAPTIONS = b'WEBVTT\n\n00:00.000 --> 00:01.000\nA private pilot.\n'
IDENTIFIER = 'wifi-pilot-v2'


def publish_fixture(root):
    source = root / 'source'
    source.mkdir()
    for filename, data in [('pilot.mp4', VIDEO), ('poster.png', POSTER), ('narration.vtt', CAPTIONS)]:
        (source / filename).write_bytes(data)
    store = MediaStore(root / 'media')
    item = store.publish(source / 'pilot.mp4', IDENTIFIER, 'Wi-Fi pilot',
                         'A private review copy.', 'v2', 50,
                         source / 'poster.png', source / 'narration.vtt')
    return store, item, source


class MediaStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store, self.item, self.source = publish_fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_published_copy_is_exact_and_independent_of_source(self):
        self.assertEqual(self.item['bytes'], len(VIDEO))
        digest = hashlib.sha256(VIDEO).hexdigest()
        self.assertIn('?v=' + digest, self.item['video_url'])
        self.assertEqual((self.store.root / IDENTIFIER / (digest + '.mp4')).read_bytes(), VIDEO)
        (self.source / 'pilot.mp4').write_bytes(b'The source was replaced.')
        with self.store.open_asset(IDENTIFIER, 'video', digest) as (stream, asset, _):
            self.assertEqual(stream.read(), VIDEO)
            self.assertEqual(asset['bytes'], len(VIDEO))

    def test_catalog_exposes_only_metadata_and_protected_role_urls(self):
        serialized = json.dumps(self.store.catalog())
        for private in [str(self.root), str(self.source), 'mtime_ns', 'published_at', 'source_path']:
            self.assertNotIn(private, serialized)
        self.assertEqual(self.item['mime'], 'video/mp4')
        for role in ('video', 'poster', 'captions', 'download'):
            self.assertTrue(self.item[role + '_url'].startswith('/api/media/' + IDENTIFIER + '/' + role + '?v='))
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store.root / 'index.json').stat().st_mode & 0o777, 0o600)
        for path in (self.store.root / IDENTIFIER).iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_publish_rejects_invalid_ids_and_unsupported_sources(self):
        for identifier in ('../outside', '', 'Capitalized', 'x' * 81, 'path/slash'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.store.publish(self.source / 'pilot.mp4', identifier, 'Title')
        invalid = self.source / 'not-video.html'
        invalid.write_bytes(b'<html>private</html>')
        with self.assertRaises(ValueError):
            self.store.publish(invalid, 'another-pilot', 'Title')
        linked = self.source / 'linked.mp4'
        linked.symlink_to(self.source / 'pilot.mp4')
        with self.assertRaises(ValueError):
            self.store.publish(linked, 'another-pilot', 'Title')

    def test_publish_rejects_invalid_caption_format_without_replacing_live_record(self):
        caption = self.source / 'invalid.vtt'
        caption.write_text('1\n00:00:00,000 --> 00:00:01,000\nWrong format\n')
        before = (self.store.root / 'index.json').read_bytes()
        with self.assertRaises(ValueError):
            self.store.publish(self.source / 'pilot.mp4', IDENTIFIER, 'Replacement', captions=caption)
        self.assertEqual((self.store.root / 'index.json').read_bytes(), before)

    def test_failed_republication_with_same_video_keeps_existing_snapshot_valid(self):
        digest = hashlib.sha256(VIDEO).hexdigest()
        snapshot = self.store.root / IDENTIFIER / (digest + '.mp4')
        original_stat = snapshot.stat()
        original_index = (self.store.root / 'index.json').read_bytes()
        invalid_poster = self.source / 'not-an-image.html'
        invalid_poster.write_text('<html>not a poster</html>')
        with self.assertRaises(ValueError):
            self.store.publish(self.source / 'pilot.mp4', IDENTIFIER, 'Failed replacement', poster=invalid_poster)
        self.assertEqual((self.store.root / 'index.json').read_bytes(), original_index)
        self.assertEqual(snapshot.stat().st_ino, original_stat.st_ino)
        self.assertEqual(snapshot.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertEqual(self.store.catalog(), [self.item])
        with self.store.open_asset(IDENTIFIER, 'video', digest) as (stream, _, _):
            self.assertEqual(stream.read(), VIDEO)
        self.assertFalse(list((self.store.root / IDENTIFIER).glob('.publishing-*')))

    def test_successful_republication_reuses_verified_identical_snapshot(self):
        digest = hashlib.sha256(VIDEO).hexdigest()
        snapshot = self.store.root / IDENTIFIER / (digest + '.mp4')
        original_stat = snapshot.stat()
        updated = self.store.publish(self.source / 'pilot.mp4', IDENTIFIER, 'Updated title')
        self.assertEqual(updated['title'], 'Updated title')
        self.assertEqual(updated['video_url'], self.item['video_url'])
        self.assertEqual(snapshot.stat().st_ino, original_stat.st_ino)
        self.assertEqual(snapshot.stat().st_mtime_ns, original_stat.st_mtime_ns)
        with self.store.open_asset(IDENTIFIER, 'video', digest) as (stream, _, _):
            self.assertEqual(stream.read(), VIDEO)

    def test_republication_rejects_corrupt_existing_content_hash_snapshot(self):
        digest = hashlib.sha256(VIDEO).hexdigest()
        snapshot = self.store.root / IDENTIFIER / (digest + '.mp4')
        snapshot.write_bytes(b'x' * len(VIDEO))
        original_index = (self.store.root / 'index.json').read_bytes()
        with self.assertRaises(ValueError):
            self.store.publish(self.source / 'pilot.mp4', IDENTIFIER, 'Replacement')
        self.assertEqual((self.store.root / 'index.json').read_bytes(), original_index)
        self.assertEqual(snapshot.read_bytes(), b'x' * len(VIDEO))

    def test_corrupt_asset_shapes_are_omitted_from_catalog_and_cannot_be_opened(self):
        index = self.store.root / 'index.json'
        original = json.loads(index.read_text())
        for assets in (None, [], 'not-an-assets-object', {'video': None}, {'video': []}, {'video': 'bad'}):
            with self.subTest(assets=assets):
                record = dict(original['items'][IDENTIFIER], assets=assets)
                index.write_text(json.dumps({'schema': 1, 'items': {IDENTIFIER: record}}))
                self.assertEqual(self.store.catalog(), [])
                with self.assertRaises(FileNotFoundError):
                    with self.store.open_asset(IDENTIFIER, 'video'):
                        self.fail('Malformed asset metadata must not be served.')

    def test_republication_changes_version_and_old_url_fails_closed(self):
        old_digest = hashlib.sha256(VIDEO).hexdigest()
        (self.source / 'pilot.mp4').write_bytes(VIDEO + b'new cut')
        current = self.store.publish(self.source / 'pilot.mp4', IDENTIFIER, 'New cut')
        self.assertNotEqual(current['video_url'], self.item['video_url'])
        with self.assertRaises(FileNotFoundError):
            with self.store.open_asset(IDENTIFIER, 'video', old_digest):
                self.fail('An outdated URL must not silently return another cut.')

    def test_byte_range_rejects_malformed_or_unsatisfiable_values(self):
        for value in ('bytes=', 'bytes=-', 'bytes=-0', 'bytes=4-2', 'bytes=10-',
                      'bytes=0-1,3-4', 'items=0-1', 'bytes=one-two', 'bytes= 0-1',
                      'bytes=000000000000000000000-1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                byte_range(value, 10)
        with self.assertRaises(ValueError):
            byte_range('bytes=0-0', 0)
        self.assertEqual(byte_range('bytes=-50', 10), (0, 9))
        self.assertEqual(byte_range('bytes=7-99', 10), (7, 9))


class MediaGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store, self.item, self.source = publish_fixture(self.root)
        self.access = AccessStore(self.root / 'access.sqlite')
        self.settings = RemoteSettings(self.root / 'remote.json')
        self.settings.set_origin('https://phone.example')
        self.server = CompanionServer(
            ('127.0.0.1', 0), make_history(self.root),
            Bridge(self.root, enabled=False), CommandJournal(self.root / 'commands.sqlite'),
            access=self.access, remote=True, settings=self.settings, media=self.store)
        self.worker = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.worker.start()
        token, self.session = self.access.exchange(self.access.create_pairing()['code'], 'Test phone')
        self.cookie = COOKIE_NAME + '=' + token

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, path=None, method='GET', headers=None, authenticated=True):
        values = {'Host': 'phone.example', 'X-Forwarded-Proto': 'https',
                  'Origin': 'https://phone.example', 'Sec-Fetch-Site': 'same-origin'}
        if authenticated:
            values['Cookie'] = self.cookie
        values.update(headers or {})
        values = {key: value for key, value in values.items() if value is not None}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path or self.item['video_url'], headers=values)
            response = connection.getresponse()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
        finally:
            connection.close()

    def assert_private(self, headers):
        self.assertIn('no-store', headers['cache-control'])
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertEqual(headers['x-frame-options'], 'DENY')
        self.assertEqual(headers['referrer-policy'], 'no-referrer')
        self.assertIn('max-age=', headers['strict-transport-security'])
        self.assertNotIn('access-control-allow-origin', headers)

    def asset_path(self, role='video'):
        record = json.loads((self.store.root / 'index.json').read_text())['items'][IDENTIFIER]
        return self.store.root / IDENTIFIER / record['assets'][role]['name']

    def test_anonymous_manifest_and_assets_require_pairing(self):
        for path in ['/api/media', *[self.item[role + '_url'] for role in ('video', 'poster', 'captions', 'download')]]:
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    status, headers, body = self.request(path, method, authenticated=False)
                    self.assertEqual(status, 401)
                    self.assert_private(headers)
                    if method == 'HEAD':
                        self.assertEqual(body, b'')
                    self.assertNotIn(VIDEO, body)

    def test_authenticated_manifest_and_media_return_only_expected_data(self):
        status, headers, body = self.request('/api/media')
        self.assertEqual(status, 200)
        self.assert_private(headers)
        self.assertEqual(json.loads(body), {'items': [self.item]})
        self.assertNotIn(str(self.root).encode(), body)
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body, VIDEO)
        self.assertEqual(headers['content-length'], str(len(VIDEO)))
        self.assertEqual(headers['content-type'], 'video/mp4')
        self.assertEqual(headers['accept-ranges'], 'bytes')
        self.assertIn('private', headers['cache-control'])
        self.assert_private(headers)

    def test_range_requests_return_exact_intervals(self):
        for requested, first, last in [('bytes=0-1', 0, 1), ('bytes=21-', 21, len(VIDEO)-1),
                                       ('bytes=-19', len(VIDEO)-19, len(VIDEO)-1),
                                       ('bytes=17-41', 17, 41), ('bytes=0-99999', 0, len(VIDEO)-1),
                                       ('bytes=-99999', 0, len(VIDEO)-1)]:
            with self.subTest(requested=requested):
                status, headers, body = self.request(headers={'Range': requested})
                self.assertEqual(status, 206)
                self.assertEqual(body, VIDEO[first:last+1])
                self.assertEqual(headers['content-range'], f'bytes {first}-{last}/{len(VIDEO)}')
                self.assertEqual(headers['content-length'], str(last-first+1))
                self.assert_private(headers)

    def test_invalid_multipart_and_unsatisfiable_ranges_return_416_without_bytes(self):
        for requested in ('bytes=0-1,4-5', 'bytes=bad', 'items=0-1', 'bytes=-0',
                          'bytes=8-3', f'bytes={len(VIDEO)}-', 'bytes=-'):
            with self.subTest(requested=requested):
                status, headers, body = self.request(headers={'Range': requested})
                self.assertEqual(status, 416)
                self.assertEqual(headers['content-range'], f'bytes */{len(VIDEO)}')
                self.assertEqual(headers['content-length'], '0')
                self.assertEqual(body, b'')
                self.assert_private(headers)

    def test_head_has_full_length_no_body_and_ignores_range(self):
        status, headers, body = self.request(method='HEAD', headers={'Range': 'bytes=0-1'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['content-length'], str(len(VIDEO)))
        self.assertEqual(body, b'')
        self.assertNotIn('content-range', headers)
        self.assert_private(headers)

    def test_if_range_matches_etag_or_last_modified_and_mismatch_returns_full_copy(self):
        _, initial, _ = self.request(method='HEAD')
        for witness in (initial['etag'], initial['last-modified']):
            with self.subTest(witness=witness):
                status, headers, body = self.request(headers={'Range': 'bytes=0-1', 'If-Range': witness})
                self.assertEqual(status, 206)
                self.assertEqual(body, VIDEO[:2])
        for witness in ('"a-different-cut"', 'Wed, 21 Oct 2015 07:28:00 GMT', 'W/' + initial['etag']):
            with self.subTest(witness=witness):
                status, headers, body = self.request(headers={'Range': 'bytes=0-1', 'If-Range': witness})
                self.assertEqual(status, 200)
                self.assertEqual(body, VIDEO)
                self.assertNotIn('content-range', headers)

    def test_conditional_read_still_authenticates_and_returns_no_bytes(self):
        _, initial, _ = self.request(method='HEAD')
        for value in (initial['etag'], '*'):
            status, headers, body = self.request(headers={'If-None-Match': value})
            self.assertEqual(status, 304)
            self.assertEqual(body, b'')
            self.assertEqual(headers['etag'], initial['etag'])
            self.assert_private(headers)
        self.assertEqual(self.request(headers={'If-None-Match': initial['etag']}, authenticated=False)[0], 401)
        self.assertEqual(self.request(headers={'If-None-Match': '"other"'})[0], 200)

    def test_rejected_origins_and_revoked_sessions_never_return_media(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'},
                        {'X-Forwarded-Proto': 'http'}, {'Sec-Fetch-Site': 'cross-site'}):
            for method in ('GET', 'HEAD'):
                with self.subTest(headers=headers, method=method):
                    status, _, body = self.request(method=method, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertNotIn(VIDEO, body)
        # Native same-origin <video> requests can legitimately omit Origin.
        self.assertEqual(self.request(headers={'Origin': None})[0], 200)
        self.access.revoke(self.session['id'])
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(method='HEAD')[0], 401)
        self.assertEqual(self.request('/api/media')[0], 401)

    def test_path_traversal_arbitrary_files_and_malformed_roles_are_unavailable(self):
        for path in ('/api/media/../source/pilot.mp4', '/api/media/%2e%2e/video',
                     '/api/media/wifi-pilot-v2/%2e%2e', '/api/media/wifi-pilot-v2/video/extra',
                     '/api/media/wifi-pilot-v2/%76ideo', '/api/media/WIFI/video',
                     '/api/media/wifi-pilot-v2/index.json', '/api/media/index.json',
                     '/api/media/wifi-pilot-v2/video?v=bad',
                     self.item['video_url'] + '&v=duplicate'):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(VIDEO, body)
                self.assertNotIn(str(self.root).encode(), body)

    def test_missing_snapshot_is_unavailable(self):
        self.asset_path().unlink()
        self.assertEqual(self.request()[0], 404)

    def test_corrupt_asset_shapes_fail_closed_through_http(self):
        index = self.store.root / 'index.json'
        original = json.loads(index.read_text())
        for assets in (None, [], 'not-an-assets-object', {'video': None}, {'video': []}):
            with self.subTest(assets=assets):
                record = dict(original['items'][IDENTIFIER], assets=assets)
                index.write_text(json.dumps({'schema': 1, 'items': {IDENTIFIER: record}}))
                status, _, body = self.request('/api/media')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {'items': []})
                status, _, body = self.request()
                self.assertEqual(status, 404)
                self.assertNotIn(VIDEO, body)

    def test_symlinked_asset_cannot_serve_other_files(self):
        target = self.asset_path()
        target.unlink()
        target.symlink_to(self.source / 'pilot.mp4')
        status, _, body = self.request()
        self.assertEqual(status, 404)
        self.assertNotIn(VIDEO, body)

    def test_symlinked_item_directory_is_unavailable(self):
        directory = self.store.root / IDENTIFIER
        relocated = self.store.root / 'relocated'
        directory.rename(relocated)
        directory.symlink_to(relocated, target_is_directory=True)
        self.assertEqual(self.request()[0], 404)

    def test_symlinked_index_and_media_root_fail_closed(self):
        index = self.store.root / 'index.json'
        renamed = self.store.root / 'old-index.json'
        index.rename(renamed)
        index.symlink_to(renamed)
        self.assertEqual(self.request()[0], 404)
        index.unlink()
        renamed.rename(index)
        relocated = self.root / 'relocated-media'
        self.store.root.rename(relocated)
        self.store.root.symlink_to(relocated, target_is_directory=True)
        self.assertEqual(self.request()[0], 404)

    def test_modified_snapshot_cannot_be_served_under_original_version(self):
        target = self.asset_path()
        original = target.stat()
        target.write_bytes(b'x' * len(VIDEO))
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
        self.assertEqual(self.request()[0], 404)
        target.write_bytes(VIDEO + b'extra')
        self.assertEqual(self.request()[0], 404)

    def test_download_poster_and_captions_use_same_authenticated_exact_snapshot(self):
        for role, expected, mime in [('download', VIDEO, 'video/mp4'), ('poster', POSTER, 'image/png'),
                                      ('captions', CAPTIONS, 'text/vtt; charset=utf-8')]:
            with self.subTest(role=role):
                status, headers, body = self.request(self.item[role + '_url'])
                self.assertEqual(status, 200)
                self.assertEqual(body, expected)
                self.assertEqual(headers['content-type'], mime)
                self.assert_private(headers)
                if role == 'download':
                    self.assertEqual(headers['content-disposition'], f'attachment; filename="{IDENTIFIER}.mp4"')
                else:
                    self.assertNotIn('content-disposition', headers)


if __name__ == '__main__':
    unittest.main()
