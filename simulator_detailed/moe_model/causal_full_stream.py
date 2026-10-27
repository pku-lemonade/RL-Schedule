"""Bounded canonical consumption of the two full-analysis comparison arrays."""
from functools import lru_cache
import hashlib
import json
import tempfile

if __package__:
    from . import counterfactual as formulas, design_space_schema as wire
else:
    import counterfactual as formulas
    import design_space_schema as wire

CHUNK_BYTES = 65536
OUTPUT_CHUNK_BYTES = 8 << 20


class TextSink:
    """ASCII canonical JSON adapter for stdout, including redirected text streams."""

    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        return self.sink.write(bytes(data).decode('ascii'))


def write_all(sink, data):
    remaining = memoryview(data)
    while remaining:
        written = sink.write(remaining)
        if type(written) is not int or not 0 < written <= len(remaining):
            raise OSError('incomplete canonical output write')
        remaining = remaining[written:]


class ArraySpool:
    """An append-only canonical output sink, never an in-memory record cache."""

    def __init__(self, depth, item_schema, check_rational):
        self.depth = depth
        self.item_schema = item_schema
        self.check_rational = check_rational
        self.file = tempfile.TemporaryFile(mode='w+b')
        self.count = 0
        self.finished = False
        self.digest = hashlib.sha256()
        self.fast = False
        self.validator = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.file.close()

    def _write(self, data):
        write_all(self.file, data)
        self.digest.update(data)

    def append(self, item):
        if self.finished:
            raise ValueError('closed comparison array')
        if type(item) is dict and isinstance(item.get('deltas'), ArraySpool):
            deltas = item['deltas']
            properties = self.item_schema['properties']
            array_schema = properties['deltas']
            wire.need(deltas.finished and array_schema['minItems'] <= deltas.count <= array_schema.get('maxItems', deltas.count),
                'SCHEMA_SIZE', '$/deltas', 'bounded array required')
            wire.need(deltas.item_schema == array_schema['items'] and not array_schema['uniqueItems'],
                'SCHEMA_TYPE', '$/deltas', 'validated incremental delta records required')
            envelope = {key: value for key, value in item.items() if key != 'deltas'}
            wire.validate(envelope, wire._object({key: value for key, value in properties.items() if key != 'deltas'}))
            self.check_rational(envelope)
        else:
            if self.fast:
                if self.validator is None:
                    self.validator = wire.compile_validator(self.item_schema)
                self.validator(item)
            else:
                wire.validate(item, self.item_schema)
            self.check_rational(item)
        self._write(b'[\n' if not self.count else b',\n')
        self._write(b' ' * (2 * (self.depth + 1)))
        for data in _summary_tokens(item, self.depth + 1) if self.fast else _tokens(item, self.depth + 1):
            self._write(data)
        self.count += 1

    def finish(self):
        if not self.finished:
            self._write(b'\n' + b' ' * (2 * self.depth) + b']' if self.count else b'[]')
            self.finished = True

    def chunks(self):
        if not self.finished:
            raise ValueError('unfinished comparison array')
        self.file.seek(0)
        actual = hashlib.sha256()
        while data := self.file.read(CHUNK_BYTES):
            actual.update(data)
            yield data
        if actual.digest() != self.digest.digest():
            raise OSError('comparison output spool integrity failure')


def _tokens(value, depth=0):
    if isinstance(value, ArraySpool):
        if value.depth != depth:
            raise ValueError('canonical array depth mismatch')
        yield from value.chunks()
    elif type(value) is dict:
        if not value:
            yield b'{}'
            return
        yield b'{\n'
        for index, key in enumerate(sorted(value)):
            if index:
                yield b',\n'
            yield ('  ' * (depth + 1) + json.dumps(key) + ': ').encode()
            yield from _tokens(value[key], depth + 1)
        yield ('\n' + '  ' * depth + '}').encode()
    elif type(value) is list:
        if not value:
            yield b'[]'
            return
        yield b'[\n'
        for index, item in enumerate(value):
            if index:
                yield b',\n'
            yield ('  ' * (depth + 1)).encode()
            yield from _tokens(item, depth + 1)
        yield ('\n' + '  ' * depth + ']').encode()
    else:
        yield json.dumps(value, allow_nan=False).encode()


@lru_cache(maxsize=4096)
def _cached_string(value):
    return json.encoder.encode_basestring_ascii(value)


def _canonical_string(value):
    return _cached_string(value) if len(value) <= 1024 else json.encoder.encode_basestring_ascii(value)


def _summary_tokens(value, depth=0):
    if isinstance(value, ArraySpool):
        yield from _tokens(value, depth)
    elif type(value) is str:
        yield _canonical_string(value).encode('ascii')
    elif depth >= 2:
        import ujson
        text = ujson.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, escape_forward_slashes=False).replace('\x7f', '\\u007f')
        yield text.replace('\n', '\n' + '  ' * depth).encode('ascii')
    elif type(value) is dict and value:
        yield b'{\n'
        for index, key in enumerate(sorted(value)):
            if index:
                yield b',\n'
            yield ('  ' * (depth + 1) + _canonical_string(key) + ': ').encode()
            yield from _summary_tokens(value[key], depth + 1)
        yield ('\n' + '  ' * depth + '}').encode()
    elif type(value) is list and value:
        import ujson
        yield b'[\n'
        for start in range(0, len(value), 16):
            if start:
                yield b',\n'
            text = ujson.dumps(value[start:start + 16], indent=2, sort_keys=True, ensure_ascii=True, escape_forward_slashes=False).replace('\x7f', '\\u007f')[2:-2]
            yield ('  ' * depth + text.replace('\n', '\n' + '  ' * depth)).encode('ascii')
        yield ('\n' + '  ' * depth + ']').encode()
    else:
        yield from _tokens(value, depth)


