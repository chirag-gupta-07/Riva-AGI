"""Typed browser tools. Session identity comes from execution context, never the LLM."""
from orchestration.tools.registry import tool
from orchestration.tools.browser_service import browser_service


@tool(category='browser')
def browser_start(headed: bool = True) -> dict:
    """Start or reuse the current session's isolated Chromium browser. Return tab IDs."""
    return browser_service.call('start', headed=headed)


@tool(category='browser')
def browser_tabs() -> list:
    """List current session tabs, including new popups."""
    return browser_service.call('tabs')


@tool(category='browser')
def browser_new_tab() -> list:
    """Open a blank tab and return all tab IDs."""
    return browser_service.call('new_tab')


@tool(category='browser')
def browser_navigate(tab_id: str, url: str) -> dict:
    """Navigate a known tab to an absolute HTTP/HTTPS URL."""
    return browser_service.call('navigate', tab_id=tab_id, url=url)


@tool(category='browser')
def browser_snapshot(tab_id: str) -> dict:
    """Observe page text and visible interactive elements with temporary reference IDs."""
    return browser_service.call('snapshot', tab_id=tab_id)


@tool(category='browser')
def browser_click(tab_id: str, ref: str) -> dict:
    """Click an observed element after authorization. Observe again to verify."""
    return browser_service.call('click', tab_id=tab_id, ref=ref)


@tool(category='browser')
def browser_fill(tab_id: str, ref: str, text: str) -> dict:
    """Fill an observed non-password input with text after authorization."""
    return browser_service.call('fill', tab_id=tab_id, ref=ref, text=text)


@tool(category='browser')
def browser_select(tab_id: str, ref: str, label: str) -> dict:
    """Select an option by its visible label after authorization."""
    return browser_service.call('select', tab_id=tab_id, ref=ref, label=label)


@tool(category='browser')
def browser_press(tab_id: str, ref: str, key: str) -> dict:
    """Press a key in an observed element (for example Enter) after authorization."""
    return browser_service.call('press', tab_id=tab_id, ref=ref, key=key)


@tool(category='browser')
def browser_hover(tab_id: str, ref: str) -> dict:
    """Hover over an observed element to reveal a menu."""
    return browser_service.call('hover', tab_id=tab_id, ref=ref)


@tool(category='browser')
def browser_scroll(tab_id: str, pixels: int = 600) -> dict:
    """Scroll the tab vertically by -2000 to 2000 pixels."""
    return browser_service.call('scroll', tab_id=tab_id, pixels=pixels)


@tool(category='browser')
def browser_wait_for(tab_id: str, text: str, timeout_seconds: int = 10) -> dict:
    """Wait up to 20 seconds for visible text, such as a submission confirmation."""
    return browser_service.call('wait', tab_id=tab_id, text=text, timeout_seconds=timeout_seconds)


@tool(category='browser')
def browser_extract(tab_id: str) -> dict:
    """Extract up to 20000 characters of visible page text with its URL."""
    return browser_service.call('extract', tab_id=tab_id)


@tool(category='browser')
def browser_screenshot(tab_id: str) -> dict:
    """Save a screenshot in workspace .riva/artifacts for human inspection."""
    return browser_service.call('screenshot', tab_id=tab_id)


@tool(category='browser')
def browser_upload(tab_id: str, ref: str, file_path: str) -> dict:
    """Upload a workspace file through an observed file input after authorization."""
    return browser_service.call('upload', tab_id=tab_id, ref=ref, file_path=file_path)


@tool(category='browser')
def browser_download(tab_id: str, ref: str, file_path: str) -> dict:
    """Click a download link and save to a new workspace file after authorization."""
    return browser_service.call('download', tab_id=tab_id, ref=ref, file_path=file_path)


@tool(category='browser')
def browser_back(tab_id: str) -> dict:
    """Navigate back in a tab's history."""
    return browser_service.call('back', tab_id=tab_id)


@tool(category='browser')
def browser_reload(tab_id: str) -> dict:
    """Reload a tab."""
    return browser_service.call('reload', tab_id=tab_id)


@tool(category='browser')
def browser_close_tab(tab_id: str) -> list:
    """Close a tab and return remaining tabs."""
    return browser_service.call('close_tab', tab_id=tab_id)


@tool(category='browser')
def browser_close() -> dict:
    """Close this session's browser and discard its cookies and temporary state."""
    return browser_service.call('close')
