# TinyBack - A tiny web scraper
# Copyright (C) 2012-2013 David Triendl
# Copyright (C) 2012 Sven Slootweg
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import HTMLParser
import abc
import httplib
import json
import platform
import re
import socket
import urllib
import urlparse
import socket

import tinyback
from tinyback import exceptions

class Service:
    """
    URL shortener client
    """

    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def charset(self):
        """
        Return characters used in shorturls

        Returns a string containing all characters that may appear in a
        (shorturl) code.
        """

    @property
    def rate_limit(self):
        """
        Returns a tuple specifiyng the rate-limit, or None.

        Returns a two-element tuple, with the first element being the number of
        requests that are allowed in the timespan denoted by the second element
        (in seconds). When there is no rate-limit, simply returns None.
        """
        return (2, 1)

    @abc.abstractmethod
    def fetch(self, code):
        """
        Return long URL for given code

        Fetches the long URL for the given shortcode from the URL shortener and
        returns the URL or throws various exceptions when something went wrong.
        The long URL is usually a bytestring.
        """

class HTTPService(Service):
    """
    Httplib-based URL shortener client

    Abstract serivce class to help with using httplib.
    """

    @abc.abstractproperty
    def url(self):
        """
        Returns the base URL of the URL shortener
        """

    @property
    def http_headers(self):
        """
        Dictionary of additional HTTP headers to send with every request.
        """
        return {}

    @property
    def http_keepalive(self):
        """
        Whether to use HTTP persistent connections or not. If set to false, the
        connection will be forcibly closed after each request
        """
        return True

    def __init__(self):
        parsed_url = urlparse.urlparse(self.url)
        self._path = parsed_url.path or "/"

        if parsed_url.scheme == "http":
            klass = httplib.HTTPConnection
        elif parsed_url.scheme == "https":
            klass = httplib.HTTPSConnection
        else:
            raise ValueError("Unknown scheme %s" % parsed_url.scheme)

        pos = parsed_url.netloc.find(':');
        if pos != -1:
            self._hostname = parsed_url.netloc[0:pos]
            self._port = parsed_url.netloc[pos+1:]
        else:
            self._hostname = parsed_url.netloc
            self._port = None
        addr = [addrinfo for addrinfo in socket.getaddrinfo(self._hostname, self._port or 80)
                if (addrinfo[0] == socket.AF_INET or addrinfo[0] == socket.AF_INET6) and
                   isinstance(addrinfo[4][0], basestring)]
        if not len(addr):
            raise ValueError("Unknown host %s" % parsed_url.netloc)
        self._host = addr[0][4][0]

        version = platform.python_version_tuple()
        if int(version[0]) == 2 and int(version[1]) <= 5:
            self._conn = klass(self._host)
        else:
            self._conn = klass(self._host, timeout=30)

    def _http_head(self, code):
        return self._http_fetch(code, "HEAD")[0]

    def _http_get(self, code):
        return self._http_fetch(code, "GET")

    def _http_fetch(self, code, method):
        headers = self.http_headers
        if self.http_keepalive:
            headers["Connection"] = "Keep-Alive"
        else:
            headers["Connection"] = "close"
        headers["Host"] = self._hostname

        try:
            self._conn.request(method, self._path + code, headers=headers)
            resp = self._conn.getresponse()
            result = (resp, resp.read())
            if not self.http_keepalive:
                self._conn.close()
            return result
        except httplib.HTTPException, e:
            self._conn.close()
            raise exceptions.ServiceException("HTTP exception: %s" % e)
        except socket.error, e:
            self._conn.close()
            raise exceptions.ServiceException("Socket error: %s" % e)

class SimpleService(HTTPService):
    """
    Simple HTTP URL shortener client

    This is a generic service for URL shorteners. It is possible to specify
    which HTTP status code corresponds to which result, but it is not required.
    """

    @property
    def http_status_redirect(self):
        """
        HTTP status codes that indicate proper redirect
        """
        return [301, 302]

    @property
    def http_status_no_redirect(self):
        """
        HTTP status codes that indicate no redirect
        """
        return [404]

    @property
    def http_status_code_blocked(self):
        """
        HTTP status code that indicates the code/long URL was blocked
        """
        return [410]

    @property
    def http_status_blocked(self):
        """
        HTTP status code that indicates that the service is blocking us
        """
        return [403, 420, 429]

    def fetch(self, code):
        resp = self._http_head(code)

        if resp.status in self.http_status_redirect:
            location = resp.getheader("Location")
            if not location:
                raise exceptions.ServiceException("No Location header after HTTP status 301")
            return location
        elif resp.status in self.http_status_no_redirect:
            raise exceptions.NoRedirectException()
        elif resp.status in self.http_status_code_blocked:
            raise exceptions.CodeBlockedException()
        elif resp.status in self.http_status_blocked:
            raise exceptions.BlockedException()
        else:
            return self.unexpected_http_status(code, resp)

    def unexpected_http_status(self, code, resp):
        raise exceptions.ServiceException("Unexpected HTTP status %i" % resp.status)

