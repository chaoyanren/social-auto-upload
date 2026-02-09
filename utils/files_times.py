from datetime import datetime, timedelta
from pathlib import Path

from conf import BASE_DIR


def get_absolute_path(relative_path: str, base_dir: str = "") -> str:
    """Convert a relative path under BASE_DIR to an absolute path string."""
    return str(Path(BASE_DIR) / base_dir / relative_path)


def get_title_and_hashtags(filename):
    """Read title/hashtags from sibling .txt. Fallback to file stem if missing."""
    video_path = Path(filename)
    txt_path = video_path.with_suffix(".txt")

    if not txt_path.exists():
        return video_path.stem, []

    with open(txt_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]

    if not lines:
        return video_path.stem, []

    title = lines[0]
    hashtags = []
    if len(lines) >= 2:
        hashtags = [tag.replace("#", "").strip() for tag in lines[1].split(" ") if tag.strip()]

    return title, hashtags


def generate_schedule_time_next_day(total_videos, videos_per_day=1, daily_times=None, timestamps=False, start_days=0):
    """Generate publish schedule for N videos."""
    if videos_per_day <= 0:
        raise ValueError("videos_per_day should be a positive integer")

    if daily_times is None:
        daily_times = [6, 11, 14, 16, 22]

    if videos_per_day > len(daily_times):
        raise ValueError("videos_per_day should not exceed the length of daily_times")

    schedule = []
    current_time = datetime.now()

    for video in range(total_videos):
        day = video // videos_per_day + start_days
        daily_video_index = video % videos_per_day

        hour = daily_times[daily_video_index]
        time_offset = timedelta(
            days=day,
            hours=hour - current_time.hour,
            minutes=-current_time.minute,
            seconds=-current_time.second,
            microseconds=-current_time.microsecond,
        )
        schedule.append(current_time + time_offset)

    if timestamps:
        return [int(time.timestamp()) for time in schedule]
    return schedule

