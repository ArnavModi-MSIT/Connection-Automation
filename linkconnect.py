"""Local, sequential LinkedIn invitation runner. English LinkedIn UI required."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
CONNECT = re.compile(r"^(?:Connect|Invite .+ to connect)$", re.I)
PENDING = re.compile(r"^(?:Pending(?:,.*)?|Invitation pending|Invitation sent(?: to .+)?)$", re.I)
MORE = re.compile(r"^(?:More|More actions)$", re.I)
RESUME_SKIP = {"sent", "pending", "connected", "sending", "review_required"}
LIMIT_TEXT = re.compile(
    r"weekly invitation limit|(?:reached|exceeded).{0,60}invitation limit|"
    r"too many invitations|out of invitations|can't send.{0,30}invitations|"
    r"unable to send.{0,30}invitation|invitation.{0,40}(?:not sent|failed)", re.I
)
SECURITY_TEXT = re.compile(
    r"let.s do a quick security check|verify your identity|security verification|"
    r"(?:account|access).{0,30}(?:restricted|suspended)|"
    r"unusual activity.{0,40}account", re.I
)


class StopRun(RuntimeError):
    """Stop the queue without retrying an uncertain operation."""


def canonical_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or not re.fullmatch(r"(?:linkedin\.com|(?:www|[a-z]{2})\.linkedin\.com)", host)
        or parts.username is not None
        or parts.password is not None
        or parts.port is not None
    ):
        raise ValueError("expected an HTTPS LinkedIn /in/ profile URL")
    match = re.fullmatch(r"/in/([^/]+)/?", parts.path)
    if not match or re.search(r"%(?![0-9a-fA-F]{2})", match[1]):
        raise ValueError("expected /in/profile-slug/ with no extra path")
    slug = unquote(match[1], errors="strict")
    if any(c.isspace() or ord(c) < 32 or c in "/\\?#" for c in slug):
        raise ValueError("invalid profile slug")
    return "https://www.linkedin.com/in/" + quote(slug, safe="-_.~") + "/"


def parse_links(text: str) -> list[str]:
    links = []
    seen = set()
    errors = []
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            url = canonical_url(line)
        except (ValueError, UnicodeError) as exc:
            errors.append(f"line {number}: {exc}")
            continue
        if url not in seen:
            links.append(url)
            seen.add(url)
    if errors:
        raise ValueError("Fix the link file before running:\n" + "\n".join(errors))
    if not links:
        raise ValueError("The link file contains no profiles.")
    return links


def load_links(path: Path) -> list[str]:
    return parse_links(path.read_text(encoding="utf-8-sig"))


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.latest: dict[str, dict] = {}
        if path.exists():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or not isinstance(record.get("status"), str):
                        raise ValueError("invalid record")
                    url = canonical_url(record["url"])
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    raise ValueError(f"Invalid journal at {path}, line {number}; review it before running.") from exc
                self.latest[url] = record

    def write(self, url: str, status: str, detail: str = "") -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "url": url,
            "status": status,
            "detail": detail,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.latest[url] = record


@contextmanager
def session_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise StopRun(f"Session already locked: {path}. Close the other runner first. See README for stale locks.") from exc
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def visible_one(locator, description: str):
    items = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
    if len(items) > 1:
        raise StopRun(f"Multiple visible {description}; stopping to avoid selecting the wrong person.")
    return items[0] if items else None


def notices(page) -> str:
    locator = page.locator("[role='alert'], [role='dialog'], .artdeco-toast-item")
    return "\n".join(locator.nth(i).inner_text() for i in range(locator.count()) if locator.nth(i).is_visible())


def guard(page, expected_url: str | None = None) -> None:
    parts = urlsplit(page.url)
    host = (parts.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise StopRun("The browser left LinkedIn; inspect the page manually.")
    if re.search(r"/(?:checkpoint|challenge|authwall|login|uas/login)(?:/|$)", parts.path):
        raise StopRun("Login or verification required. Use the login command and complete it manually.")
    if page.locator("input[type='password']:visible, iframe[src*='captcha']:visible").count():
        raise StopRun("Login or CAPTCHA is visible. Complete it manually using the login command.")
    text = notices(page)
    body_text = page.locator("body").inner_text()[:12000]
    if LIMIT_TEXT.search(text):
        raise StopRun("LinkedIn reported an invitation limit or sending error. Queue stopped.")
    if SECURITY_TEXT.search(text) or SECURITY_TEXT.search(body_text):
        raise StopRun("LinkedIn reported an account restriction or security check. Queue stopped.")
    if expected_url is not None:
        try:
            current = canonical_url(page.url)
        except ValueError as exc:
            raise StopRun("The requested profile did not load. Inspect the browser manually.") from exc
        if current != expected_url:
            raise StopRun("The profile URL changed. Update the supplied link after reviewing it manually.")


def profile_header(page):
    heading = visible_one(page.locator("main").get_by_role("heading", level=1), "profile headings")
    if heading is None:
        raise StopRun("No unique profile heading found. This layout is unsupported; no request attempted.")
    header = heading.locator(
        "xpath=ancestor::*[self::section or "
        "contains(concat(' ', normalize-space(@class), ' '), ' pv-top-card ')][1]"
    )
    if header.count() != 1:
        raise StopRun("Cannot isolate the profile action area. No request attempted.")
    return header


def pending_button(header):
    return visible_one(header.get_by_role("button", name=PENDING), "Pending buttons")


def connect_button(page, header):
    button = visible_one(header.get_by_role("button", name=CONNECT), "Connect buttons")
    if button is not None:
        return button
    more = visible_one(header.get_by_role("button", name=MORE), "More buttons")
    if more is None:
        return None
    more.click()
    menu_locator = page.locator("[role='menu']:visible, .artdeco-dropdown__content--is-open:visible")
    try:
        menu_locator.first.wait_for(state="visible", timeout=3000)
    except Exception as exc:
        raise StopRun("The More menu could not be identified. No request attempted.") from exc
    # Some menu containers have both selectors; CSS returns each element only once.
    menu = visible_one(menu_locator, "More menus")
    if menu is None:
        raise StopRun("The More menu is unavailable. No request attempted.")
    return visible_one(menu.get_by_role("button", name=CONNECT).or_(menu.get_by_role("menuitem", name=CONNECT)), "Connect menu actions")


def invitation_dialog(page):
    return visible_one(page.get_by_role("dialog"), "invitation dialogs")


def wait_for_invitation_ui(page, header, url: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        guard(page, url)
        if pending_button(header) is not None:
            return None
        dialog = invitation_dialog(page)
        if dialog is not None:
            return dialog
        page.wait_for_timeout(250)
    raise StopRun("Connect was clicked but its result is unclear. Review this profile manually before retrying.")


def send_from_dialog(page, dialog, note: str | None, url: str) -> None:
    guard(page, url)
    text = dialog.inner_text()
    if not re.search(r"\binvitation\b|\binvite\b|\bconnect\b", text, re.I):
        raise StopRun("The dialog could not be recognized as a connection invitation. No Send click attempted.")
    if dialog.locator("input[type='email']:visible").count() or re.search(r"(?:enter|provide).{0,40}email", text, re.I):
        raise StopRun("This profile requires an email address. No email will be guessed; queue stopped.")
    if note is not None:
        add_note = visible_one(dialog.get_by_role("button", name=re.compile(r"^Add a note$", re.I)), "Add a note buttons")
        if add_note is not None:
            add_note.click()
        textarea = dialog.locator("textarea:visible")
        try:
            textarea.first.wait_for(state="visible", timeout=3000)
        except Exception as exc:
            raise StopRun("The invitation note field is unavailable; request not submitted from the dialog.") from exc
        field = visible_one(textarea, "note fields")
        maximum = field.get_attribute("maxlength")
        if maximum and len(note) > int(maximum):
            raise StopRun(f"Note exceeds the current field limit of {maximum} characters.")
        field.fill(note)
        send_name = re.compile(r"^(?:Send|Send invitation|Send now)$", re.I)
    else:
        send_name = re.compile(r"^(?:Send without a note|Send|Send invitation|Send now)$", re.I)
    button = visible_one(dialog.get_by_role("button", name=send_name), "Send buttons")
    if button is None or not button.is_enabled():
        raise StopRun("No unambiguous, enabled Send button in the invitation dialog.")
    guard(page, url)
    button.click()


def process_profile(page, url: str, send: bool, note: str | None, before_click, timeout: float = 12) -> tuple[str, str]:
    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    if response is not None and response.status in {401, 403, 429, 999}:
        raise StopRun(f"LinkedIn returned HTTP {response.status}; queue stopped.")
    guard(page, url)
    page.locator("main").get_by_role("heading", level=1).first.wait_for(state="visible", timeout=10000)
    header = profile_header(page)
    if pending_button(header) is not None:
        return "pending", "An invitation is already pending."
    if visible_one(header.get_by_text("1st", exact=True), "connection badges") is not None:
        return "connected", "Already a first-degree connection."
    if invitation_dialog(page) is not None:
        raise StopRun("An unexpected dialog is open. Review it manually before continuing.")
    button = connect_button(page, header)
    if button is None:
        return "unavailable", "No Connect action in the profile header or its More menu."
    if not button.is_enabled():
        return "unavailable", "The Connect action is disabled."
    guard(page, url)
    if not send:
        return "would_send", "Connect is available; preview did not click Connect or Send."
    # This durable record precedes even Connect: some layouts may submit immediately.
    before_click()
    button.click()
    dialog = wait_for_invitation_ui(page, header, url, timeout)
    if dialog is not None:
        send_from_dialog(page, dialog, note, url)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        guard(page, url)
        if pending_button(header) is not None:
            detail = "Confirmed by Pending in the requested profile action area."
            if note is not None and dialog is None:
                detail += " LinkedIn submitted directly; the supplied note could not be added."
            return "sent", detail
        page.wait_for_timeout(250)
    raise StopRun("Send was attempted but Pending was not confirmed. Review manually; no automatic retry.")


def diagnostic(page, directory: Path) -> str:
    try:
        folder = directory / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".png")
        page.screenshot(path=str(path), full_page=False, timeout=5000)
        return str(path)
    except Exception:
        return "Screenshot unavailable."


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def delay_value(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite, nonnegative number")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Separate local session and journal directory.")
    result.add_argument("--browser", choices=["chromium", "chrome", "msedge"], default="chromium")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Open a browser for manual login; saves the local session.")
    validate = commands.add_parser("validate", help="Check and deduplicate links without opening a browser.")
    validate.add_argument("file", type=Path)
    run = commands.add_parser("run", help="Preview the queue, or send sequentially with --send.")
    run.add_argument("file", type=Path)
    run.add_argument("--send", action="store_true", help="Actually send invitations. Otherwise preview only.")
    run.add_argument("--limit", type=positive, help="Maximum profiles to visit this run; default: entire remaining queue.")
    run.add_argument("--delay", type=delay_value, default=5, help="Seconds between profiles, default 5; not a safe-rate guarantee.")
    run.add_argument("--note-file", type=Path, help="Optional UTF-8 invitation note, tool limit 200 characters.")
    return result


def run_queue(page, links: list[str], journal: Journal, args, note: str | None,
              on_event=None, stop_requested=None) -> int:
    def emit(event, **values):
        if on_event is not None:
            on_event({"event": event, **values})

    remaining = []
    for url in links:
        status = journal.latest.get(url, {}).get("status")
        if status in RESUME_SKIP:
            print(f"SKIP [{status}] {url}")
            emit("skipped", url=url, status=status, detail="Skipped from saved history.")
        else:
            remaining.append(url)
    if args.limit is not None:
        for url in remaining[args.limit:]:
            emit("skipped", url=url, status="not_selected", detail="Outside this run's profile limit.")
        remaining = remaining[:args.limit]
    mode = "SEND" if args.send else "PREVIEW"
    print(f"{mode}: {len(remaining)} profiles. Results: {journal.path}")
    emit("queue", total=len(remaining))
    for index, url in enumerate(remaining, 1):
        if stop_requested is not None and stop_requested():
            emit("stopped", detail="Stopped before the next profile.")
            return 130
        print(f"[{index}/{len(remaining)}] {url}", flush=True)
        emit("profile", url=url, current=index)

        def before_click():
            journal.write(url, "sending", "Attempt started; review manually if interrupted.")
            emit("attempt", url=url, status="sending", detail="Sending invitation.")

        try:
            status, detail = process_profile(
                page, url, args.send, note,
                before_click=before_click,
            )
        except Exception as exc:
            attempted = journal.latest.get(url, {}).get("status") == "sending"
            status = "review_required" if attempted else "halted"
            # Do not dump Playwright call logs or page content into the journal.
            detail = str(exc) if isinstance(exc, StopRun) else f"Browser operation failed ({type(exc).__name__})."
            screenshot = diagnostic(page, args.data_dir)
            journal.write(url, status, detail)
            emit("result", url=url, status=status, detail=detail)
            print(f"STOP [{status}]: {detail}\nDiagnostic: {screenshot}", flush=True)
            return 2
        journal.write(url, status, detail)
        emit("result", url=url, status=status, detail=detail)
        print(f"{status.upper()}: {detail}", flush=True)
        if index < len(remaining):
            if stop_requested is None:
                page.wait_for_timeout(args.delay * 1000)
            else:
                deadline = time.monotonic() + args.delay
                while time.monotonic() < deadline and not stop_requested():
                    page.wait_for_timeout(min(250, max(1, (deadline - time.monotonic()) * 1000)))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        links = load_links(args.file) if args.command in {"run", "validate"} else []
        if args.command == "validate":
            print(f"{len(links)} unique, valid LinkedIn profiles.")
            return 0
        note = None
        if args.command == "run" and args.note_file is not None:
            note = args.note_file.read_text(encoding="utf-8-sig").strip()
            if not note or len(note) > 200:
                raise ValueError("The note must contain 1-200 characters.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise StopRun("Install dependencies first: python -m pip install -r requirements.txt") from exc
        args.data_dir = args.data_dir.resolve()
        print("LinkedIn prohibits automated invitations; your account may be restricted.", flush=True)
        with session_lock(args.data_dir):
            journal = Journal(args.data_dir / "results.jsonl")
            with sync_playwright() as playwright:
                options = {"headless": False, "viewport": {"width": 1280, "height": 900}}
                if args.browser != "chromium":
                    options["channel"] = args.browser
                context = playwright.chromium.launch_persistent_context(
                    str(args.data_dir / "browser-profile"), **options
                )
                try:
                    context.set_default_timeout(10000)
                    page = context.pages[0] if context.pages else context.new_page()
                    if args.command == "login":
                        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
                        input("Log in manually in the browser, open your LinkedIn feed, then press Enter here: ")
                        guard(page)
                        if not urlsplit(page.url).path.startswith("/feed"):
                            raise StopRun("Open the signed-in LinkedIn feed before saving the session.")
                        print("Session saved locally. Keep this data directory private.")
                        return 0
                    return run_queue(page, links, journal, args, note)
                finally:
                    context.close()
    except KeyboardInterrupt:
        print("\nStopped. Any incomplete sending record is skipped on the next run; inspect it manually.")
        return 130
    except (OSError, ValueError, StopRun) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Browser startup/operation failed ({type(exc).__name__}). Check browser installation and session lock. See README.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