class YourlsService(Service):
    """
    A service for installations of Yourls (http://yourls.org).
    """

    @abc.abstractproperty
    def yourls_api_url(self):
        """
        The endpoint of the Yourls API.

        The Yourls API is typically located at /yourls-api.php
        """

    @abc.abstractproperty
    def yourls_url_convert(self):
        """
        The value of the YOURLS_URL_CONVERT parameter.

        The YOUR_SULR_CONVERT parameter specifies what charset is used by the
        Yourls installation.
        """

    @property
    def charset(self):
        if self.yourls_url_convert == 36:
            return "0123456789abcdefghijklmnopqrstuvwxyz"
        elif self.yourls_url_convert == 62:
            return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        raise RuntimeError("Bad value for yourls_url_convert parameter")

    def __init__(self):
        parsed_url = urlparse.urlparse(self.yourls_api_url)
        self._path = parsed_url.path or "/"

        if parsed_url.scheme == "http":
            klass = httplib.HTTPConnection
        elif parsed_url.scheme == "https":
            klass = httplib.HTTPSConnection
        else:
            raise ValueError("Unknown scheme %s" % parsed_url.scheme)

        version = platform.python_version_tuple()
        if int(version[0]) == 2 and int(version[1]) <= 5:
            self._conn = klass(parsed_url.netloc)
        else:
            self._conn = klass(parsed_url.netloc, timeout=30)

    def fetch(self, code):
        params = {"action": "expand", "shorturl": code, "format": "simple"}
        try:
            self._conn.request("GET", self._path + "?" + urllib.urlencode(params))
            resp = self._conn.getresponse()
            data = resp.read()
        except httplib.HTTPException, e:
            self._conn.close()
            raise exceptions.ServiceException("HTTP exception: %s" % e)
        except socket.error, e:
            self._conn.close()
            raise exceptions.ServiceException("Socket error: %s" % e)

        if resp.status == 200:
            if data == "not found":
                raise exceptions.NoRedirectException()
            return data
        raise exceptions.ServiceException("Unexpected HTTP status %i" % resp.status)

