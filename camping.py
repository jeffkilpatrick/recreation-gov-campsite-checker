# -*- coding: utf-8 -*-
#!/usr/bin/env python3

import json
import logging
import os
import smtplib
import ssl
import yaml

from collections import defaultdict
from datetime import datetime, timedelta
from itertools import count, groupby
from typing import Any, Callable, Dict, Iterable, List, Optional

from dateutil import rrule

import formatters as f

from clients.recreation_client import RecreationClient
from enums.date_format import DateFormat
from enums.emoji import Emoji
from utils import formatter
from utils.camping_argparser import CampingArgumentParser


SETTINGS_FILE = os.path.join(os.environ["HOME"], ".campsite-checker.yml")


LOG = logging.getLogger(__name__)
log_formatter = logging.Formatter(
    "%(asctime)s - %(process)s - %(levelname)s - %(message)s"
)
sh = logging.StreamHandler()
sh.setFormatter(log_formatter)
LOG.addHandler(sh)

def get_park_information(
    park_id, start_date, end_date, campsite_type=None, campsite_ids=()
):
    """
    This function consumes the user intent, collects the necessary information
    from the recreation.gov API, and then presents it in a nice format for the
    rest of the program to work with. If the API changes in the future, this is
    the only function you should need to change.

    The only API to get availability information is the `month?` query param
    on the availability endpoint. You must query with the first of the month.
    This means if `start_date` and `end_date` cross a month boundary, we must
    hit the endpoint multiple times.

    The output of this function looks like this:

    {"<campsite_id>": [<date>, <date>]}

    Where the values are a list of ISO 8601 date strings representing dates
    where the campsite is available.

    Notably, the output doesn't tell you which sites are available. The rest of
    the script doesn't need to know this to determine whether sites are available.
    """

    # Get each first of the month for months in the range we care about.
    start_of_month = datetime(start_date.year, start_date.month, 1)
    months = list(
        rrule.rrule(rrule.MONTHLY, dtstart=start_of_month, until=end_date)
    )

    # Get data for each month.
    api_data = []
    for month_date in months:
        api_data.append(RecreationClient.get_availability(park_id, month_date))

    # Collapse the data into the described output format.
    # Filter by campsite_type if necessary.
    data = {}

    for month_data in api_data:
        for campsite_id, campsite_data in month_data["campsites"].items():
            available = []
            a = data.setdefault(campsite_id, [])
            for date, availability_value in campsite_data[
                "availabilities"
            ].items():
                if availability_value != "Available":
                    continue

                if (
                    campsite_type
                    and campsite_type != campsite_data["campsite_type"]
                ):
                    continue

                if (
                    len(campsite_ids) > 0
                    and int(campsite_data["campsite_id"]) not in campsite_ids
                ):
                    continue

                available.append(date)
            if available:
                a += available

    return data


def is_weekend(date):
    weekday = date.weekday()

    return weekday == 4 or weekday == 5


def get_num_available_sites(
    park_information, start_date: datetime, end_date: datetime, nights: Optional[int] = None, weekends_only: bool = False,
) -> f.AVAILABLE_SITES_BY_DATE:
    maximum = len(park_information)

    num_available = 0
    num_days = (end_date - start_date).days
    raw_dates: Iterable[datetime] = [end_date - timedelta(days=i) for i in range(1, num_days + 1)]
    if weekends_only:
        raw_dates = filter(is_weekend, raw_dates)
    dates: Iterable[str] = set(
        formatter.format_date(
            i, format_string=DateFormat.ISO_DATE_FORMAT_RESPONSE.value
        )
        for i in raw_dates
    )

    if nights not in range(1, num_days + 1):
        nights = num_days
        LOG.debug("Setting number of nights to {}.".format(nights))

    available_dates_by_campsite_id: Dict[int, List[Dict[str, str]]] = defaultdict(list)
    for site, availabilities in park_information.items():
        # List of dates that are in the desired range for this site.
        desired_available = []

        for date in availabilities:
            if date not in dates:
                continue
            desired_available.append(date)

        if not desired_available:
            continue

        appropriate_consecutive_ranges = consecutive_nights(
            desired_available, nights
        )

        if appropriate_consecutive_ranges:
            num_available += 1
            LOG.debug("Available site {}: {}".format(num_available, site))

        for r in appropriate_consecutive_ranges:
            start, end = r
            available_dates_by_campsite_id[int(site)].append(
                {"start": start, "end": end}
            )

    return num_available, maximum, available_dates_by_campsite_id


def consecutive_nights(available, nights):
    """
    Returns a list of dates from which you can start that have
    enough consecutive nights.

    If there is one or more entries in this list, there is at least one
    date range for this site that is available.
    """
    ordinal_dates = [
        datetime.strptime(
            dstr, DateFormat.ISO_DATE_FORMAT_RESPONSE.value
        ).toordinal()
        for dstr in available
    ]
    c = count()

    consecutive_ranges = list(
        list(g) for _, g in groupby(ordinal_dates, lambda x: x - next(c))
    )

    long_enough_consecutive_ranges = []
    for r in consecutive_ranges:
        # Skip ranges that are too short.
        if len(r) < nights:
            continue
        for start_index in range(0, len(r) - nights + 1):
            start_nice = formatter.format_date(
                datetime.fromordinal(r[start_index]),
                format_string=DateFormat.INPUT_DATE_FORMAT.value,
            )
            end_nice = formatter.format_date(
                datetime.fromordinal(r[start_index + nights - 1] + 1),
                format_string=DateFormat.INPUT_DATE_FORMAT.value,
            )
            long_enough_consecutive_ranges.append((start_nice, end_nice))

    return long_enough_consecutive_ranges


