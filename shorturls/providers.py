import base64
import os
import random

import requests

from domains.models import Domain


class BaseProvider:
    def create_short_url(self, domain: Domain, long_url: str) -> str:
        pass


class MockProvider(BaseProvider):
    def create_short_url(self, domain: Domain, long_url: str) -> str:
        return base64.urlsafe_b64encode(long_url.encode()).decode()[:random.randint(4, 8)]


class FirebaseDynamicLinksProvider(BaseProvider):
    api_key = os.environ.get("FIREBASE_WEB_API_KEY")

    def create_short_url(self, domain: Domain, long_url: str) -> str:
        request_body = {
            'dynamicLinkInfo': {
                'domainUriPrefix': f'https://{domain.name}',
                'link': long_url
            },
            'suffix': {
                'option': 'SHORT'
            }
        }
        response = requests.post('https://firebasedynamiclinks.googleapis.com/v1/shortLinks',
                                 params={'key': self.api_key}, json=request_body)
        return response.json()['shortLink'].split('/')[-1]


PROVIDER_CLASS = FirebaseDynamicLinksProvider