class Bitly(HTTPService):
    """
    http://bit.ly/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"

    @property
    def url(self):
        return "http://bit.ly/"

    def fetch(self, code):
        resp = self._http_head(code)

        if resp.status == 301:
            location = resp.getheader("Location")
            if not location:
                raise exceptions.ServiceException("No Location header after HTTP status 301")
            if resp.reason == "Moved":  # Normal bit.ly redirect
                return location
            elif resp.reason == "Moved Permanently":
                # Weird "bundles" redirect, forces connection close despite
                # sending Keep-Alive header
                self._conn.close()
                raise exceptions.CodeBlockedException()
            else:
                raise exceptions.ServiceException("Unknown HTTP reason %s after HTTP status 301" % resp.reason)
        elif resp.status == 302:
            location = resp.getheader("Location")
            if not location:
                raise exceptions.ServiceException("No Location header after HTTP status 302")
            return self._parse_warning_url(code, location)
        elif resp.status == 403:
            raise exceptions.BlockedException()
        elif resp.status == 404:
            raise exceptions.NoRedirectException()
        elif resp.status == 410:
            raise exceptions.CodeBlockedException()
        else:
            raise exceptions.ServiceException("Unknown HTTP status %i" % resp.status)

    def _parse_warning_url(self, code, url):
        url = urlparse.urlparse(url)
        if url.scheme != "http" or url.netloc != "bit.ly" or url.path != "/a/warning":
            raise exceptions.ServiceException("Unexpected Location header after HTTP status 302")
        query = urlparse.parse_qs(url.query)
        if not ("url" in query and len(query["url"]) == 1) or not ("hash" in query and len(query["hash"]) == 1):
            raise exceptions.ServiceException("Unexpected Location header after HTTP status 302")
        if query["hash"][0] != code:
            raise exceptions.ServiceException("Hash mismatch for HTTP status 302")
        return query["url"][0]

class Isgd(SimpleService):
    """
    http://is.gd/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"

    @property
    def rate_limit(self):
        return (60, 60)

    @property
    def url(self):
        return "http://is.gd/"

    @property
    def http_status_code_blocked(self):
        """
        HTTP status code that indicates the code/long URL was blocked
        """
        return [502]

    def unexpected_http_status(self, code, resp):
        if resp.status != 200:
            return super(Isgd, self).unexpected_http_status(code, resp)

        resp, data = self._http_get(code)
        if resp.status != 200:
            raise exceptions.ServiceException("HTTP status changed from 200 to %i on second request" % resp.status)

        if not data:
            raise exceptions.CodeBlockedException("Empty response on status 200")
        if "<div id=\"main\"><p>Rate limit exceeded - please wait 1 minute before accessing more shortened URLs</p></div>" in data:
            raise exceptions.BlockedException()
        if "<div id=\"disabled\"><h2>Link Disabled</h2>" in data:
            return self._parse_blocked(code, data)
        if "<p>The full original link is shown below. <b>Click the link</b> if you'd like to proceed to the destination shown:" in data:
            return self._parse_preview(code, data)

    def _parse_blocked(self, code, data):
        match = re.search("<p>For reference and to help those fighting spam the original destination of this URL is given below \(we strongly recommend you don't visit it since it may damage your PC\): -<br />(.*)</p><h2>is\.gd</h2><p>is\.gd is a free service used to shorten long URLs\.", data)
        if not match:
            raise exceptions.ServiceException("Could not find target URL in 'Link Disabled' page")

        url = match.group(1).decode("utf-8")
        url = HTMLParser.HTMLParser().unescape(url).encode("utf-8")
        if url == "":
            raise exceptions.CodeBlockedException("Empty URL on preview")
        return url

    def _parse_preview(self, code, data):
        match = re.search("<b>Click the link</b> if you'd like to proceed to the destination shown: -<br /><a href=\"(.*)\" class=\"biglink\">", data)
        if not match:
            raise exceptions.ServiceException("Could not find target URL in 'Preview' page")

        url = match.group(1).decode("utf-8")
        return HTMLParser.HTMLParser().unescape(url).encode("utf-8")


