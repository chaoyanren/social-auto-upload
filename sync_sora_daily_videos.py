import argparse
import io
import json
import os
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image


def find_latest_daily_dir(source_root: Path) -> Path:
    candidates = [p for p in source_root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8]
    if not candidates:
        raise FileNotFoundError(f"No daily folders like YYYYMMDD under: {source_root}")
    return sorted(candidates, key=lambda p: p.name, reverse=True)[0]


def pick_thumbnail_url(item: dict) -> str:
    url = (item.get("thumbnail") or "").strip()
    if url:
        return url
    for cand in item.get("candidates", []) or []:
        if isinstance(cand, str) and ("thumbnail" in cand) and cand.startswith("http"):
            return cand
    return ""


def load_manifest_index(manifest_path: Path) -> tuple[dict, dict]:
    if not manifest_path.exists():
        return {}, {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_asset = {}
    for item in (data or []):
        asset_id = item.get("asset_id")
        if asset_id:
            by_asset[asset_id] = item

    # Precompute duplicate thumbnail URLs (manifest data is sometimes wrong and reuses the same thumbnail).
    thumb_counts: dict[str, int] = {}
    for item in by_asset.values():
        url = pick_thumbnail_url(item)
        if url:
            thumb_counts[url] = thumb_counts.get(url, 0) + 1

    return by_asset, thumb_counts


def download_thumbnail_image(url: str, out_path: Path, overwrite: bool) -> bool:
    if out_path.exists() and not overwrite:
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)

    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = None
    last_err = None
    for _ in range(2):
        try:
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            break
        except Exception as e:
            last_err = e
            data = None

    if data is None:
        # Fallback to curl (helps on some Windows SSL/network setups).
        tmp = out_path.with_suffix(out_path.suffix + ".curl")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "curl",
            "--ssl-no-revoke",
            "-L",
            "--fail",
            "--connect-timeout",
            "10",
            "--max-time",
            "60",
            "-o",
            str(tmp),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not tmp.exists():
            raise RuntimeError(f"thumbnail download failed: {last_err}")
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)

    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    suffix = out_path.suffix.lower()
    tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")
    if suffix == ".png":
        img.save(tmp_out, format="PNG", optimize=True)
    else:
        img.save(tmp_out, format="JPEG", quality=90, optimize=True)
    os.replace(tmp_out, out_path)
    return True


