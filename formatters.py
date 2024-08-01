from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from clients.recreation_client import RecreationClient
from enums.emoji import Emoji
from hash_store import HashStore


AVAILABLE_PARK_SITES_BY_DATE = Tuple[int, int, Dict[int, List[Dict[str, str]]], str]
AVAILABLE_SITES_BY_DATE = Tuple[int, int, Dict[int, List[Dict[str, str]]]]
FORMATTER = Callable[[Dict[int, AVAILABLE_PARK_SITES_BY_DATE], bool], Optional[str]]


def classic(settings: Dict[str, Any]) -> FORMATTER:
    def formatter(info_by_park_id: Dict[int, AVAILABLE_PARK_SITES_BY_DATE], has_availabilities: bool) -> Optional[str]:
        out = []
        has_availabilities = False
        for park_id, info in info_by_park_id.items():
            current, maximum, available_dates_by_site_id, park_name = info
            if current:
                emoji = Emoji.SUCCESS.value
                has_availabilities = True
            else:
                emoji = Emoji.FAILURE.value

            out.append(
                "{emoji} {park_name} ({park_id}): {current} site(s) available out of {maximum} site(s)".format(
                    emoji=emoji,
                    park_name=park_name,
                    park_id=park_id,
                    current=current,
                    maximum=maximum,
                )
            )

            # Displays campsite ID and availability dates.
            if available_dates_by_site_id:
                for site_id, dates in available_dates_by_site_id.items():
                    out.append(
                        "  * Site {site_id} is available on the following dates:".format(
                            site_id=site_id
                        )
                    )
                    for date in dates:
                        out.append(
                            "    * {start} -> {end}".format(
                                start=date["start"], end=date["end"]
                            )
                        )

        if has_availabilities:
            out.insert(
                0,
                "there are campsites available!!!"
            )
        else:
            out.insert(0, "There are no campsites available :(")
        return "\n".join(out)
    return formatter


def compress_dates(dates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(dates) == 0:
        return dates

    compressed: List[Dict[str, str]] = []
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    length: Optional[int] = None
    for entry in dates:
        this_start = datetime.fromisoformat(entry["start"])
        this_end = datetime.fromisoformat(entry["end"])
        if not start:
            # New range
            start = this_start
            end = this_end
            length = 1
            continue
        if end == this_start:
            # Extend the range
            assert length is not None
            end = this_end
            length += 1
        else:
            # Finish the range
            assert end is not None
            compressed.append({
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "length": str(length)
            })
            start = None
            end = None
            length = None
    if start:
        assert end is not None
        compressed.append({
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "length": str(length)
        })

    return compressed


def make_formatter(settings: Dict[str, Any]) -> FORMATTER:
    name = settings.get("format", "classic")
    check_hash = settings.get("check_hash", True)
    factory = globals().get(name, classic)
    formatter = factory(settings)

    def hash_checker(info_by_park_id: Dict[int, AVAILABLE_PARK_SITES_BY_DATE], has_availabilities: bool) -> Optional[str]:
        hash_store = HashStore()
        formatted = formatter(info_by_park_id, has_availabilities)
        is_new = hash_store.check_and_save(name, formatted or "")
        if is_new and formatted:
            return formatted
        return None

    return hash_checker if check_hash else formatter

def verbose_ascii(settings: Dict[str, Any]) -> FORMATTER:
    def formatter(info_by_park_id: Dict[int, AVAILABLE_PARK_SITES_BY_DATE], has_availabilities: bool) -> Optional[str]:
        messages: List[str] = []
        first = True
        for park_id, (num_available, num_sites, available_dates_by_site_id, park_name) in info_by_park_id.items():
            if num_available < 1:
                continue
            if first:
                first = False
            else:
                messages.append("")
            messages.append(f"-=-=- {park_name}: {num_available} of {num_sites} sites available -=-=-")

            for site_id, dates in available_dates_by_site_id.items():
                site_atts = RecreationClient.get_site_attributes(site_id)
                messages.append(f"Site {site_atts['campsite_name']} ({site_atts['campsite_type']}):")
                squashed_dates = compress_dates(dates)
                for d in squashed_dates:
                    messages.append(f" * {d['start']} -> {d['end']} ({d['length']} nights)")

        if not has_availabilities and settings.get("require_availability", False):
            return
        if len(messages) == 0:
            message = "No available campsites"
        else:
            message = "\n".join(messages)
        message_ascii = message.encode("utf-8").decode("ascii", "ignore")
        return message_ascii
    return formatter