class Owly(SimpleService):
    """
    http://ow.ly/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    @property
    def url(self):
        return "http://ow.ly/"

    def unexpected_http_status(self, code, resp):
        if resp.status != 200:
            return super(Owly, self).unexpected_http_status(code, resp)

        resp, data = self._http_get(code)
        if resp.status != 200:
            raise exceptions.ServiceException("HTTP status changed from 200 to %i on second request" % resp.status)

        match = re.search("<a class=\"btn ignore\" href=\"(.*?)\" title=", data)
        if not match:
            raise exceptions.ServiceException("Could not find target URL in safety warning")

        url = match.group(1).decode("utf-8")
        return HTMLParser.HTMLParser().unescape(url).encode("utf-8")


class Tinyurl(HTTPService):
    """
    http://tinyurl.com/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz"

    @property
    def url(self):
        return "http://tinyurl.com/"

    def fetch(self, code):
        resp = self._http_head(code)

        if resp.status == 200:
            return self._fetch_200(code)
        elif resp.status == 301:
            location = resp.getheader("Location")
            if not location:
                raise exceptions.CodeBlockedException("No Location header after HTTP status 301")
            tiny = resp.getheader("X-tiny")
            if tiny and tiny[:3] == "aff":
                return self._preview(code, location)
            return location
        elif resp.status == 302:
            raise exceptions.CodeBlockedException()
        elif resp.status == 404:
            raise exceptions.NoRedirectException()
        elif resp.status == 500:
            # Some "errorhelp" URLs result in HTTP status 500, which goes away when trying a different server
            self._conn.close()
            raise exceptions.ServiceException("HTTP status 500")
        else:
            raise exceptions.ServiceException("Unknown HTTP status %i" % resp.status)

        return resp.status

    def _fetch_200(self, code):
        resp, data = self._http_get(code)

        if resp.status != 200:
            raise exceptions.ServiceException("HTTP status changed from 200 to %i on second request" % resp.status)

        if "<title>Redirecting...</title>" in data:
            return self._parse_errorhelp(code, data)
        elif "Error: TinyURL redirects to a TinyURL." in data:
            return self._parse_tinyurl_redirect(data)
        else:
            raise exceptions.ServiceException("Unexpected response on status 200")

    def _parse_errorhelp(self, code, data):
        match = re.search('<meta http-equiv="refresh" content="0;url=(.*?)">', data)
        if not match:
            raise exceptions.ServiceException("No redirect on \"errorhelp\" page on HTTP status 200")
        url = urlparse.urlparse(match.group(1))
        if url.scheme != "http" or url.netloc != "tinyurl.com" or url.path != "/errorb.php":
            raise exceptions.ServiceException("Unexpected redirect on \"errorhelp\" page  on HTTP status 200")
        query = urlparse.parse_qs(url.query)
        if not ("url" in query and len(query["url"]) == 1) or not ("path" in query and len(query["path"]) == 1):
            raise exceptions.ServiceException("Unexpected redirect on \"errorhelp\" page  on HTTP status 200")
        if query["path"][0] != ("/" + code):
            raise exceptions.ServiceException("Code mismatch on \"errorhelp\" on HTTP status 200")

        return query["url"][0]

    def _parse_tinyurl_redirect(self, data):
        match = re.search("<p class=\"intro\">The URL you followed redirects back to a TinyURL and therefore we can't directly send you to the site\\. The URL it redirects to is <a href=\"(.*?)\">", data, re.DOTALL)
        if not match:
            raise exceptions.ServiceException("No redirect on \"tinyurl redirect\" page on HTTP status 200")

        url = match.group(1).decode("utf-8")
        return HTMLParser.HTMLParser().unescape(url).encode("utf-8")

    def _preview(self, code, affiliate_url):
        resp, data = self._http_get("preview.php?num=" + code)

        if resp.status != 200:
            raise exceptions.ServiceException("Unexpected HTTP status %i on preview page" % resp.status)

        match = re.search("<a id=\"redirecturl\" href=\"(.*?)\">Proceed to this site.</a>", data, re.DOTALL)
        if not match:
            raise exceptions.ServiceException("No redirect on preview page")

        url = match.group(1).decode("utf-8")
        if url == "":
            return self._scrub_url(code, affiliate_url)
        return HTMLParser.HTMLParser().unescape(url).encode("utf-8")

    def _scrub_url(self, code, url):
        parsed_url = urlparse.urlparse(url)
        if parsed_url.hostname == "redirect.tinyurl.com" and parsed_url.path == "/api/click":
            query = urlparse.parse_qs(parsed_url.query)
            if query["out"]:
                return query["out"][0]

        return url

class Ur1ca(SimpleService):
    """
    http://ur1.ca/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz"

    @property
    def rate_limit(self):
        return None

    @property
    def url(self):
        return "http://ur1.ca/"

    @property
    def http_status_no_redirect(self):
        return [200]

class Snipurl(SimpleService):
    """
    http://snipurl.com
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz-_~"

    @property
    def url(self):
        return "http://snipurl.com"

    @property
    def http_status_no_redirect(self):
        return [410]

    @property
    def http_keepalive(self):
        return False

    def fetch(self, code):
        location = super(Snipurl, self).fetch(code)
        try:
            if location.decode("ascii") == "/site/getprivate?snip=" + code:
                raise exceptions.CodeBlockedException("Private key required")
        except UnicodeDecodeError:
            pass
        return location

    def unexpected_http_status(self, code, resp):
        if resp.status != 500:
            return super(Snipurl, self).unexpected_http_status(code, resp)

        resp, data = self._http_get(code)
        if resp.status != 500:
            raise exceptions.ServiceException("HTTP status changed from 500 to %i on second request" % resp.status)

        match = re.search("<p>You clicked on a snipped URL, which will take you to the following looong URL: </p> <div class=\"quote\"><span class=\"quotet\"></span><br/>(.*?)</div> <br />", data)
        if not match:
            raise exceptions.ServiceException("Could not find target URL on preview page")

        url = match.group(1).decode("utf-8")
        return HTMLParser.HTMLParser().unescape(url).encode("utf-8")

