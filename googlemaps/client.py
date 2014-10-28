#
# Copyright 2014 Google Inc. All rights reserved.
#
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
#

"""
Core client functionality, common across all API requests (including performing
HTTP requests).
"""

import base64
from datetime import datetime
from datetime import timedelta
import hashlib
import hmac
import requests
import random
import time

import googlemaps

try: # Python 3
    from urllib.parse import urlencode
except ImportError: # Python 2
    from urllib import urlencode


_VERSION = "0.1"
_USER_AGENT = "GoogleGeoApiClientPython/%s" % _VERSION
_BASE_URL = "https://maps.googleapis.com"

class Client(object):
    """Performs requests to the Google Maps API web services."""

    def __init__(self, key=None, client_id=None, client_secret=None,
                 timeout=None, connect_timeout=None, read_timeout=None,
                 retry_timeout=60):
        """
        :param key: Maps API key. Required, unless "client_id" and
            "client_secret" are set.
        :type key: string

        :param timeout: Combined connect and read timeout for HTTP requests, in
            seconds. Specify "None" for no timeout.
        :type timeout: int

        :param connect_timeout: Connection timeout for HTTP requests, in
            seconds. You should specify read_timeout in addition to this option.
            Note that this requires requests >= 2.4.0.
        :type connect_timeout: int

        :param read_timeout: Read timeout for HTTP requests, in
            seconds. You should specify connect_timeout in addition to this
            option. Note that this requires requests >= 2.4.0.
        :type read_timeout: int

        :param retry_timeout: Timeout across multiple retriable requests, in
            seconds.
        :type retry_timeout: int

        :param client_id: (for Maps API for Work customers) Your client ID.
        :type client_id: string

        :param client_secret: (for Maps API for Work customers) Your client
            secret (base64 encoded).
        :type client_secret: string

        :raises ValueError: when either credentials are missing, incomplete
            or invalid.
        :raises NotImplementedError: if connect_timeout and read_timeout are
            used with a version of requests prior to 2.4.0.
        """
        if not key and not (client_secret and client_id):
            raise ValueError("Must provide API key or enterprise credentials "
                             "when creating client.")

        if key and not key.startswith("AIza"):
            raise ValueError("Invalid API key provided.")

        self.key = key

        if timeout and (connect_timeout or read_timeout):
            raise ValueError("Specify either timeout, or connect_timeout " +
                             "and read_timeout")

        if connect_timeout and read_timeout:
            # Check that the version of requests is >= 2.4.0
            chunks = requests.__version__.split(".")
            if chunks[0] < 2 or (chunks[0] == 2 and chunks[1] < 4):
                raise NotImplementedError("Connect/Read timeouts require "
                                          "requests v2.4.0 or higher")
            self.timeout = (connect_timeout, read_timeout)
        else:
            self.timeout = timeout

        self.client_id = client_id
        self.client_secret = client_secret
        self.retry_timeout = timedelta(seconds=retry_timeout)

    def get(self, url, params, first_request_time=None, retry_counter=0):
        """Performs HTTP GET request with credentials, returning the body as
        JSON.

        :param url: URL path for the request
        :type url: string
        :param params: HTTP GET parameters
        :type params: dict
        :param first_request_time: The time of the first request (None if no retries
                have occurred).
        :type first_request_time: datetime.datetime
        :param retry_counter: The number of this retry, or zero for first attempt.
        :type retry_counter: int

        :raises ApiException: when the API returns an error.
        """

        if not first_request_time:
            first_request_time = datetime.now()

        if retry_counter > 0:
            # 0.5 * (1.5 ^ i) is an increased sleep time of 1.5x per iteration,
            # starting at 0.5s when retry_counter=0. The first retry will occur
            # at 1, so subtract that first.
            delay_seconds = 0.5 * 1.5 ** (retry_counter - 1)

            # Jitter this value by 50% and pause.
            time.sleep(delay_seconds * (random.random() + 0.5))

        resp = requests.get(
            _BASE_URL + self._generate_auth_url(url, params),
            headers={"User-Agent": _USER_AGENT},
            timeout=self.timeout,
            verify=True) # NOTE(cbro): verify SSL certs.

        elapsed = datetime.now() - first_request_time
        exceeded_timeout = elapsed > self.retry_timeout

        if resp.status_code in [500, 503, 504] and not exceeded_timeout:
            # Retry request.
            return self.get(url, params, first_request_time, retry_counter + 1)

        if resp.status_code != 200:
            resp.raise_for_status() # raises a requests.exceptions.HTTPError

        body = resp.json()

        api_status = body["status"]
        if api_status == "OK" or api_status == "ZERO_RESULTS":
            return body

        if api_status == "OVER_QUERY_LIMIT" and not exceeded_timeout:
            # Retry request.
            return self.get(url, params, first_request_time, retry_counter + 1)

        if "error_message" in body:
            raise googlemaps.ApiError(api_status, body["error_message"])
        else:
            raise googlemaps.ApiError(api_status)

    def _generate_auth_url(self, path, params):
        """Returns the path and query string portion of the request URL, first
        adding any necessary parameters.
        :param path: The path portion of the URL.
        :type path: string
        :param params: URL parameters.
        :type params: dict
        :rtype: string
        """
        if self.key:
            params["key"] = self.key
            return path + "?" + urlencode(params)

        if self.client_id and self.client_secret:
            params["client"] = self.client_id

            # Sort params - signature changes depending on the order.
            path = "?".join([path, urlencode(sort_params(params))])
            sig = sign_hmac(self.client_secret, path)
            return path + "&signature=" + sig

from googlemaps.directions import directions
from googlemaps.distance_matrix import distance_matrix
from googlemaps.elevation import elevation
from googlemaps.elevation import elevation_along_path
from googlemaps.geocoding import geocode
from googlemaps.geocoding import reverse_geocode
from googlemaps.timezone import timezone

Client.directions = directions
Client.distance_matrix = distance_matrix
Client.elevation = elevation
Client.elevation_along_path = elevation_along_path
Client.geocode = geocode
Client.reverse_geocode = reverse_geocode
Client.timezone = timezone

def sign_hmac(secret, payload):
    """Returns a basee64-encoded HMAC-SHA1 signature of a given string.
    :param secret: The key used for the signature, base64 encoded.
    :type secret: string
    :param s: The string.
    :type s: string
    :rtype: string
    """
    # Encode/decode from UTF-8. In Python 3, this converts to bytes and back;
    # in Python 2, it is a no-op.
    payload = payload.encode('utf-8')
    sig = hmac.new(base64.urlsafe_b64decode(secret), payload, hashlib.sha1)
    out = base64.urlsafe_b64encode(sig.digest())
    return out.decode('utf-8')

def sort_params(params):
    """Sorts a params dict into a list of tuples, sorted by their keys."""

    return sorted(params.items(), key=lambda t: t[0])
