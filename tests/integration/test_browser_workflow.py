"""Real Chromium tests using only a loopback HTTP fixture, no API keys."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import uuid
import pytest
from orchestration.tools import tool_registry
from orchestration.tools.policy import execution_scope
from orchestration.tools.browser_service import browser_service

HTML = b'''<!doctype html><html><body>
<h1>RIVA browser test</h1>
<label for="name">Your name</label><input id="name">
<button onclick="document.getElementById('result').textContent='Submitted: '+document.getElementById('name').value">Submit</button>
<p id="result"></p><a href="/report" download="report.txt">Download report</a>
<label for="upload">Upload file</label><input id="upload" type="file">
<a href="/" target="_blank">New window</a>
<button onclick="setTimeout(()=>document.getElementById('result').textContent='Delayed ready',100)">Delayed</button>
</body></html>'''


@pytest.fixture
def browser_fixture(isolated_execution):
    pytest.importorskip('playwright.sync_api', reason='Install requirements-browser.txt to run Chromium tests')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if self.path == '/report':
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Disposition', 'attachment; filename="report.txt"')
                self.end_headers()
                self.wfile.write(b'RIVA verified report')
            else:
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(HTML)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with execution_scope(session_id=uuid.uuid4().hex, local_hosts=frozenset({'127.0.0.1'}),
                             authorize=lambda name, args: True):
            started = tool_registry.execute('browser_start', headed=False)
            tab = started['tabs'][0]['tab_id']
            try:
                tool_registry.execute('browser_navigate', tab_id=tab, url=f'http://127.0.0.1:{server.server_port}/')
                yield tab
            finally:
                tool_registry.execute('browser_close')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def reference(tab, label):
    snapshot = tool_registry.execute('browser_snapshot', tab_id=tab)
    return next(element['ref'] for element in snapshot['elements'] if element['label'] == label)


def test_form_verification_download_and_upload(browser_fixture, tmp_path):
    tab = browser_fixture
    original_ref = reference(tab, 'Your name')
    tool_registry.execute('browser_fill', tab_id=tab, ref=original_ref, text='RIVA')
    stale = tool_registry.execute_result('browser_fill', tab_id=tab, ref=original_ref, text='wrong')
    assert not stale.success and 'Stale' in stale.error
    tool_registry.execute('browser_click', tab_id=tab, ref=reference(tab, 'Submit'))
    tool_registry.execute('browser_wait_for', tab_id=tab, text='Submitted: RIVA')
    assert 'Submitted: RIVA' in tool_registry.execute('browser_extract', tab_id=tab)['text']
    path = str(tmp_path / 'report.txt')
    result = tool_registry.execute('browser_download', tab_id=tab, ref=reference(tab, 'Download report'), file_path=path)
    assert result['bytes'] > 0
    assert (tmp_path / 'report.txt').read_text() == 'RIVA verified report'
    tool_registry.execute('browser_upload', tab_id=tab, ref=reference(tab, 'Upload file'), file_path=path)
    image = tool_registry.execute('browser_screenshot', tab_id=tab)
    from pathlib import Path
    assert Path(image['path']).read_bytes().startswith(b'\x89PNG')


def test_denied_action_and_session_isolation(browser_fixture):
    tab = browser_fixture
    ref = reference(tab, 'Submit')
    with execution_scope(authorize=None):
        result = tool_registry.execute_result('browser_click', tab_id=tab, ref=ref)
        assert not result.success and 'Authorization' in result.error
    assert 'Submitted:' not in tool_registry.execute('browser_extract', tab_id=tab)['text']
    with execution_scope(session_id='different-session'):
        result = tool_registry.execute_result('browser_snapshot', tab_id=tab)
        assert not result.success and 'No browser session' in result.error


def test_dynamic_page_and_tabs(browser_fixture):
    tab = browser_fixture
    tool_registry.execute('browser_click', tab_id=tab, ref=reference(tab, 'Delayed'))
    tool_registry.execute('browser_wait_for', tab_id=tab, text='Delayed ready')
    assert 'Delayed ready' in tool_registry.execute('browser_extract', tab_id=tab)['text']
    tabs = tool_registry.execute('browser_new_tab')
    assert len(tabs) == 2
    other = next(t['tab_id'] for t in tabs if t['tab_id'] != tab)
    assert len(tool_registry.execute('browser_close_tab', tab_id=other)) == 1