class Googl(Service):
    """
    http://goo.gl/
    """

    @property
    def rate_limit(self):
        return (1, 5)

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def __init__(self):
        host = "www.googleapis.com"

        version = platform.python_version_tuple()
        if int(version[0]) == 2 and int(version[1]) <= 5:
            self._conn = httplib.HTTPSConnection(host)
        else:
            self._conn = httplib.HTTPSConnection(host, timeout=30)

    def fetch(self, code):
        try:
            self._conn.request("GET", "/urlshortener/v1/url?shortUrl=http://goo.gl/%s" % code)
            resp = self._conn.getresponse()
            data = resp.read()
        except httplib.HTTPException, e:
            self._conn.close()
            raise exceptions.ServiceException("HTTP exception: %s" % e)
        except socket.error, e:
            self._conn.close()
            raise exceptions.ServiceException("Socket error: %s" % e)

        if resp.status == 200:
            return self._parse_json(data)
        elif resp.status == 403:
            raise exceptions.BlockedException()
        elif resp.status == 404:
            raise exceptions.NoRedirectException()
        else:
            raise exceptions.ServiceException("Unexpected HTTP status %i" % resp.status)

    def _parse_json(self, data):
        try:
            data = json.loads(data)
        except ValueError:
            raise exceptions.ServiceException("Could not decode response")

        if not "kind" in data or data["kind"] != "urlshortener#url":
            raise exceptions.ServiceException("No/bad type given")
        if not "status" in data:
            raise exceptions.ServiceException("No status given")
        if not "longUrl" in data:
            raise exceptions.CodeBlockedException("Status: %s" % data["status"])
        return data["longUrl"]

class Trimnew(SimpleService):
    """
    http://tr.im/
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz"

    @property
    def url(self):
        return "http://tr.im/"

    @property
    def http_status_redirect(self):
        return [301]

    @property
    def http_status_no_redirect(self):
        return []

    @property
    def http_status_code_blocked(self):
        return []

    @property
    def http_status_blocked(self):
        return [404]

    def fetch(self, code):
        if code == "500":
            raise exceptions.CodeBlockedException()

        url = super(Trimnew, self).fetch(code)
        if url == "http://tr.im/404":
            raise exceptions.NoRedirectException()
        return url

class Postly(SimpleService):

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    @property
    def rate_limit(self):
        return None

    @property
    def url(self):
        return "https://post.ly/"

    @property
    def http_status_redirect(self):
        return [301]

    @property
    def http_status_no_redirect(self):
        return [302]

class Wpme(SimpleService):
    """ Wordpress.com's shortener wp.me. """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"

    @property
    def url(self):
        return "http://wp.me/"


class BaseVisibliService(SimpleService):
    @property
    def http_status_redirect(self):
        return [301]

    @property
    def http_status_no_redirect(self):
        return []  # see unexpected_http_status

    @property
    def rate_limit(self):
        return (1, 5)

    @property
    def http_headers(self):
        return {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.8.3) "
            "Gecko/20120431 Firefox/18.0"}

    def unexpected_http_status(self, code, resp):
        if resp.status == 302:
            location = resp.getheader("Location")

            if location and ("sharedby" in location or "visibli" in location):
                raise exceptions.NoRedirectException()
            elif location and location.startswith("http://yahoo.com"):
                raise exceptions.BlockedException("Banned (location=%s)" % location)

            # Guess it be an override for site that busts out iframes
            return location

        if resp.status != 200:
            return super(BaseVisbliService, self).unexpected_http_status(code, resp)

        resp, data = self._http_get(code)
        if resp.status != 200:
            raise exceptions.ServiceException("HTTP status changed from 200 to %i on second request" % resp.status)

        match = re.search(r'<iframe id="[^"]+" src="([^"]+)">', data)
        if not match:
            if 'Undefined index:  HTTP_USER_AGENT' in data:
                raise exceptions.ServiceException("Website broken about user-agent")

            raise exceptions.ServiceException("No iframe url found")

        url = match.group(1).decode("utf-8")
        url = HTMLParser.HTMLParser().unescape(url).encode("utf-8")
        return url


class VisibliHex(BaseVisibliService):
    """Visibli's old share shortener

    It uses urls like http://links.visibli.com/links/fbc5fa

    Note: This service is obsolete and it should not need to be run as
    it is being grabbed without tinyback. See the wiki.
    """

    @property
    def charset(self):
        return "0123456789abcdef"

    @property
    def url(self):
        return "http://links.sharedby.co/links/"


