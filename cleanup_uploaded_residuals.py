import argparse
import json
from datetime import datetime
from pathlib import Path


def load_pending_stems(record_file: Path) -> set[str]:
    if not record_file.exists():
        return set()
    try:
        with open(record_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return set()
    stems: set[str] = set()
    for item in data.get("synced_files", []):
        if not isinstance(item, dict):
            continue
        mp4 = item.get("mp4")
        if not mp4:
            continue
        stem = Path(mp4).stem
        if stem:
            stems.add(stem)
    return stems


def artifact_stem(path: Path, prefix: str) -> str | None:
    name = path.name
    lower_name = name.lower()
    if lower_name.endswith(".cover.jpg"):
        stem = name[:-10]
    elif path.suffix.lower() in {".mp4", ".txt", ".png"}:
        stem = path.stem
    else:
        return None
    if not stem.startswith(prefix):
        return None
    return stem


def collect_artifact_groups(videos_dir: Path, prefix: str) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in videos_dir.iterdir():
        if not path.is_file():
            continue
        stem = artifact_stem(path, prefix)
        if not stem:
            continue
        groups.setdefault(stem, []).append(path)
    return groups


def newest_mtime(paths: list[Path]) -> float:
    return max(p.stat().st_mtime for p in paths)


def should_delete_group(
    stem: str,
    paths: list[Path],
    pending_stems: set[str],
    min_age_hours: float,
    now_ts: float,
) -> bool:
    if stem in pending_stems:
        return False
    age_hours = (now_ts - newest_mtime(paths)) / 3600.0
    if age_hours < min_age_hours:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time cleanup for uploaded residual files in social-auto-upload/videos."
    )
    parser.add_argument("--videos-dir", default="videos", help="Directory containing synced upload artifacts.")
    parser.add_argument(
        "--record-file",
        default="videos/.sync_last.json",
        help="Sync record. Stems still listed here are treated as pending and kept.",
    )
    parser.add_argument(
        "--prefix",
        default="s_",
        help="Only filenames with this stem prefix are considered cleanup candidates.",
    )
    parser.add_argument(
        "--min-age-hours",
        type=float,
        default=1.0,
        help="Delete only files older than this age in hours.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete files. Without this flag, script only prints dry-run plan.",
    )
    args = parser.parse_args()

    videos_dir = Path(args.videos_dir).resolve()
    record_file = Path(args.record_file).resolve()
    if not videos_dir.exists() or not videos_dir.is_dir():
        raise FileNotFoundError(f"videos dir not found: {videos_dir}")

    pending_stems = load_pending_stems(record_file)
    groups = collect_artifact_groups(videos_dir, args.prefix)
    now_ts = datetime.now().timestamp()

    delete_targets: list[Path] = []
    keep_count = 0
    for stem, paths in sorted(groups.items()):
        if should_delete_group(stem, paths, pending_stems, args.min_age_hours, now_ts):
            delete_targets.extend(sorted(paths, key=lambda p: p.name))
        else:
            keep_count += len(paths)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"mode: {mode}")
    print(f"videos_dir: {videos_dir}")
    print(f"record_file: {record_file}")
    print(f"pending_stems: {len(pending_stems)}")
    print(f"keep_files: {keep_count}")
    print(f"delete_files: {len(delete_targets)}")

    for path in delete_targets:
        if args.apply:
            path.unlink(missing_ok=True)
            print(f"deleted: {path}")
        else:
            print(f"would delete: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