def probe_video_duration_seconds(video_path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-hide_banner",
                "-loglevel",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        return float((proc.stdout or "").strip())
    except Exception:
        return None


def extract_cover_frame(video_path: Path, out_path: Path, overwrite: bool, prefer_seconds: float) -> bool:
    if out_path.exists() and not overwrite:
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration = probe_video_duration_seconds(video_path)
    ts = prefer_seconds
    if duration and duration > 0:
        ts = min(prefer_seconds, max(0.0, duration / 2.0))

    # Keep a real image extension so ffmpeg can infer the output format.
    tmp_out = out_path.with_suffix(".tmp" + out_path.suffix)
    # Try the preferred timestamp; if that fails, fall back to the first frame.
    for attempt_ts in (ts, 0.0):
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    str(attempt_ts),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    str(tmp_out),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:
            proc = None
        if proc and proc.returncode == 0 and tmp_out.exists():
            os.replace(tmp_out, out_path)
            return True
    if tmp_out.exists():
        tmp_out.unlink(missing_ok=True)
    return False


def title_for_file(video_path: Path, manifest_map: dict) -> str:
    stem = video_path.stem
    item = manifest_map.get(stem)
    if not item:
        return stem
    title = (item.get("title") or "").strip()
    if title:
        return title.replace("\r", " ").replace("\n", " ").strip()
    return stem


def sync_videos(
    source_dir: Path,
    target_dir: Path,
    limit: int,
    overwrite: bool,
    cover_strategy: str,
    cover_frame_seconds: float,
) -> list[dict]:
    manifest_map, thumb_counts = load_manifest_index(source_dir / "manifest.latest.json")
    files = sorted(source_dir.glob("*.mp4"), key=lambda p: p.name)
    if limit and limit > 0:
        files = files[:limit]

    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[dict] = []

    for src in files:
        dst = target_dir / src.name
        if dst.exists() and not overwrite:
            continue

        shutil.copy2(src, dst)

        title = title_for_file(src, manifest_map)
        txt_path = dst.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"{title}\n#sora\n")

        item = manifest_map.get(src.stem, {}) if manifest_map else {}
        thumbnail_url = pick_thumbnail_url(item) if item else ""
        is_duplicate_thumb = bool(thumbnail_url) and thumb_counts.get(thumbnail_url, 0) > 1

        thumbnail_file = ""
        cover_source = None
        cover_reason = None

        # Keep the same stem as the mp4 so upload scripts can auto-detect.
        cover_path = target_dir / f"{src.stem}.png"
        cover_strategy = (cover_strategy or "auto").lower().strip()

        def try_manifest_cover() -> bool:
            nonlocal thumbnail_file, cover_source, cover_reason
            if not thumbnail_url:
                cover_reason = "no_thumbnail_url"
                return False
            if is_duplicate_thumb:
                cover_reason = "thumbnail_url_duplicated_in_manifest"
                return False
            try:
                download_thumbnail_image(thumbnail_url, cover_path, overwrite=overwrite)
                thumbnail_file = str(cover_path)
                cover_source = "manifest"
                return True
            except Exception as e:
                cover_reason = f"thumbnail_download_failed: {e}"
                return False

        def try_frame_cover() -> bool:
            nonlocal thumbnail_file, cover_source, cover_reason
            ok = extract_cover_frame(dst, cover_path, overwrite=overwrite, prefer_seconds=cover_frame_seconds)
            if ok:
                thumbnail_file = str(cover_path)
                cover_source = "frame"
                if cover_reason is None:
                    cover_reason = "cover_strategy_frame"
                return True
            cover_reason = cover_reason or "frame_extract_failed"
            return False

        if cover_strategy == "manifest":
            try_manifest_cover()
        elif cover_strategy == "frame":
            try_frame_cover()
        else:  # auto
            if not try_manifest_cover():
                try_frame_cover()

        if thumbnail_file:
            print(f"cover: {Path(thumbnail_file).name} ({cover_source})")

        synced.append(
            {
                "name": src.name,
                "mp4": str(dst),
                "txt": str(txt_path),
                "title": title,
                "thumbnail_url": thumbnail_url or None,
                "thumbnail_file": thumbnail_file or None,
                "cover_source": cover_source,
                "cover_reason": cover_reason,
            }
        )
        print(f"synced: {src.name}")

    return synced


def write_record(record_file: Path, source_dir: Path, target_dir: Path, synced: list[dict]) -> None:
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "synced_count": len(synced),
        "synced_files": synced,
    }
    with open(record_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Sync daily Sora downloader outputs into social-auto-upload/videos."
    )
    parser.add_argument(
        "--source-root",
        default=r"D:\Development\sora-ai-video-downloader-python\videos",
        help="Root folder that contains daily YYYYMMDD subfolders.",
    )
    parser.add_argument(
        "--date",
        default="latest",
        help="Date folder name YYYYMMDD, or 'latest'.",
    )
    parser.add_argument(
        "--target-dir",
        default="videos",
        help="Target folder in social-auto-upload for uploader scripts.",
    )
    parser.add_argument(
        "--record-file",
        default="videos/.sync_last.json",
        help="Write synced file list for follow-up upload/cleanup.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only sync first N videos (0=all).")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing mp4/txt.")
    parser.add_argument(
        "--cover-strategy",
        default="auto",
        choices=["auto", "manifest", "frame"],
        help="How to generate cover images for upload. 'auto' prefers manifest thumbnail when unique, else extracts a frame from mp4.",
    )
    parser.add_argument(
        "--cover-frame-seconds",
        type=float,
        default=5.0,
        help="When cover-strategy uses 'frame', extract a frame around this timestamp (falls back to first frame).",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    target_dir = Path(args.target_dir).resolve()
    record_file = Path(args.record_file).resolve()

    if args.date.lower() == "latest":
        source_dir = find_latest_daily_dir(source_root)
    else:
        source_dir = source_root / args.date
        if not source_dir.exists():
            raise FileNotFoundError(f"Date folder not found: {source_dir}")

    print(f"source: {source_dir}")
    print(f"target: {target_dir}")

    synced = sync_videos(
        source_dir,
        target_dir,
        args.limit,
        args.overwrite,
        cover_strategy=args.cover_strategy,
        cover_frame_seconds=args.cover_frame_seconds,
    )
    write_record(record_file, source_dir, target_dir, synced)
    print(f"record: {record_file}")
    print(f"done. synced files: {len(synced)}")


if __name__ == "__main__":
    main()
