"""Canonical-domain redirect.

Both vanthir.com and www.vanthir.com serve the app; to avoid Google indexing two
copies of every page, www.<canonical> 301-redirects to the apex (https). Only the
www form of CANONICAL_HOST is redirected — the Railway domain, localhost, and the
Railway healthcheck host are left untouched.
"""

from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical = getattr(settings, "CANONICAL_HOST", "")
        self.www = f"www.{self.canonical}" if self.canonical else ""

    def __call__(self, request):
        if self.www and request.get_host().split(":")[0] == self.www:
            return HttpResponsePermanentRedirect(
                f"https://{self.canonical}{request.get_full_path()}"
            )
        return self.get_response(request)
