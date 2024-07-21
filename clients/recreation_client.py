import logging
import time

import requests
import user_agent

from utils import formatter

LOG = logging.getLogger(__name__)


class RecreationClient:

    BASE_URL = "https://www.recreation.gov"
    AVAILABILITY_ENDPOINT = (
        BASE_URL + "/api/camps/availability/campground/{park_id}/month"
    )
    MAIN_PAGE_ENDPOINT = BASE_URL + "/api/camps/campgrounds/{park_id}"

    headers = {"User-Agent": user_agent.generate_user_agent() }

    @classmethod
    def get_availability(cls, park_id, month_date):
        params = {"start_date": formatter.format_date(month_date)}
        LOG.debug(
            "Querying for {} with these params: {}".format(park_id, params)
        )
        url = cls.AVAILABILITY_ENDPOINT.format(park_id=park_id)
        resp = cls._send_request(url, params)
        return resp

    @classmethod
    def get_park_name(cls, park_id):
        resp = cls._send_request(
            cls.MAIN_PAGE_ENDPOINT.format(park_id=park_id), {}
        )
        return resp["campground"]["facility_name"]

    @classmethod
    def _send_request(cls, url, params):
        max_attempts = 15
        resp = requests.get(url, params=params, headers=cls.headers)
        for i in range(0, max_attempts):
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(0.5)
                continue
            else:
                raise RuntimeError(
                    "failedRequest",
                    "ERROR, {status_code} code received from {url}: {resp_text}".format(
                        status_code=resp.status_code, url=url, resp_text=resp.text
                    ),
                )
        raise RuntimeError(
                "failedRequest",
                "ERROR, Failed after {attempts} attempts to retreive {url}".format(
                    attempts=max_attempts, url=url
                ),
        )
