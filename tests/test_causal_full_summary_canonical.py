"""Canonical summary strings, including admitted DEL metadata and surrogates."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'))
import causal_full_capacity as capacity
import causal_full_fixtures as fixtures
import causal_full_stream as stream
import counterfactual as formulas
from test_causal_full_summary import limits, sink_result, study


class SummaryCanonicalTests(unittest.TestCase):
    def test_canonical_encoder_is_standard_library_c_encoder(self):
        self.assertEqual(json.encoder.encode_basestring_ascii.__module__, '_json')
        for text in ('public', '\x7f', '\\u007f', '\ud800', '\U0001f642'):
            self.assertEqual(stream._canonical_string(text), json.encoder.encode_basestring_ascii(text))

    def test_cache_entry_and_string_length_bounds(self):
        stream._cached_string.cache_clear()
        for index in range(5000):
            text = 'public-cache-' + str(index)
            self.assertEqual(stream._canonical_string(text), json.encoder.encode_basestring_ascii(text))
        self.assertEqual(stream._cached_string.cache_info().maxsize, 4096)
        self.assertEqual(stream._cached_string.cache_info().currsize, 4096)
        before = stream._cached_string.cache_info()
        self.assertEqual(stream._canonical_string('\x7f' * 1025), json.encoder.encode_basestring_ascii('\x7f' * 1025))
        self.assertEqual(stream._cached_string.cache_info(), before)

    def test_cache_reuses_equal_string_values_without_result_objects(self):
        stream._cached_string.cache_clear()
        first = ''.join(['public-', 'same-token'])
        second = ''.join(['public', '-same-token'])
        self.assertIsNot(first, second)
        self.assertEqual(stream._canonical_string(first), stream._canonical_string(second))
        self.assertEqual(stream._cached_string.cache_info().misses, 1)
        self.assertEqual(stream._cached_string.cache_info().hits, 1)
        stream._cached_string.cache_clear()
        self.assertEqual(stream._cached_string.cache_info().currsize, 0)

    def test_cache_history_and_eviction_do_not_change_bytes(self):
        value = {'public-key\x7f': ['token', '\\u007f', '\x7f', '\udfff']}
        expected = formulas.encoded(value)[:-1]
        stream._cached_string.cache_clear()
        self.assertEqual(b''.join(stream._summary_tokens(value)), expected)
        for index in range(4200):
            stream._canonical_string(str(index))
        self.assertEqual(b''.join(stream._summary_tokens(value)), expected)

    def test_long_strings_bypass_cache_at_every_depth(self):
        value = ('\x7f\ud800\\u007f' * 200)
        expected = json.encoder.encode_basestring_ascii(value).encode('ascii')
        stream._cached_string.cache_clear()
        for depth in (0, 1, 2, 7):
            self.assertEqual(b''.join(stream._summary_tokens(value, depth)), expected)
        self.assertEqual(stream._cached_string.cache_info().currsize, 0)

    def test_cached_and_uncached_boundaries_match(self):
        for length in (0, 1, 1023, 1024, 1025):
            value = '\udfff' * length
            self.assertEqual(stream._canonical_string(value), json.encoder.encode_basestring_ascii(value))

    def test_string_fast_path_does_not_use_bulk_encoder(self):
        import ujson
        with patch.object(ujson, 'dumps', side_effect=AssertionError('string tokens use the C canonical encoder')):
            for depth in (0, 1, 2, 7):
                self.assertEqual(b''.join(stream._summary_tokens('\x7f', depth)), b'"\\u007f"')

    def test_all_unicode_nested_bulk_path_in_bounded_batches(self):
        for start in range(0, 0x110000, 4096):
            text = ''.join(map(chr, range(start, min(start + 4096, 0x110000))))
            value = {text: [text]}
            expected = formulas.encoded(value)[:-1].replace(b'\n', b'\n    ')
            self.assertEqual(b''.join(stream._summary_tokens(value, 2)), expected)

    def test_admitted_del_metadata_canonical_bytes_identity_and_receipt(self):
        specification = fixtures.model()
        specification['source_nodes'][0]['metadata_json'] = '{"review":"\x7f"}'
        model, request = study(specification)
        certificate = model.summary_capacity(request, limits())
        result = model.evaluate(request, capacity=certificate)
        expected = formulas.encoded(result)
        self.assertIn(b'\\u007f', expected)
        self.assertNotIn(b'\x7f', expected)
        identity = formulas.identity({key: value for key, value in result.items() if key != 'output_identity'})
        self.assertEqual(result['output_identity'], identity)
        for block_size in (1, 7):
            with self.subTest(block_size=block_size):
                actual, receipt = sink_result(model, request, certificate, block_size)
                capacity.validate_receipt(model, request, receipt)
                self.assertEqual(actual, expected)
                self.assertEqual(json.loads(actual), result)
                self.assertEqual(receipt['output_identity'], identity)
                self.assertEqual(receipt['bytes_written'], len(expected))
                self.assertEqual(receipt['sha256'], hashlib.sha256(expected).hexdigest())

    def test_controls_boundaries_unicode_escapes_surrogates_and_batches(self):
        codepoints = list(range(0x20)) + [0x20, 0x21, 0x22, 0x2f, 0x5c, 0x7d, 0x7e, 0x7f,
            0x80, 0x9f, 0xa0, 0xff, 0x100, 0x7ff, 0x800, 0x2028, 0x2029, 0x4e2d, 0xd7ff,
            0xd800, 0xdbff, 0xdc00, 0xdfff, 0xe000, 0xffff, 0x10000, 0x1f642, 0x10ffff]
        strings = [chr(codepoint) for codepoint in codepoints] + ['', '\\u007f', '\\\x7f', '\x7f\\u007f',
            '"\\/\b\f\n\r\t', '\ud800\udc00', '\udbff\udfff', '\udc00\ud800', '\ud800x\udfff',
            'ASCII\x7f\u00e9\u4e2d\U0001f642', '\ud800\x7f\udc00']
        for text in strings:
            with self.subTest(text=ascii(text)):
                expected = json.encoder.encode_basestring_ascii(text).encode('ascii')
                for depth in (0, 1, 2, 7):
                    self.assertEqual(b''.join(stream._summary_tokens(text, depth)), expected)
        for width in (0, 1, 15, 16, 17, 33, len(strings)):
            batch = [{text: [text, {'value': text}]} for text in strings[:width]]
            for value in (batch, {'array': batch}, {'outer': {'array': batch}}):
                for depth in (0, 1, 2, 7):
                    with self.subTest(width=width, depth=depth, kind=type(value).__name__):
                        expected = formulas.encoded(value)[:-1].replace(b'\n', b'\n' + b'  ' * depth)
                        self.assertEqual(b''.join(stream._summary_tokens(value, depth)), expected)

    def test_all_unicode_codepoints_in_bounded_string_batches(self):
        for start in range(0, 0x110000, 4096):
            text = ''.join(map(chr, range(start, min(start + 4096, 0x110000))))
            with self.subTest(start=start):
                expected = json.encoder.encode_basestring_ascii(text).encode('ascii')
                self.assertEqual(b''.join(stream._summary_tokens(text, 2)), expected)


if __name__ == '__main__':
    unittest.main()
