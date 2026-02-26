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


def is_wm_video_file(video_path: Path) -> bool:
    return video_path.stem.lower().endswith("_wm")


def normalized_asset_id_from_video(video_path: Path) -> str:
    stem = video_path.stem
    if stem.lower().endswith("_wm"):
        return stem[:-3]
    return stem


def list_syncable_videos(source_dir: Path, video_variant: str) -> list[Path]:
    files = [p for p in source_dir.glob("*.mp4") if p.is_file()]
    if video_variant == "main_only":
        return [p for p in files if not is_wm_video_file(p)]
    if video_variant == "wm_only":
        return [p for p in files if is_wm_video_file(p)]
    return files


def resolve_source_dir(source_root: Path, date_arg: str, video_variant: str) -> tuple[Path, str]:
    date_value = (date_arg or "latest").strip()
    date_lower = date_value.lower()

    if date_lower == "latest":
        daily_dirs = [p for p in source_root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8]
        if daily_dirs:
            return sorted(daily_dirs, key=lambda p: p.name, reverse=True)[0], "daily"
        if list_syncable_videos(source_root, video_variant):
            return source_root, "flat"
        raise FileNotFoundError(
            f"No daily folders like YYYYMMDD under: {source_root}, and no syncable videos found under source root. "
            "Try: 1) run downloader first; 2) use --date root to force flat source-root mode."
        )

    if date_lower in {"root", "flat", "."}:
        if not list_syncable_videos(source_root, video_variant):
            raise FileNotFoundError(
                f"No syncable videos found under source root: {source_root}. "
                "Try: 1) run downloader first; 2) switch --video-variant."
            )
        return source_root, "flat"

    source_dir = source_root / date_value
    if source_dir.exists() and source_dir.is_dir():
        return source_dir, "daily"

    raise FileNotFoundError(
        f"Date folder not found: {source_dir}. "
        "Try: 1) use --date latest; 2) use --date root for flat source-root mode."
    )


def pick_thumbnail_url(item: dict) -> str:
    url = (item.get("thumbnail") or "").strip()
    if url:
        return url
    for cand in item.get("candidates", []) or []:
        if isinstance(cand, str) and ("thumbnail" in cand) and cand.startswith("http"):
            return cand
    return ""


def load_manifest_index(manifest_path: Path) -> tuple[list[dict], dict, dict]:
    if not manifest_path.exists():
        return [], {}, {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else []
    by_asset = {}
    for item in entries:
        asset_id = item.get("asset_id")
        if asset_id:
            by_asset[asset_id] = item

    # Precompute duplicate thumbnail URLs (manifest data is sometimes wrong and reuses the same thumbnail).
    thumb_counts: dict[str, int] = {}
    for item in by_asset.values():
        url = pick_thumbnail_url(item)
        if url:
            thumb_counts[url] = thumb_counts.get(url, 0) + 1

    return entries, by_asset, thumb_counts


def candidate_names_for_asset(asset_id: str, video_variant: str) -> list[str]:
    if video_variant == "wm_only":
        return [f"{asset_id}_wm.mp4"]
    if video_variant == "main_only":
        return [f"{asset_id}.mp4"]
    return [f"{asset_id}.mp4", f"{asset_id}_wm.mp4"]


def ordered_source_videos(
    source_dir: Path,
    manifest_entries: list[dict],
    limit: int,
    video_variant: str,
    source_mode: str,
) -> list[Path]:
    files = list_syncable_videos(source_dir, video_variant)
    if not files:
        return []

    # Flat mode: prioritize newest manifest assets, then backfill by file mtime.
    if source_mode == "flat":
        by_name = {p.name: p for p in files}
        selected: list[Path] = []
        used_names: set[str] = set()
        seen_assets: set[str] = set()

        for item in manifest_entries:
            asset_id = str(item.get("asset_id") or "").strip()
            if not asset_id or asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            for name in candidate_names_for_asset(asset_id, video_variant):
                if name in used_names:
                    continue
                matched = by_name.get(name)
                if matched:
                    selected.append(matched)
                    used_names.add(name)

        remaining = [p for p in files if p.name not in used_names]
        remaining.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
        selected.extend(remaining)
    else:
        selected = sorted(files, key=lambda p: p.name)

    if limit and limit > 0:
        return selected[:limit]
    return selected


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
    stem = normalized_asset_id_from_video(video_path)
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
    video_variant: str,
    source_mode: str,
) -> list[dict]:
    manifest_entries, manifest_map, thumb_counts = load_manifest_index(source_dir / "manifest.latest.json")
    files = ordered_source_videos(
        source_dir,
        manifest_entries,
        limit=limit,
        video_variant=video_variant,
        source_mode=source_mode,
    )

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

        asset_id = normalized_asset_id_from_video(src)
        item = manifest_map.get(asset_id, {}) if manifest_map else {}
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
                "asset_id": asset_id,
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


def write_record(
    record_file: Path,
    source_dir: Path,
    target_dir: Path,
    synced: list[dict],
    source_mode: str,
    video_variant: str,
) -> None:
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(source_dir),
        "source_mode": source_mode,
        "video_variant": video_variant,
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
        help="Root folder for downloader outputs. Supports daily YYYYMMDD folders and flat mp4 layout.",
    )
    parser.add_argument(
        "--date",
        default="latest",
        help="Date folder name YYYYMMDD, 'latest', or root/flat/. for flat source-root mode.",
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
    parser.add_argument(
        "--video-variant",
        default="all",
        choices=["all", "main_only", "wm_only"],
        help="Choose which mp4 variant to sync: all, main_only, or wm_only (wm_only means no-watermark in this workflow).",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    target_dir = Path(args.target_dir).resolve()
    record_file = Path(args.record_file).resolve()

    source_dir, source_mode = resolve_source_dir(source_root, args.date, args.video_variant)

    print(f"source: {source_dir}")
    print(f"source mode: {source_mode}")
    print(f"target: {target_dir}")

    synced = sync_videos(
        source_dir,
        target_dir,
        args.limit,
        args.overwrite,
        cover_strategy=args.cover_strategy,
        cover_frame_seconds=args.cover_frame_seconds,
        video_variant=args.video_variant,
        source_mode=source_mode,
    )
    write_record(
        record_file,
        source_dir,
        target_dir,
        synced,
        source_mode=source_mode,
        video_variant=args.video_variant,
    )
    print(f"record: {record_file}")
    print(f"done. synced files: {len(synced)}")


if __name__ == "__main__":
    main()