def chunks(value):
    if isinstance(value, CanonicalOutput):
        yield from value.chunks()
        return
    pending = bytearray()
    summary = type(value) is dict and value.get('version') == 'caller-causal-full-analysis-summary-v1'
    for token in _summary_tokens(value) if summary else _tokens(value):
        for offset in range(0, len(token), CHUNK_BYTES):
            piece = token[offset:offset + CHUNK_BYTES]
            if len(pending) + len(piece) > CHUNK_BYTES:
                yield bytes(pending)
                pending.clear()
            pending.extend(piece)
    pending.extend(b'\n')
    if pending:
        yield bytes(pending)


def identity(value):
    if isinstance(value, CanonicalOutput):
        return value.identity()
    digest = hashlib.sha256()
    for data in chunks(value):
        digest.update(data)
    return digest.hexdigest()


def _assembly_tokens(value, depth):
    if type(value) is dict and value:
        yield b'{\n'
        for index, key in enumerate(sorted(value)):
            if index:
                yield b',\n'
            yield ('  ' * (depth + 1) + _canonical_string(key) + ': ').encode('ascii')
            yield from _assembly_tokens(value[key], depth + 1)
        yield ('\n' + '  ' * depth + '}').encode('ascii')
    elif type(value) is list and value:
        import ujson
        yield b'[\n'
        for start in range(0, len(value), 16):
            if start:
                yield b',\n'
            text = ujson.dumps(value[start:start + 16], indent=2, sort_keys=True, ensure_ascii=True,
                escape_forward_slashes=False).replace('\x7f', '\\u007f')[2:-2]
            yield ('  ' * depth + text.replace('\n', '\n' + '  ' * depth)).encode('ascii')
        yield ('\n' + '  ' * depth + ']').encode('ascii')
    else:
        yield from _summary_tokens(value, depth)


class CanonicalOutput:
    """Execution-owned preimage with one canonical assembly and deterministic framing."""

    def __init__(self, result):
        self.result = result
        self.file = tempfile.TemporaryFile(mode='w+b', buffering=CHUNK_BYTES)
        self.chunk_bytes = min(CHUNK_BYTES, OUTPUT_CHUNK_BYTES)
        self.insertion = None
        self.output_identity = None
        self.prefix_digest = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.file.close()

    def assemble(self):
        if self.insertion is not None:
            raise ValueError('canonical output already assembled')
        summary = self.result.get('version') == 'caller-causal-full-analysis-summary-v1'
        tokens = _assembly_tokens if summary else _tokens
        write_all(self.file, b'{\n')
        for index, key in enumerate(sorted(key for key in self.result if key != 'output_identity')):
            if index:
                write_all(self.file, b',\n')
            if self.insertion is None and key > 'output_identity':
                self.insertion = self.file.tell()
            write_all(self.file, ('  ' + _canonical_string(key) + ': ').encode('ascii'))
            for data in tokens(self.result[key], 1):
                write_all(self.file, data)
        write_all(self.file, b'\n}\n')
        if self.file.tell() >= 2 * OUTPUT_CHUNK_BYTES:
            self.chunk_bytes = OUTPUT_CHUNK_BYTES
        self.file.flush()

    def preimage_chunks(self):
        self.file.seek(0)
        remaining = self.insertion
        while remaining:
            data = self.file.read(min(remaining, self.chunk_bytes))
            if not data:
                raise OSError('incomplete canonical preimage')
            remaining -= len(data)
            yield data
        while data := self.file.read(self.chunk_bytes):
            yield data

    def identity(self):
        if self.output_identity is None:
            digest = hashlib.sha256()
            count = 0
            for data in self.preimage_chunks():
                digest.update(data)
                count += len(data)
                if count == self.insertion:
                    self.prefix_digest = digest.copy()
            self.output_identity = digest.hexdigest()
        return self.output_identity

    def chunks(self):
        identity_line = ('  "output_identity": "' + self.identity() + '",\n').encode('ascii')
        self.file.seek(0)
        remaining = self.insertion
        while remaining:
            data = self.file.read(min(remaining, self.chunk_bytes))
            if not data:
                raise OSError('incomplete canonical preimage')
            remaining -= len(data)
            yield data
        yield identity_line
        while data := self.file.read(self.chunk_bytes):
            yield data


    def emit_to(self, sink):
        self.identity()
        digest = self.prefix_digest.copy()
        count = 0
        for data in chunks(self):
            write_all(sink, data)
            if count >= self.insertion:
                digest.update(data)
            count += len(data)
        return count, digest.hexdigest()


def validation_view(result):
    comparisons, edges = result['comparisons'], result['pareto']['edges']
    if not isinstance(comparisons, ArraySpool):
        return result
    if not isinstance(edges, ArraySpool) or not comparisons.finished or not edges.finished:
        raise ValueError('unfinished comparison output')
    return dict(result, comparisons=[], pareto=dict(result['pareto'], edges=[]))
