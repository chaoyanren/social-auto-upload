import argparse
import json
import shutil
from pathlib import Path


def find_latest_daily_dir(source_root: Path) -> Path:
    candidates = [p for p in source_root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8]
    if not candidates:
        raise FileNotFoundError(f"No daily folders like YYYYMMDD under: {source_root}")
    return sorted(candidates, key=lambda p: p.name, reverse=True)[0]


def load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.exists():
        return {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_asset = {}
    for item in data:
        asset_id = item.get("asset_id")
        if asset_id:
            by_asset[asset_id] = item
    return by_asset


def title_for_file(video_path: Path, manifest_map: dict) -> str:
    stem = video_path.stem
    item = manifest_map.get(stem)
    if not item:
        return stem
    title = (item.get("title") or "").strip()
    if title:
        return title.replace("\r", " ").replace("\n", " ").strip()
    return stem


def sync_videos(source_dir: Path, target_dir: Path, limit: int, overwrite: bool) -> int:
    manifest_map = load_manifest(source_dir / "manifest.latest.json")
    files = sorted(source_dir.glob("*.mp4"), key=lambda p: p.name)
    if limit and limit > 0:
        files = files[:limit]

    target_dir.mkdir(parents=True, exist_ok=True)
    copied = 0

    for src in files:
        dst = target_dir / src.name
        if dst.exists() and not overwrite:
            continue

        shutil.copy2(src, dst)

        title = title_for_file(src, manifest_map)
        txt_path = dst.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"{title}\n#sora\n")

        copied += 1
        print(f"synced: {src.name}")

    return copied


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
    parser.add_argument("--limit", type=int, default=0, help="Only sync first N videos (0=all).")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing mp4/txt.")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    target_dir = Path(args.target_dir).resolve()

    if args.date.lower() == "latest":
        source_dir = find_latest_daily_dir(source_root)
    else:
        source_dir = source_root / args.date
        if not source_dir.exists():
            raise FileNotFoundError(f"Date folder not found: {source_dir}")

    print(f"source: {source_dir}")
    print(f"target: {target_dir}")

    copied = sync_videos(source_dir, target_dir, args.limit, args.overwrite)
    print(f"done. synced files: {copied}")


if __name__ == "__main__":
    main()
