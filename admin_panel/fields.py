import base64
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _get_fernet():
    raw = settings.SECRET_KEY.encode('utf-8')
    key = base64.urlsafe_b64encode(raw[:32].ljust(32, b'\x00'))
    return Fernet(key)


class EncryptedCharField(models.CharField):
    description = "Encrypts data at rest using Fernet symmetric encryption"

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        if not isinstance(value, str) or not value.startswith('gAAAAA'):
            return value
        try:
            f = _get_fernet()
            return f.decrypt(value.encode('utf-8')).decode('utf-8')
        except Exception:
            return value

    def get_prep_value(self, value):
        if value is None or value == '':
            return value
        if isinstance(value, str) and value.startswith('gAAAAA'):
            return value
        f = _get_fernet()
        return f.encrypt(value.encode('utf-8')).decode('utf-8')


class EncryptedTextField(models.TextField):
    description = "Encrypts text data at rest using Fernet symmetric encryption"

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        if not isinstance(value, str) or not value.startswith('gAAAAA'):
            return value
        try:
            f = _get_fernet()
            return f.decrypt(value.encode('utf-8')).decode('utf-8')
        except Exception:
            return value

    def get_prep_value(self, value):
        if value is None or value == '':
            return value
        if isinstance(value, str) and value.startswith('gAAAAA'):
            return value
        f = _get_fernet()
        return f.encrypt(value.encode('utf-8')).decode('utf-8')


class EncryptedEmailField(EncryptedCharField):
    description = "Encrypts email addresses at rest"


def encrypt_value(plaintext):
    """Encrypt a plaintext string using the Fernet key derived from SECRET_KEY."""
    if not plaintext:
        return plaintext
    if isinstance(plaintext, str) and plaintext.startswith('gAAAAA'):
        return plaintext
    return _get_fernet().encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_value(ciphertext):
    """Decrypt a Fernet-encrypted string back to plaintext."""
    if not ciphertext:
        return ciphertext
    if not isinstance(ciphertext, str) or not ciphertext.startswith('gAAAAA'):
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except Exception:
        return ciphertext
