import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from conf import BASE_DIR
from uploader.douyin_uploader.main import DouYinVideo, douyin_setup
from utils.files_times import generate_schedule_time_next_day, get_title_and_hashtags


def load_record(record_file: Path) -> dict:
    if not record_file.exists():
        return {}
    with open(record_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    synced = data.get("synced_files")
    if not isinstance(synced, list):
        data["synced_files"] = []
    return data


def save_record(record_file: Path, record: dict) -> None:
    record_file.parent.mkdir(parents=True, exist_ok=True)
    with open(record_file, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def safe_unlink(path: Path | None) -> bool:
    if path and path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def build_entries(record: dict) -> list[dict]:
    entries: list[dict] = []
    for item in record.get("synced_files", []):
        if not isinstance(item, dict):
            continue
        mp4 = item.get("mp4")
        if not mp4:
            continue

        video_path = Path(mp4)
        if not video_path.exists():
            continue

        txt_path = None
        txt_value = item.get("txt")
        if txt_value:
            cand_txt = Path(txt_value)
            if cand_txt.exists():
                txt_path = cand_txt

        thumb = item.get("thumbnail_file")
        thumb_path = Path(thumb) if thumb else None
        if thumb_path and not thumb_path.exists():
            thumb_path = None

        # Backward-compatible fallbacks.
        if thumb_path is None:
            for cand in (video_path.with_suffix(".cover.jpg"), video_path.with_suffix(".png")):
                if cand.exists():
                    thumb_path = cand
                    break

        entries.append(
            {
                "item": item,
                "video": video_path,
                "txt": txt_path,
                "thumbnail": thumb_path,
            }
        )
    return entries


def write_remaining_record(record_file: Path, record: dict, remaining_items: list[dict]) -> None:
    record["synced_files"] = remaining_items
    record["synced_count"] = len(remaining_items)
    record["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_record(record_file, record)


def delete_uploaded_artifacts(entry: dict) -> None:
    video_path = entry.get("video")
    txt_path = entry.get("txt")
    thumb_path = entry.get("thumbnail")
    if safe_unlink(video_path):
        print(f"deleted uploaded video: {video_path}")
    if safe_unlink(txt_path):
        print(f"deleted uploaded text: {txt_path}")
    if safe_unlink(thumb_path):
        print(f"deleted uploaded thumbnail: {thumb_path}")


def parse_daily_times(value: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            hour_text, minute_text = part.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        else:
            hour = int(part)
            minute = 0
        if hour < 0 or hour > 23:
            raise ValueError(f"Invalid hour: {hour}")
        if minute < 0 or minute > 59:
            raise ValueError(f"Invalid minute: {minute}")
        result.append((hour, minute))
    if not result:
        result = [(15, 0)]
    return result


async def upload_files(
    record_file: Path,
    record: dict,
    entries: list[dict],
    daily_times: list[tuple[int, int]],
    videos_per_day: int = 0,
) -> int:
    account_file = Path(BASE_DIR) / "cookies" / "douyin_uploader" / "account.json"

    effective_videos_per_day = videos_per_day if videos_per_day > 0 else min(len(entries), len(daily_times))
    if effective_videos_per_day > len(daily_times):
        raise ValueError("videos_per_day should not exceed number of daily times")

    publish_datetimes = generate_schedule_time_next_day(
        len(entries),
        effective_videos_per_day,
        daily_times=daily_times,
    )

    ok = await douyin_setup(account_file, handle=False)
    if not ok:
        raise RuntimeError("Douyin cookie is invalid. Please re-login to refresh cookies.")

    for index, entry in enumerate(entries):
        file = entry["video"]
        thumbnail_path = entry.get("thumbnail")
        title, tags = get_title_and_hashtags(str(file))

        print(f"uploading: {file}")
        print(f"title: {title}")
        print(f"tags: {tags}")

        try:
            if isinstance(thumbnail_path, Path) and thumbnail_path.exists():
                app = DouYinVideo(
                    title,
                    file,
                    tags,
                    publish_datetimes[index],
                    account_file,
                    thumbnail_path=thumbnail_path,
                )
            else:
                app = DouYinVideo(title, file, tags, publish_datetimes[index], account_file)
            await app.main()
        except Exception:
            remaining_items = [x["item"] for x in entries[index:]]
            write_remaining_record(record_file, record, remaining_items)
            raise

        delete_uploaded_artifacts(entry)
        remaining_items = [x["item"] for x in entries[index + 1 :]]
        write_remaining_record(record_file, record, remaining_items)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload files listed in sync record to Douyin.")
    parser.add_argument(
        "--record-file",
        default="videos/.sync_last.json",
        help="Record file generated by sync_sora_daily_videos.py",
    )
    parser.add_argument(
        "--daily-times",
        default="15",
        help="Comma-separated publish times, e.g. 15 or 10,12:30,20",
    )
    parser.add_argument(
        "--videos-per-day",
        type=int,
        default=0,
        help="How many videos to schedule per day (0 means auto = len(daily-times)).",
    )
    args = parser.parse_args()

    record_file = Path(args.record_file).resolve()
    record = load_record(record_file)
    entries = build_entries(record)
    if not entries:
        print(f"No synced files found in record: {record_file}")
        return 2

    daily_times = parse_daily_times(args.daily_times)
    try:
        asyncio.run(
            upload_files(
                record_file,
                record,
                entries,
                daily_times,
                videos_per_day=args.videos_per_day,
            ),
            debug=False,
        )
    except Exception as e:
        print(f"Upload error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
