from authlib.common.encoding import (
    to_bytes, urlsafe_b64encode, json_b64encode
)
from ..rfc7515.util import (
    extract_header,
    extract_segment,
    prepare_algorithm_key,
)
from .errors import (
    DecodeError,
    MissingAlgorithmError,
    UnsupportedAlgorithmError,
    MissingEncryptionAlgorithmError,
    UnsupportedEncryptionAlgorithmError,
    UnsupportedCompressionAlgorithmError,
    InvalidHeaderParameterName,
)


class JWE(object):
    #: Registered Header Parameter Names defined by `Section 4.1`_
    REGISTERED_HEADER_PARAMETER_NAMES = frozenset([
        'alg', 'enc', 'zip',
        'jku', 'jwk', 'kid',
        'x5u', 'x5c', 'x5t', 'x5t#S256',
        'typ', 'cty', 'crit'
    ])

    def __init__(self, algorithms, private_headers=None):
        self._alg_algorithms = {}
        self._enc_algorithms = {}
        self._zip_algorithms = {}
        self._private_headers = private_headers

        for algorithm in algorithms:
            self.register_algorithm(algorithm)

    def register_algorithm(self, algorithm):
        if algorithm.TYPE != 'JWE':
            raise ValueError(
                'Invalid algorithm for JWE, {!r}'.format(algorithm))

        if algorithm.HEADER_KEY == 'alg':
            self._alg_algorithms[algorithm.name] = algorithm
        elif algorithm.HEADER_KEY == 'enc':
            self._enc_algorithms[algorithm.name] = algorithm
        elif algorithm.HEADER_KEY == 'zip':
            self._zip_algorithms[algorithm.name] = algorithm

    def serialize_compact(self, protected, msg, key):
        self._validate_header(protected)
        algorithm, enc_alg, key = self._prepare_alg_enc_key(protected, key)

        # step 1: Encoding JWE Protected Header
        protected_segment = json_b64encode(protected)

        # step 2: Generate a random Content Encryption Key (CEK)
        cek = enc_alg.generate_cek()

        # step 3: Encrypt the CEK with the recipient's public key
        ek = algorithm.wrap(cek, protected, key)

        # step 4: Generate a random JWE Initialization Vector
        iv = enc_alg.generate_iv()

        # step 5: Let the Additional Authenticated Data encryption parameter
        # be ASCII(BASE64URL(UTF8(JWE Protected Header)))
        aad = to_bytes(protected_segment, 'ascii')

        # step 6: perform encryption
        msg = self._compress_text(msg, protected)
        ciphertext, tag = enc_alg.encrypt(self, msg, aad, iv, cek)
        return b'.'.join([
            protected_segment,
            urlsafe_b64encode(ek),
            urlsafe_b64encode(iv),
            urlsafe_b64encode(ciphertext),
            urlsafe_b64encode(tag)
        ])

    def deserialize_compact(self, s, key):
        try:
            s = to_bytes(s)
            protected_s, ek_s, iv_s, ciphertext_s, tag_s = s.rsplit(b'.')
        except ValueError:
            raise DecodeError('Not enough segments')

        protected = extract_header(protected_s, DecodeError)
        ek = extract_segment(ek_s, DecodeError, 'encryption key')
        iv = extract_segment(iv_s, DecodeError, 'initialization vector')
        ciphertext = extract_segment(ciphertext_s, DecodeError, 'ciphertext')
        tag = extract_segment(tag_s, DecodeError, 'authentication tag')

        self._validate_header(protected)

        algorithm, enc_alg, key = self._prepare_alg_enc_key(
            protected, key, private=True)

        cek = algorithm.unwrap(ek, protected, key)
        aad = to_bytes(protected_s, 'ascii')
        msg = enc_alg.decrypt(ciphertext, aad, iv, tag, cek)
        msg = self._decompress_text(msg, protected)
        return {'header': protected, 'payload': msg}

    def _compress_text(self, s, header):
        if 'zip' in header:
            zip_alg = self._zip_algorithms[header['zip']]
            return zip_alg.compress(to_bytes(s))
        return to_bytes(s)

    def _decompress_text(self, s, header):
        if 'zip' in header:
            zip_alg = self._zip_algorithms[header['zip']]
            return zip_alg.decompress(to_bytes(s))
        return s

    def _prepare_alg_enc_key(self, header, key, private=False):
        algorithm, key = prepare_algorithm_key(
            self._alg_algorithms, header, None, key, private=private)
        enc_alg = self._enc_algorithms[header['enc']]
        return algorithm, enc_alg, key

    def _validate_header(self, header):
        if 'alg' not in header:
            raise MissingAlgorithmError()

        alg = header['alg']
        if alg not in self._alg_algorithms:
            raise UnsupportedAlgorithmError()

        if 'enc' not in header:
            raise MissingEncryptionAlgorithmError()

        enc = header['enc']
        if enc not in self._enc_algorithms:
            raise UnsupportedEncryptionAlgorithmError()

        zip = header.get('zip')
        if zip and zip not in self._zip_algorithms:
            raise UnsupportedCompressionAlgorithmError()

        names = self.REGISTERED_HEADER_PARAMETER_NAMES.copy()
        if self._private_headers:
            names = names.union(self._private_headers)

        for k in header:
            if k not in names:
                raise InvalidHeaderParameterName(k)
