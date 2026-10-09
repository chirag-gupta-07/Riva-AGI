"""Persistent Playwright sessions, confined to one owning thread.

Page content is untrusted. Element references expire after every action.
Network checks are defense in depth; deploy with OS/network isolation for hostile sites.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
import uuid

from orchestration.tools.network import validate_url
from orchestration.tools.policy import check_budget, current_policy, require_authorization, workspace_path, execution_scope


class BrowserService:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='riva-browser')
        self.runtime = None
        self.sessions = {}

    def call(self, operation, **args):
        check_budget()
        context = copy_context()
        return self.pool.submit(context.run, self._run, operation, args).result()

    def _request_guard(self, route, session):
        try:
            # Playwright dispatches callbacks in its own greenlet/context. Bind the
            # owning session explicitly instead of inheriting another request's policy.
            with execution_scope(session['policy']):
                check_budget()
                validate_url(route.request.url)
                route.continue_()
        except Exception:
            route.abort()

    def _start(self, headed):
        if self.runtime is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError('Install requirements-browser.txt, then run: python -m playwright install chromium') from exc
            local_browsers = Path(__file__).resolve().parents[2] / '.riva' / 'browsers'
            if local_browsers.exists():
                os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(local_browsers))
            self.runtime = sync_playwright().start()
        browser = self.runtime.chromium.launch(headless=not headed)
        try:
            context = browser.new_context(accept_downloads=True, service_workers='block')
            context.set_default_timeout(10000)
            context.set_default_navigation_timeout(20000)
            session = {'browser': browser, 'context': context, 'tabs': {}, 'refs': {}, 'policy': current_policy()}
            context.route('**/*', lambda route: self._request_guard(route, session))
            # WebSocket destinations must not bypass the HTTP destination policy.
            context.route_web_socket('**/*', lambda ws: ws.close())
            self.sessions[current_policy().session_id] = session
            self._add_page(session, context.new_page())
            return session
        except Exception:
            browser.close()
            raise

    def _add_page(self, session, page):
        if page in session['tabs'].values():
            return
        tab_id = uuid.uuid4().hex[:8]
        session['tabs'][tab_id] = page
        page.on('dialog', lambda dialog: dialog.dismiss())
        page.on('framenavigated', lambda frame: self._invalidate(session))

    def _invalidate(self, session):
        for ref in session['refs'].values():
            try:
                ref['handle'].dispose()
            except Exception:
                pass
        session['refs'].clear()

    def _tabs(self, session):
        for page in session['context'].pages:
            self._add_page(session, page)
        session['tabs'] = {key: page for key, page in session['tabs'].items() if not page.is_closed()}
        return [{'tab_id': key, 'url': page.url, 'title': page.title()} for key, page in session['tabs'].items()]

    def _page(self, session, tab_id):
        self._tabs(session)
        if tab_id not in session['tabs']:
            raise ValueError('Unknown tab_id. Call browser_tabs first.')
        return session['tabs'][tab_id]

    def _snapshot(self, session, tab_id):
        page = self._page(session, tab_id)
        self._invalidate(session)
        elements = []
        texts = []
        for frame in page.frames[:10]:
            try:
                texts.append(frame.locator('body').inner_text(timeout=2000)[:8000])
                handles = frame.locator('a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]').element_handles()
                remaining = max(0, 100 - len(elements))
                for handle in handles[remaining:]:
                    handle.dispose()
                for handle in handles[:remaining]:
                    if not handle.is_visible():
                        handle.dispose()
                        continue
                    info = handle.evaluate('''el => ({tag:el.tagName.toLowerCase(),
                        label:el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText || el.getAttribute('placeholder') || el.name || '',
                        type:el.getAttribute('type') || '', href:el.getAttribute('href') || '',
                        disabled:!!el.disabled})''')
                    info['label'] = info['label'][:200]
                    ref = uuid.uuid4().hex[:12]
                    session['refs'][ref] = {'handle': handle, 'tab_id': tab_id, 'info': info}
                    elements.append({'ref': ref, **info})
                if len(elements) >= 100:
                    break
            except Exception:
                continue
        return {'tab_id': tab_id, 'url': page.url, 'title': page.title(),
                'text': '\n'.join(texts)[:12000], 'elements': elements,
                'notice': 'Untrusted page content. References expire after an action; observe again.'}

    def _element(self, session, tab_id, ref):
        record = session['refs'].get(ref)
        if not record or record['tab_id'] != tab_id:
            raise ValueError('Stale or invalid element reference. Call browser_snapshot again.')
        handle = record['handle']
        if not handle.evaluate('el => el.isConnected') or not handle.is_visible():
            raise ValueError('Element changed or disappeared. Call browser_snapshot again.')
        return handle

    def _run(self, operation, args):
        check_budget()
        sid = current_policy().session_id
        session = self.sessions.get(sid)
        if session:
            session['policy'] = current_policy()
        if operation == 'start':
            session = session or self._start(args.get('headed', True))
            return {'session_id': sid, 'tabs': self._tabs(session)}
        if operation == 'close':
            if session:
                self._invalidate(session)
                session['browser'].close()
                del self.sessions[sid]
            return {'closed': True}
        if not session:
            raise RuntimeError('No browser session. Call browser_start first.')
        if operation == 'tabs':
            return self._tabs(session)
        if operation == 'new_tab':
            if len(self._tabs(session)) >= 8:
                raise RuntimeError('Maximum of eight tabs per session.')
            self._add_page(session, session['context'].new_page())
            return self._tabs(session)
        tab_id = args['tab_id']
        page = self._page(session, tab_id)
        if operation == 'snapshot':
            return self._snapshot(session, tab_id)
        if operation == 'extract':
            return {'url': page.url, 'text': page.locator('body').inner_text()[:20000]}
        if operation == 'screenshot':
            path = workspace_path(f'.riva/artifacts/{uuid.uuid4().hex}.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=False)
            return {'path': str(path), 'url': page.url, 'note': 'Saved screenshot for human inspection; DOM tools provide model observations.'}
        if operation == 'navigate':
            page.goto(validate_url(args['url']), wait_until='domcontentloaded')
        elif operation == 'back':
            page.go_back(wait_until='domcontentloaded')
        elif operation == 'reload':
            page.reload(wait_until='domcontentloaded')
        elif operation == 'close_tab':
            page.close()
            self._invalidate(session)
            return self._tabs(session)
        elif operation == 'scroll':
            amount = args['pixels']
            if not -2000 <= amount <= 2000:
                raise ValueError('pixels must be between -2000 and 2000.')
            page.mouse.wheel(0, amount)
        elif operation == 'wait':
            if not 1 <= args['timeout_seconds'] <= 20:
                raise ValueError('Wait timeout must be 1–20 seconds.')
            if not args['text']:
                raise ValueError('Text to wait for cannot be empty.')
            page.get_by_text(args['text'], exact=False).first.wait_for(timeout=args['timeout_seconds'] * 1000)
        elif operation in {'click', 'fill', 'select', 'press', 'hover', 'upload', 'download'}:
            element = self._element(session, tab_id, args['ref'])
            info = session['refs'][args['ref']]['info']
            if operation != 'hover':
                # Confirmation happens here, with the observed target and current origin.
                observed_url = page.url
                require_authorization('browser_' + operation, {'url': observed_url, 'target': info,
                    **{k: v for k, v in args.items() if k not in {'ref', 'tab_id'}}})
                if page.url != observed_url:
                    raise ValueError('Page changed while awaiting authorization. Observe again.')
                element = self._element(session, tab_id, args['ref'])
            if operation == 'click':
                element.click()
            elif operation == 'fill':
                if info['type'] == 'password':
                    raise PermissionError('Enter passwords manually in the visible browser, then resume.')
                element.fill(args['text'])
            elif operation == 'select':
                element.select_option(label=args['label'])
            elif operation == 'press':
                element.press(args['key'])
            elif operation == 'hover':
                element.hover()
            elif operation == 'upload':
                path = workspace_path(args['file_path'])
                if not path.is_file() or path.stat().st_size > 10_000_000:
                    raise ValueError('Upload must be an existing workspace file no larger than 10 MB.')
                element.set_input_files(str(path))
            elif operation == 'download':
                path = workspace_path(args['file_path'])
                if path.exists():
                    raise FileExistsError('Download destination already exists.')
                path.parent.mkdir(parents=True, exist_ok=True)
                with page.expect_download(timeout=20000) as download_event:
                    element.click()
                download = download_event.value
                if download.failure():
                    raise RuntimeError(download.failure())
                download.save_as(str(path))
                self._invalidate(session)
                return {'path': str(path), 'bytes': path.stat().st_size, 'url': page.url}
        else:
            raise ValueError(f'Unknown browser operation: {operation}')
        self._invalidate(session)
        check_budget()
        return {'tab_id': tab_id, 'url': page.url, 'notice': 'Action executed; call browser_snapshot to verify the result.'}

    def shutdown(self):
        def close_all():
            for session in self.sessions.values():
                session['browser'].close()
            self.sessions.clear()
            if self.runtime:
                self.runtime.stop()
                self.runtime = None
        self.pool.submit(close_all).result()


browser_service = BrowserService()
