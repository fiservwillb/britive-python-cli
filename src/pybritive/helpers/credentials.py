import random
import base64
import hashlib
import time
import webbrowser
import requests
from requests.adapters import HTTPAdapter, Retry
from pathlib import Path
import click
import configparser
import json
import os
from .encryption import StringEncryption, InvalidPassphraseException
from britive.britive import Britive
from dateutil import parser
import jwt


interactive_login_fields_to_pop = [
    'challengeParameters',
    'success',
    'type',
    'user',
    'userId',
    'username',
    'refreshToken',
    'authTime',
    'maxSessionTimeout'
]


# the credentials should expire sooner than the true expiration date
# in case we need to do things like polling for credentials during
# an approval process
credential_expiration_safe_zone_minutes = 10
federation_provider_default_expiration_seconds = 900


class InteractiveLoginTimeout(Exception):
    pass


def b64_encode_url_safe(value: bytes):
    return base64.urlsafe_b64encode(value).decode('utf-8').replace('=', '')


# this base class expects self.credentials to be a dict - so sub classes need to convert to dict
class CredentialManager:
    def __init__(self, tenant_name: str, tenant_alias: str, cli: any, federation_provider: str = None):
        self.cli = cli
        self.tenant = tenant_name
        self.alias = tenant_alias
        self.base_url = f'https://{Britive.parse_tenant(tenant_name)}'
        self.federation_provider = federation_provider
        self.session = None

        # not sure if we really need 32 random bytes or if any random string would work
        # but the current britive-cli in node.js does it this way so it will be done the same
        # way in python
        self.verifier = b64_encode_url_safe(bytes([random.getrandbits(8) for _ in range(0, 32)]))
        self.auth_token = b64_encode_url_safe(bytes(hashlib.sha512(self.verifier.encode('utf-8')).digest()))
        self.credentials = self.load() or {}

    def _setup_requests_session(self):
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

        # allow the disabling of TLS/SSL verification for testing in development (mostly local development)
        if os.getenv('BRITIVE_NO_VERIFY_SSL') and '.dev.' in self.tenant:
            # turn off ssl verification
            self.session.verify = False
            # wipe these due to this bug: https://github.com/psf/requests/issues/3829
            os.environ['CURL_CA_BUNDLE'] = ""
            os.environ['REQUESTS_CA_BUNDLE'] = ""
            # disable the warning message
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def perform_interactive_login(self):
        self.cli.print(f'Performing interactive login against tenant {self.tenant}.')
        sso_idp = self.cli.config.get_tenant().get('sso_idp')
        if sso_idp:
            sso_idp = sso_idp.replace('saml', 'SAML')  # ui is expecting it in caps
            url = f'{self.base_url}/sso?idp={sso_idp}&token={self.auth_token}'
        else:
            url = f'{self.base_url}/login?token={self.auth_token}'

        # establish a requests session which will be used in retrieve_tokens()
        self._setup_requests_session()

        try:
            webbrowser.get()
            webbrowser.open(url)
        except webbrowser.Error:
            self.cli.print(
                'No web browser found. Please manually navigate to the link below and authenticate.'
            )
            self.cli.print(url)

        time.sleep(3)
        num_tries = 1
        while True:
            if num_tries > 60:
                raise InteractiveLoginTimeout()
            response = self.retrieve_tokens()

            if response.status_code >= 400:
                time.sleep(2)
                num_tries += 1
            else:
                credentials = response.json()['authenticationResult']

                # calculate a safe expiration time
                auth_time = int(credentials.get('authTime', 0))
                session_time = int(credentials.get('maxSessionTimeout', 0))
                creds_expire_after = auth_time + session_time - (credential_expiration_safe_zone_minutes * 60 * 1000)
                credentials['safeExpirationTime'] = creds_expire_after

                # drop a bunch of unnecessary fields
                for field in interactive_login_fields_to_pop:
                    credentials.pop(field, None)

                self.save(credentials)
                self.cli.print(f'Authenticated to tenant {self.tenant} via interactive login.')
                break

    def perform_federation_provider_authentication(self):
        self.cli.print(f'Performing {self.federation_provider} federation provider authentication '
                       f'against tenant {self.tenant}.')

        # we need to extract the duration, if provided
        # field format is provider-[something provider specific]_[duration in seconds]
        helper = self.federation_provider.split('_')
        duration = federation_provider_default_expiration_seconds
        if len(helper) == 2:
            try:
                duration = int(helper[1])
            except ValueError:
                self.cli.print(f'Invalid federation provider duration {helper[1]} provided - defaulting '
                               f'to {duration} seconds.')

        # generate the token
        generated_token = Britive.source_federation_token_from(
            provider=helper[0],
            tenant=self.tenant,
            duration_seconds=duration
        )

        # obtain the provider and expiration time of the token
        provider, token = generated_token.split('::')
        provider = provider.lower()
        token = str(token)

        expiration_time = (int(time.time()) + federation_provider_default_expiration_seconds) * 1000

        try:
            if provider == 'aws':
                token = base64.b64decode(token.encode('utf-8'))
                token_expires = json.loads(token)['iam_request_headers']['x-britive-expires']
                expiration_time = int(parser.parse(token_expires).timestamp() * 1000)
            if provider == 'oidc':
                expiration_time = jwt.decode(
                    token,
                    # validation of the token will occur on the Britive backend
                    # so not verifying everything here is okay since we are just
                    # trying to extract the token expiration time so we can store
                    # it in the ~/.britive/pybritive.credentials[.encrypted] file
                    options={
                        'verify_signature': False,
                        'verify_aud': False
                    }
                )['exp'] * 1000
        except Exception as e:
            self.cli.print(f'Cannot obtain token expiration time for {self.federation_provider}. Defaulting to '
                           f'{federation_provider_default_expiration_seconds} seconds.')

        # generate the credentials object and save it
        credentials = {
            'accessToken': generated_token,
            'safeExpirationTime': expiration_time
        }

        self.save(credentials)
        self.cli.print(f'Authenticated to tenant {self.tenant} via federation provider authentication.')

    def retrieve_tokens(self):
        url = f'{self.base_url}/api/auth/cli/retrieve-tokens'
        auth_params = {
            'authParameters': {
                'cliToken': self.verifier
            }
        }
        headers = {
            'Content-Type': 'application/json'
        }
        return self.session.post(url, headers=headers, json=auth_params)

    def load(self, full=False):
        # we should NEVER get here but adding here just in case
        raise click.ClickException('Must use a subclass of CredentialManager')

    def save(self, credentials: dict):
        # we should NEVER get here but adding here just in case
        raise click.ClickException('Must use a subclass of CredentialManager')

    def delete(self):
        # we should NEVER get here but adding here just in case
        raise click.ClickException('Must use a subclass of CredentialManager')

    # this helper exists since subclasses may need to override the method due to how the
    # access token may be encrypted and/or stored
    def _get_token(self):
        return self.credentials['accessToken']

    def get_token(self):
        if not self.has_valid_credentials():  # no credentials or expired creds for the tenant so do interactive login

            # both methods below write the credentials out and update self.credentials as needed
            if self.federation_provider:
                self.perform_federation_provider_authentication()
            else:
                self.perform_interactive_login()

        return self._get_token()

    def has_valid_credentials(self):
        if not self.credentials or self.credentials == {}:
            self.cli.print(f'Credentials for tenant {self.tenant} not found.')
            return False
        if int(time.time() * 1000) <= int(self.credentials.get('safeExpirationTime', 0)):
            return True
        self.cli.print(f'Credentials for tenant {self.tenant} have expired.')
        return False