def check_park(
    park_id, start_date, end_date, campsite_type, campsite_ids=(), nights=None, weekends_only=False, excluded_site_ids=[],
) -> f.AVAILABLE_PARK_SITES_BY_DATE:
    park_information = get_park_information(
        park_id, start_date, end_date, campsite_type, campsite_ids
    )
    LOG.debug(
        "Information for park {}: {}".format(
            park_id, json.dumps(park_information, indent=2)
        )
    )
    park_name = RecreationClient.get_park_name(park_id)
    current, maximum, availabilities_filtered = get_num_available_sites(
        park_information, start_date, end_date, nights=nights, weekends_only=weekends_only,
    )
    return current, maximum, availabilities_filtered, park_name


def generate_human_output(
    info_by_park_id: Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE], start_date: datetime, end_date: datetime, gen_campsite_info=False
):
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
        if gen_campsite_info and available_dates_by_site_id:
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
            "there are campsites available from {start} to {end}!!!".format(
                start=start_date.strftime(DateFormat.INPUT_DATE_FORMAT.value),
                end=end_date.strftime(DateFormat.INPUT_DATE_FORMAT.value),
            ),
        )
    else:
        out.insert(0, "There are no campsites available :(")
    return "\n".join(out), has_availabilities


def generate_json_output(info_by_park_id: Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE]):
    availabilities_by_park_id = {}
    has_availabilities = False
    for park_id, info in info_by_park_id.items():
        current, _, available_dates_by_site_id, _ = info
        if current:
            has_availabilities = True
            availabilities_by_park_id[park_id] = available_dates_by_site_id

    return json.dumps(availabilities_by_park_id), has_availabilities


def parse_settings() -> Dict[str, Any]:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as settings_file:
            return yaml.safe_load(settings_file)
    else:
        return {"print": {"enabled": True}}


REPORTER = Callable[[Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE], bool], None]


def mail_reporter(settings: Dict[str, Any], formatter: f.FORMATTER) -> REPORTER:
    SUBJECT = "Campsite Watcher"

    def report(info_by_park_id: Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE], has_availabilities: bool) -> None:
        message_ascii = formatter(info_by_park_id, has_availabilities)
        if message_ascii is None:
            return
        body = f"""Subject: {SUBJECT}
From: {str(settings["from_name"])} <{str(settings["from_email"])}>
To: {", ".join(settings["recipients"])}

{message_ascii}"""
        context = ssl.create_default_context()
        with smtplib.SMTP(str(settings["host"]), int(settings["port"])) as server:
            server.ehlo()
            if bool(settings.get("tls", True)):
                server.starttls(context=context)
                server.ehlo()
            if settings.get("user", None):
                assert settings.get("password", None) is not None
                server.login(settings.get("user", None), settings.get("password", None))
            server.sendmail(str(settings["from_email"]), settings["recipients"], body)

    return report


def get_reporters(settings: Dict[str, Any], start_date: datetime, end_date: datetime, show_campsite_info=False) -> Iterable[REPORTER]:
    reporters: List[REPORTER] = []

    # if "print" in settings and settings["print"].get("enabled", True):
    #     def printer(info_by_park_id: Dict[int, AVAILABLE_PARK_SITES_BY_DATE], _: bool) -> None:
    #         output, has_availabilities = generate_human_output(
    #             info_by_park_id,
    #             args.start_date,
    #             args.end_date,
    #             args.show_campsite_info,
    #         )
    #         if has_availabilities and not settings.get("require_availability", False):
    #             print(output)
    #     reporters.append(printer)

    if "print" in settings and settings["print"].get("enabled", True):
        print_settings = settings["print"]
        def printer(info_by_park_id: Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE], has_availabilities: bool) -> None:
            formatter = f.make_formatter(print_settings)
            formatted = formatter(info_by_park_id, has_availabilities)
            if formatted is not None:
                print(formatted)
        reporters.append(printer)


    if "smtp" in settings and settings["smtp"].get("enabled", True):
        smtp_settings = settings["smtp"]
        formatter = f.make_formatter(smtp_settings)
        reporters.append(mail_reporter(smtp_settings, formatter))

    return reporters

def main(parks, json_output=False, reporters: Iterable[REPORTER] = [print]) -> bool:
    info_by_park_id: Dict[int, f.AVAILABLE_PARK_SITES_BY_DATE] = {}
    for park_id in parks:
        info_by_park_id[park_id] = check_park(
            park_id,
            args.start_date,
            args.end_date,
            args.campsite_type,
            args.campsite_ids,
            nights=args.nights,
            weekends_only=args.weekends_only,
        )

    _, has_availabilities = generate_json_output(info_by_park_id)

    for reporter in reporters:
        reporter(info_by_park_id, has_availabilities)
    return has_availabilities


if __name__ == "__main__":
    parser = CampingArgumentParser()
    args = parser.parse_args()

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    settings = parse_settings()
    reporters = get_reporters(settings, args.start_date, args.end_date, args.show_campsite_info)

    main(args.parks, json_output=args.json_output, reporters=reporters)
