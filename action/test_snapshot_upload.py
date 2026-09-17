"""snapshot_upload.py mod en falsk OIDC-udsteder og broker (http.server i en tråd).  python3 -m unittest -v"""
import base64
import contextlib
import gzip
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import snapshot_upload as su

HEAD = 'b' * 40


class Fake(BaseHTTPRequestHandler):
    calls = []
    reply_url = None
    status = 201
    oidc_body = None
    broker_body = None

    def log_message(self, *args):
        pass

    def do_GET(self):  # OIDC-udsteder
        Fake.calls.append(('GET', self.path, dict(self.headers), None))
        body = json.dumps(Fake.oidc_body if Fake.oidc_body is not None else {'value': 'eyJhbGciOiJSUzI1NiJ9.eyJhIjoxfQ.sig'}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # broker
        raw = self.rfile.read(int(self.headers['Content-Length']))
        Fake.calls.append(('POST', self.path, dict(self.headers), json.loads(raw)))
        url = Fake.reply_url or f'http://127.0.0.1:{self.server.server_port}/r/' + 'A' * 22
        reply = Fake.broker_body if Fake.broker_body is not None else {'ok': True, 'data': {'id': 'A' * 22, 'url': url, 'expires_at': 'x'}}
        body = json.dumps(reply).encode()
        self.send_response(Fake.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


class SnapshotUpload(unittest.TestCase):
    def setUp(self):
        Fake.calls, Fake.reply_url, Fake.status, Fake.oidc_body, Fake.broker_body = [], None, 201, None, None
        self.server = HTTPServer(('127.0.0.1', 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.snapshot = self.dir / 'snapshot.json'
        self.snapshot.write_text(json.dumps({
            'schema': 1, 'repository': 'projekt', 'head_sha': HEAD, 'verdict': 'request_changes',
            'summary': {'tilfoejet': ['Login']}, 'files': [{'path': 'a.py', 'layer': 'Implementation', 'patch': '+x', 'added': 1, 'removed': 0}],
            'findings': [{'path': 'a.py', 'line': 1, 'severity': 'alvorlig', 'category': 'Sikkerhed', 'title': 'T', 'body': 'B',
                          'agent_prompt': 'intern', 'suggestion': None, 'confidence': 90}],
            'excluded': [['package-lock.json', 'filtreret']], 'incremental': None, 'base_sha': 'c' * 40,
            'checks': [{'name': 'CHANGELOG opdateret', 'status': 'fail', 'explanation': 'Mangler.', 'mode': 'error', 'ekstra': 1},
                       {'name': 'Ukendt', 'status': 'maybe'}],
            'review': {'mode': 'incremental', 'cost_usd': 1.5, 'duration_s': 60}}))
        self.output = self.dir / 'output'
        self.output.write_text('')

    def env(self, **extra):
        values = {'MANILENS_URL': self.base, 'GITHUB_OUTPUT': str(self.output),
                  'ACTIONS_ID_TOKEN_REQUEST_URL': self.base + '/token?x=1', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'req-token'}
        values.update(extra)
        return patch.dict(os.environ, values, clear=False)

    def run_main(self, head=HEAD, pr='12'):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = su.main(['--snapshot', str(self.snapshot), '--head', head, '--pr', pr])
        return code, out.getvalue()

    def test_uploads_gzip_base64_with_oidc_and_writes_only_the_url(self):
        with self.env():
            code, out = self.run_main()

        self.assertEqual(code, 0, out)
        oidc = Fake.calls[0]
        self.assertEqual(oidc[0], 'GET')
        self.assertIn('audience=' + self.base.replace(':', '%3A').replace('/', '%2F'), oidc[1])
        self.assertEqual(oidc[2]['Authorization'], 'bearer req-token')
        post = Fake.calls[1]
        self.assertEqual((post[0], post[1]), ('POST', '/api/snapshot'))
        self.assertTrue(post[2]['Authorization'].startswith('Bearer eyJ'))
        body = post[3]
        self.assertEqual((body['pr'], body['head_sha'], body['encoding']), (12, HEAD, 'gzip+base64'))
        sent = json.loads(gzip.decompress(base64.b64decode(body['data'])))
        self.assertEqual(sent['verdict'], 'request_changes')
        self.assertIsInstance(sent['summary'], str)
        self.assertIn('Login', sent['summary'])
        self.assertEqual(sent['findings'][0]['agent_prompt'], 'intern', 'fix-prompten vises på oversigten')
        self.assertNotIn('suggestion', sent['findings'][0], 'kun felter brokeren gemmer')
        self.assertEqual(sent['base_sha'], 'c' * 40)
        self.assertEqual(sent['checks'], [{'name': 'CHANGELOG opdateret', 'status': 'fail', 'explanation': 'Mangler.', 'mode': 'error'}],
                         'ukendte felter og ugyldig status sendes ikke')
        self.assertEqual(sent['excluded'], [['package-lock.json', 'filtreret']])
        self.assertEqual(sent['review'], {'mode': 'incremental', 'cost_usd': 1.5, 'duration_s': 60})
        self.assertEqual(sent['scanner_counts'], [])
        self.assertEqual(self.output.read_text(), 'snapshot_url=' + self.base + '/r/' + 'A' * 22 + '\n')
        self.assertNotIn('eyJ', out, 'OIDC-tokenet skrives aldrig')

    def test_scanner_counts_file_is_sent_when_valid(self):
        counts = self.dir / 'tools.md.counts.json'
        counts.write_text(json.dumps([{'tool': 'ruff', 'rule': 'S608', 'severity': 'error', 'count': 3}, {'tool': 'x'}, 'skrald']))
        with self.env(), contextlib.redirect_stdout(io.StringIO()):
            su.main(['--snapshot', str(self.snapshot), '--head', HEAD, '--pr', '12', '--scanner-counts', str(counts)])
        sent = json.loads(gzip.decompress(base64.b64decode(Fake.calls[1][3]['data'])))
        self.assertEqual(sent['scanner_counts'], [{'tool': 'ruff', 'rule': 'S608', 'severity': 'error', 'count': 3}])

    def test_fields_are_cut_to_broker_limits_so_one_long_check_never_costs_the_whole_overview(self):
        long_finding = {'title': 't' * 600, 'agent_prompt': 'a' * 25_000, 'line': 3}
        self.assertEqual(su.payload({'head_sha': HEAD, 'findings': [long_finding]})['findings'], [{'title': 't' * 500, 'agent_prompt': 'a' * 20_000, 'line': 3}])
        data = su.payload({'head_sha': HEAD, 'checks': [{'name': 'n' * 300, 'status': 'fail', 'explanation': 'e' * 3000, 'mode': 'kritisk'}],
                           'excluded': [['p' * 600, 'r' * 300]]},
                          [{'tool': 't' * 80, 'rule': 'r' * 200, 'severity': 's' * 40, 'count': True},
                           {'tool': 'ruff', 'rule': 'S1', 'severity': 'error', 'count': -1},
                           {'tool': 't' * 80, 'rule': 'r' * 200, 'severity': 's' * 40, 'count': 2}])
        self.assertEqual(data['checks'], [{'name': 'n' * 200, 'status': 'fail', 'explanation': 'e' * 2000}], 'ukendt mode udelades')
        self.assertEqual(data['excluded'], [['p' * 512, 'r' * 200]])
        self.assertEqual(data['scanner_counts'], [{'tool': 't' * 64, 'rule': 'r' * 128, 'severity': 's' * 32, 'count': 2}])

    def test_limits_are_bytes_like_the_broker_so_danish_text_is_never_rejected(self):
        # Brokeren tæller UTF-8-bytes (strlen); 500 × "æ" er 1000 bytes. Et tegn må ikke klippes i to.
        data = su.payload({'head_sha': HEAD, 'findings': [{'title': 'æ' * 600, 'body': 'ok'}],
                           'checks': [{'name': 'n', 'status': 'pass', 'explanation': 'ø' * 2000}], 'excluded': [['å' * 600, 'r']]})
        title = data['findings'][0]['title']
        self.assertEqual((len(title.encode()), title), (500, 'æ' * 250))
        self.assertEqual(len(data['checks'][0]['explanation'].encode()), 2000)
        self.assertEqual(len(data['excluded'][0][0].encode()), 512)
        self.assertEqual(su.clip('aæ' * 3, 4), 'aæa', 'et tegn på to bytes klippes aldrig i to')

    def test_lists_and_numbers_are_kept_inside_broker_limits(self):
        file = {'path': 'x.php', 'layer': 'Kode', 'patch': 'p', 'added': 1, 'removed': 0}
        data = su.payload({'head_sha': HEAD, 'files': [file] * 400, 'findings': [{'title': 't', 'line': 3, 'confidence': 90}] * 250,
                           'checks': [{'name': 'n', 'status': 'pass'}] * 60, 'excluded': [['p', 'r']] * 1500})
        self.assertEqual([len(data[k]) for k in ('files', 'findings', 'checks', 'excluded')], [300, 200, 50, 1000])
        odd = su.payload({'head_sha': HEAD, 'findings': [{'title': 't', 'line': -3, 'confidence': 150}, {'title': 'u', 'line': True, 'confidence': 80}]})
        self.assertEqual(odd['findings'], [{'title': 't'}, {'title': 'u', 'confidence': 80}], 'ugyldigt linjenummer/sikkerhed udelades')

    def test_files_the_broker_cannot_store_are_listed_as_excluded_instead(self):
        good = {'path': 'x.php', 'layer': 'Kode', 'patch': 'p', 'added': 1, 'removed': 0}
        data = su.payload({'head_sha': HEAD, 'files': [good, {**good, 'path': 'y.bin', 'patch': 'p' * 1_000_001}, {**good, 'path': 'z' * 600}]})
        self.assertEqual(data['files'], [good])
        self.assertEqual(data['excluded'], [['y.bin', 'for stor til oversigten'], ['z' * 512, 'for stor til oversigten']])

    def test_review_usage_outside_the_broker_schema_is_dropped(self):
        for review in ({'mode': 'bogus'}, {'mode': 'full', 'cost_usd': -1}, {'mode': 'full', 'duration_s': 1.5}, 'full'):
            self.assertIsNone(su.payload({'head_sha': HEAD, 'review': review})['review'], review)
        self.assertEqual(su.payload({'head_sha': HEAD, 'review': {'mode': 'full', 'cost_usd': 2, 'duration_s': None}})['review'],
                         {'mode': 'full', 'cost_usd': 2, 'duration_s': None})

    def test_missing_or_broken_scanner_counts_file_does_not_stop_the_upload(self):
        with self.env(), contextlib.redirect_stdout(io.StringIO()):
            code = su.main(['--snapshot', str(self.snapshot), '--head', HEAD, '--pr', '12', '--scanner-counts', str(self.dir / 'mangler.json')])
        self.assertEqual((code, len(Fake.calls)), (0, 2))

    def test_missing_oidc_environment_warns_and_exits_zero(self):
        with self.env(ACTIONS_ID_TOKEN_REQUEST_URL='', ACTIONS_ID_TOKEN_REQUEST_TOKEN=''):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn('::warning::', out)
        self.assertEqual(Fake.calls, [])
        self.assertEqual(self.output.read_text(), '')

    def test_head_mismatch_makes_no_call(self):
        with self.env():
            code, out = self.run_main(head='c' * 40)
        self.assertEqual(code, 0)
        self.assertIn('::warning::', out)
        self.assertEqual(Fake.calls, [])

    def test_url_outside_manilens_url_is_rejected(self):
        Fake.reply_url = 'https://evil.example/r/' + 'A' * 22
        with self.env():
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn('::warning::', out)
        self.assertEqual(self.output.read_text(), '')

    def test_broker_error_warns_and_exits_zero(self):
        Fake.status = 403
        with self.env():
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn('::warning::', out)
        self.assertEqual(self.output.read_text(), '')

    def test_non_object_json_from_issuer_or_broker_warns_and_exits_zero(self):
        for field, value in (('oidc_body', [1, 2]), ('broker_body', ['x']), ('broker_body', {'data': 'tekst'})):
            Fake.oidc_body, Fake.broker_body = None, None
            setattr(Fake, field, value)
            with self.subTest(field=field, value=value), self.env():
                code, out = self.run_main()
                self.assertEqual(code, 0)
                self.assertIn('::warning::', out)

    def test_too_large_snapshot_is_not_sent(self):
        data = json.loads(self.snapshot.read_text())
        # Tre filer under brokerens 1 MB pr. patch, men tilsammen over grænsen for hele kroppen.
        data['files'] = [{**data['files'][0], 'path': f'f{i}.bin', 'patch': base64.b64encode(os.urandom(700_000)).decode()} for i in range(3)]
        self.snapshot.write_text(json.dumps(data))
        with self.env():
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn('for stor', out)
        self.assertEqual([c for c in Fake.calls if c[0] == 'POST'], [])


if __name__ == '__main__':
    unittest.main()