class FileCredentialManager(CredentialManager):
    def __init__(self, tenant_name: str, tenant_alias: str, cli: any, federation_provider: str = None):
        home = os.getenv('PYBRITIVE_HOME_DIR', str(Path.home()))
        self.path = str(Path(home) / '.britive' / 'pybritive.credentials')
        super().__init__(tenant_name, tenant_alias, cli, federation_provider)

    def load(self, full=False):
        path = Path(self.path)
        if not path.is_file():  # credentials file does not yet exist, create it as an empty file
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text('')

        # open the file with configparser
        credentials = configparser.ConfigParser()
        credentials.optionxform = str  # maintain key case
        credentials.read(str(path))
        credentials = json.loads(json.dumps(credentials._sections))  # TODO this is messy but works for now
        if full:
            return credentials
        return credentials.get(self.alias, None)

    def save(self, credentials: dict):
        full_credentials = self.load(full=True)
        if credentials is None:
            full_credentials.pop(self.alias, None)
        else:
            full_credentials[self.alias] = credentials

        config = configparser.ConfigParser()
        config.optionxform = str  # maintain key case
        config.read_dict(full_credentials)

        # write the new credentials file
        with open(str(self.path), 'w') as f:
            config.write(f, space_around_delimiters=False)
        self.credentials = credentials

    def delete(self):
        self.save(None)


class EncryptedFileCredentialManager(CredentialManager):
    def __init__(self, tenant_name: str, tenant_alias: str, cli: any, passphrase: str = None,
                 federation_provider: str = None):
        home = os.getenv('PYBRITIVE_HOME_DIR', str(Path.home()))
        self.path = str(Path(home) / '.britive' / 'pybritive.credentials.encrypted')
        self.passphrase = passphrase
        self.string_encryptor = StringEncryption(passphrase=self.passphrase)
        super().__init__(tenant_name, tenant_alias, cli, federation_provider)

    def decrypt(self, encrypted_access_token: str):
        try:
            return self.string_encryptor.decrypt(ciphertext=encrypted_access_token)
        except InvalidPassphraseException:
            self.cli.print('invalid passphrase provided - wiping credentials and forcing a re-authentication.')
            self.delete()
            self.credentials = self.load() or {}
            return self.get_token()

    def encrypt(self, decrypted_access_token: str):
        return self.string_encryptor.encrypt(plaintext=decrypted_access_token)

    def load(self, full=False):
        path = Path(self.path)
        if not path.is_file():  # credentials file does not yet exist, create it as an empty file
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text('')

        # open the file with configparser
        credentials = configparser.ConfigParser()
        credentials.optionxform = str  # maintain key case
        credentials.read(self.path)
        credentials = json.loads(json.dumps(credentials._sections))  # TODO this is messy but works for now

        if full:
            return credentials
        return credentials.get(self.alias, None)

    def _get_token(self):
        # we should do a just-in-time decryption so unencrypted creds are not stored in memory
        return self.decrypt(self.credentials['accessToken'])

    def save(self, credentials: dict):
        full_credentials = self.load(full=True)
        if credentials is None:
            full_credentials.pop(self.alias, None)
        else:
            credentials['accessToken'] = self.encrypt(credentials['accessToken'])
            full_credentials[self.alias] = credentials
            # effectively a deep copy
            self.credentials = json.loads(json.dumps(credentials))

        config = configparser.ConfigParser()
        config.optionxform = str  # maintain key case
        config.read_dict(full_credentials)

        # write the new credentials file
        with open(str(self.path), 'w') as f:
            config.write(f, space_around_delimiters=False)

    def delete(self):
        self.save(None)
