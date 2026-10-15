"""Pull Amazon Photos into month roots that feed the trip-dump pipeline.

This lives beside cluster, curate, and organize because it supplies their source
photos. Listing is the default; --download fetches selected photos. Session
cookies must come from a manual browser login, never automated login.
"""

import argparse
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

API = "https://www.amazon.com/drive/v1"
STAMP = re.compile(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{22}(?=_)")


def month_windows(start, end):
    """Half-open monthly upload windows, clipped to the requested dates."""
    while start < end:
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(next_month, end)
        yield start, stop
        start = stop


def taken_date(info):
    raw = str(info.get("contentDate") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        match = STAMP.search(info.get("name") or "")
        if match:
            try:
                return date(*(int(x) for x in match.groups()[:3]))
            except ValueError:
                pass
    return None


def node_info(node):
    props = node.get("contentProperties") or {}
    return {
        "id": node.get("id"),
        "name": node.get("name") or "",
        "contentDate": props.get("contentDate") or "",
        "extension": (props.get("extension") or "jpg").lower().lstrip("."),
    }


def local_ids(base_dir):
    ids = set()
    if base_dir.exists():
        for path in base_dir.rglob("*"):
            if path.is_file():
                match = NODE_ID.match(path.name)
                if match:
                    ids.add(match.group())
    return ids


def destination(base_dir, info, taken, occupied):
    """Reserve a collision-free filename without splitting node IDs on underscores."""
    node_id = info["id"]
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", node_id)
    ext = re.sub(r"[^a-z0-9]", "", info["extension"]) or "jpg"
    stamp = taken.isoformat()
    base = f"{safe_id}_{stamp}"
    folder = base_dir / taken.strftime("%Y-%m")
    candidate = folder / f"{base}.{ext}"
    suffix = 2
    while candidate in occupied or candidate.exists():
        candidate = folder / f"{base}-{suffix}.{ext}"
        suffix += 1
    occupied.add(candidate)
    return candidate


def make_session(cookie_file):
    import requests

    raw = json.loads(Path(cookie_file).read_text())
    cookies = (
        raw if isinstance(raw, dict) else {row["name"]: row["value"] for row in raw}
    )
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".amazon.com", path="/")
    session.headers.update(
        {"User-Agent": "ai-instagram-organizer/1.0", "Accept": "application/json"}
    )
    return session


def checked(response):
    if response.status_code == 401:
        raise PermissionError(
            "AUTH_EXPIRED: refresh cookies via a manual browser login and rerun"
        )
    response.raise_for_status()
    return response


def search(session, start, end):
    filt = (
        "type:(PHOTOS) AND createdDate:["
        f"{start.isoformat()}T00:00:00.000Z TO {end.isoformat()}T00:00:00.000Z]"
    )
    offset = 0
    while True:
        response = checked(
            session.get(
                f"{API}/search",
                params={
                    "filters": filt,
                    "limit": 200,
                    "offset": offset,
                    "sort": "['createdDate DESC']",
                    "searchContext": "customer",
                },
                timeout=60,
            )
        )
        rows = response.json().get("data", [])
        yield from rows
        if len(rows) < 200:
            break
        offset += 200
        time.sleep(0.3)


def selected_nodes(rows, after, before, straggler_year=None):
    found, unknown, seen = [], 0, set()
    for row in rows:
        info = node_info(row)
        node_id = info["id"]
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        taken = taken_date(info)
        if taken is None:
            unknown += 1
        elif after <= taken <= before or (
            straggler_year and taken.year == straggler_year
        ):
            info["taken"] = taken.isoformat()
            found.append(info)
    return found, unknown


def run(
    cookie_file,
    base_dir,
    state_file,
    taken_after,
    taken_before,
    upload_since=None,
    straggler_year=None,
    download=False,
    session=None,
    today=None,
):
    base_dir, state_file = Path(base_dir), Path(state_file)
    after, before = date.fromisoformat(taken_after), date.fromisoformat(taken_before)
    if before < after:
        raise ValueError("taken-before must be on or after taken-after")
    today = today or date.today()
    since = (
        date.fromisoformat(upload_since) if upload_since else today - timedelta(days=90)
    )
    session = session or make_session(cookie_file)
    known = local_ids(base_dir)
    prior = json.loads(state_file.read_text()) if state_file.exists() else {}
    downloaded = set(prior.get("downloaded", []))
    # Reuse a matching scan, while retaining downloaded IDs across rescans.
    criteria = {
        "taken_after": taken_after,
        "taken_before": taken_before,
        "upload_since": since.isoformat(),
        "straggler_year": straggler_year,
        "through": today.isoformat(),
    }
    if prior.get("criteria") == criteria:
        found, unknown = prior["selected"], prior["unknown"]
    else:
        rows = (
            node
            for start, end in month_windows(since, today + timedelta(days=1))
            for node in search(session, start, end)
        )
        found, unknown = selected_nodes(rows, after, before, straggler_year)
    state = {
        "criteria": criteria,
        "selected": found,
        "unknown": unknown,
        "downloaded": sorted(downloaded),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))
    todo = [n for n in found if n["id"] not in known and n["id"] not in downloaded]
    print(f"selected={len(found)} missing={len(todo)} unknown_date={unknown}")
    if not download:
        return state
    occupied = set()
    for info in todo:
        target = destination(
            base_dir, info, date.fromisoformat(info["taken"]), occupied
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        response = checked(
            session.get(f"{API}/nodes/{info['id']}/content", timeout=120, stream=True)
        )
        try:
            with target.open("wb") as output:
                for chunk in response.iter_content(256 * 1024):
                    output.write(chunk)
            if target.stat().st_size == 0:
                target.unlink()
                continue
        except Exception:
            target.unlink(missing_ok=True)
            raise
        downloaded.add(info["id"])
        state["downloaded"] = sorted(downloaded)
        state_file.write_text(json.dumps(state, indent=2))
        time.sleep(0.2)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cookie-file", required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--taken-after", required=True)
    parser.add_argument("--taken-before", required=True)
    parser.add_argument("--upload-since")
    parser.add_argument("--straggler-year", type=int)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(**vars(args))
    except PermissionError as error:
        parser.exit(2, f"{error}\n")
    return 0