class Visibli(BaseVisibliService):
    """Visibli's (now SharedBy) new shortener

    It uses urls like:

    * http://links.visibli.com/share/AHbpFG
    * http://vsb.li/AHbpFG
    * http://links.sharedby.co/share/AHbpFG
    * http://sharedby.co/AHbpFG
    * http://archive_team_and_urlteam_is_the_best.sharedby.co/AHbpFG
    * http://shrd.by/AHbpFG
    """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    @property
    def url(self):
        return "http://sharedby.co/"


class Vbly(YourlsService):

    @property
    def yourls_api_url(self):
        return "http://vbly.us/yourls-api.php"

    @property
    def yourls_url_convert(self):
        return 36


class Arsehat(YourlsService):

    @property
    def yourls_api_url(self):
        return "http://arseh.at/api.php"

    @property
    def yourls_url_convert(self):
        return 36

class Pixorial(SimpleService):
    """ """

    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz"

    @property
    def url(self):
        return "http://myhub.pixorial.com/s/"

    @property
    def rate_limit(self):
        """
        Returns a tuple specifiyng the rate-limit, or None.

        Returns a two-element tuple, with the first element being the number of
        requests that are allowed in the timespan denoted by the second element
        (in seconds). When there is no rate-limit, simply returns None.
        """
        return (20, 1)

    @property
    def http_keepalive(self):
        """
        Whether to use HTTP persistent connections or not. If set to false, the
        connection will be forcibly closed after each request
        """
        return False

    def fetch(self, code):
        resp = self._http_head(code)

        if resp.status in self.http_status_redirect:
            location = resp.getheader("Location")
            if location == "http://myhub.pixorial.com/":
                raise exceptions.NoRedirectException("Redirected to home page")
            if not location:
                raise exceptions.ServiceException("No Location header after HTTP status 301")
            return location
        elif resp.status in self.http_status_no_redirect:
            raise exceptions.NoRedirectException()
        elif resp.status in self.http_status_code_blocked:
            raise exceptions.CodeBlockedException()
        elif resp.status in self.http_status_blocked:
            raise exceptions.BlockedException()
        else:
            return self.unexpected_http_status(code, resp)

class Twitter(SimpleService):
    """ Twitter changes all urls in their short messages to use this shortener. """
    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    @property
    def url(self):
        return "http://t.co/"
    @property
    def rate_limit(self):
        return (20, 1)
    @property
    def http_keepalive(self):
        return False

class Trim(SimpleService):
    """ Twitter changes all urls in their short messages to use this shortener. """
    @property
    def charset(self):
        return "0123456789abcdefghijklmnopqrstuvwxyz"
    @property
    def url(self):
        return "http://tr.im/"
    @property
    def rate_limit(self):
        return (20, 1)
    @property
    def http_keepalive(self):
        return True
    def fetch(self, code):
        resp = self._http_head(code)

        if resp.status in self.http_status_redirect:
            location = resp.getheader("Location")
            if code != "404" and location == "http://tr.im/404":
                raise exceptions.NoRedirectException("Redirected to 404 page")
            if not location:
                raise exceptions.ServiceException("No Location header after HTTP status 301")
            return location
        elif resp.status in self.http_status_no_redirect:
            raise exceptions.NoRedirectException()
        elif resp.status in self.http_status_code_blocked:
            raise exceptions.CodeBlockedException()
        elif resp.status in self.http_status_blocked:
            raise exceptions.BlockedException()
        else:
            return self.unexpected_http_status(code, resp)


_factory_map = {
    "bitly": Bitly,
    "isgd": Isgd,
    "owly": Owly,
    "tinyurl": Tinyurl,
    "ur1ca": Ur1ca,
    "snipurl": Snipurl,
    "googl": Googl,
    "trimnew": Trimnew,
    "postly": Postly,
    "wpme": Wpme,
    "visiblihex": VisibliHex,
    "visibli": Visibli,
    "vbly": Vbly,
    "arsehat": Arsehat,
    "pixorial": Pixorial,
    "twitter": Twitter,
    "trim": Trim,
}


def factory(name):
    service = _factory_map.get(name)
    if not service:
        raise ValueError("Unknown service %s" % name)
    else:
        return service()
