"""Exact materialized byte oracle and execution-owned canonical spool lifecycle."""
from contextlib import ExitStack
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from test_causal_full_summary import full, capacity, fixtures, formulas, schema, stream, study, limits, wire, oracle, summary_reference

def materialized_result(model, request):
    result = oracle.evaluate(model, dict(request, version=schema.VERSION))
    return summary_reference(result) if request['version'] == schema.SUMMARY_VERSION else result


def canonical_chunks(value):
    for text in json.JSONEncoder(sort_keys=True, indent=2, allow_nan=False).iterencode(value):
        yield text.encode()
    yield b'\n'


class ForwardSink:
    def __init__(self, target, width=1 << 30):
        self.target, self.width = target, width

    def fileno(self):
        return self.target.fileno()

    def write(self, data):
        return self.target.write(data[:self.width])

    def seek(self, *arguments):
        raise AssertionError('caller sink must not be sought')


class OutputIdentityTests(unittest.TestCase):
    def test_exact_materialized_preimage_identity_stream_and_receipt(self):
        variants = [fixtures.model() for index in range(3)]
        variants[1]['peak'].update(kind='unavailable', parameter=None, domain=None, missing_input='public-unknown')
        variants[1]['source_nodes'][0]['metadata_json'] = json.dumps(dict(public='\x00\x7f\ud800\U0001f642\u2028/"\\\n', unknown=None))
        variants[2]['composition']['participants'][0]['attention_offset_ticks'] = 13
        for specification in variants:
            model, request = study(specification)
            for version in (schema.SUMMARY_VERSION, schema.VERSION):
                request = dict(request, version=version)
                certificate = model.summary_capacity(request, limits()) if version == schema.SUMMARY_VERSION else None
                materialized = materialized_result(model, request)
                preimage = formulas.encoded({key: value for key, value in materialized.items() if key != 'output_identity'})
                expected = formulas.encoded(materialized)
                oracle_receipt = dict(output_identity=hashlib.sha256(preimage).hexdigest(),
                    bytes_written=len(expected), sha256=hashlib.sha256(expected).hexdigest())
                if certificate is not None:
                    oracle_receipt['capacity'] = certificate
                    oracle_receipt['receipt_identity'] = formulas.identity(oracle_receipt)
                for block_size, chunk_size, short in ((1, 7, 13), (7, 65536, 65537), (129, 8 << 20, 1 << 30)):
                    observed = []
                    assemble = stream.CanonicalOutput.assemble
                    def capture(output):
                        assemble(output)
                        self.assertEqual(b''.join(output.preimage_chunks()), preimage)
                        observed.append(output)
                    with tempfile.TemporaryFile('w+b') as target, patch.object(stream, 'OUTPUT_CHUNK_BYTES', chunk_size), \
                            patch.object(stream.CanonicalOutput, 'assemble', capture):
                        receipt = full.evaluate_to(model, request, ForwardSink(target, short), block_size=block_size, capacity=certificate)
                        target.seek(0)
                        self.assertEqual(target.read(), expected)
                    self.assertEqual(receipt, oracle_receipt)
                    self.assertEqual(formulas.encoded(receipt), formulas.encoded(oracle_receipt))
                    self.assertEqual(receipt['output_identity'], hashlib.sha256(preimage).hexdigest())
                    self.assertEqual(len(observed), 1)
                    self.assertTrue(observed[0].file.closed)
                    if certificate:
                        capacity.validate_receipt(model, request, receipt)

    def test_one_assembly_no_second_record_traversal_and_spool_reuse(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        assemble, validate, array_chunks = stream.CanonicalOutput.assemble, full.validate_result, stream.ArraySpool.chunks
        counts = dict(assembly=0, arrays=0)
        held = ExitStack()
        def assembled(output):
            counts['assembly'] += 1
            assemble(output)
        def array_read(output):
            counts['arrays'] += 1
            yield from array_chunks(output)
        def validated(result, **keywords):
            validate(result, **keywords)
            for name in ('_assembly_tokens', '_summary_tokens', '_tokens'):
                held.enter_context(patch.object(stream, name, side_effect=AssertionError('second canonical traversal')))
            held.enter_context(patch.object(stream.ArraySpool, 'chunks', side_effect=AssertionError('second comparison traversal')))
            held.enter_context(patch.object(stream.CanonicalOutput, 'preimage_chunks', side_effect=AssertionError('second preimage hash')))
        with held, tempfile.TemporaryFile('w+b') as sink, patch.object(stream.CanonicalOutput, 'assemble', assembled), \
                patch.object(stream.ArraySpool, 'chunks', array_read), patch.object(full, 'validate_result', validated):
            receipt = full.evaluate_to(model, request, sink, capacity=certificate)
        self.assertEqual(counts, dict(assembly=1, arrays=2))
        self.assertGreater(receipt['bytes_written'], 0)

    def test_hash_assembly_write_receipt_failures_close_every_spool(self):
        model, request = study()
        certificate = model.summary_capacity(request, limits())
        for stage in ('assembly', 'hash', 'zero', 'none', 'negative', 'oversize', 'write', 'receipt'):
            opened = []
            temporary = tempfile.TemporaryFile
            def observed(*arguments, **keywords):
                result = temporary(*arguments, **keywords)
                opened.append(result)
                return result
            identity = formulas.identity
            def fail_receipt(value):
                if type(value) is dict and 'bytes_written' in value:
                    raise OSError('receipt identity failure')
                return identity(value)
            class BrokenSink(ForwardSink):
                def write(self, data):
                    if stage == 'write':
                        raise OSError('partial output failure')
                    if stage in ('zero', 'none', 'negative', 'oversize'):
                        return {'zero': 0, 'none': None, 'negative': -1, 'oversize': len(data) + 1}[stage]
                    return super().write(data)
            with temporary('w+b') as target, ExitStack() as stack:
                stack.enter_context(patch.object(stream.tempfile, 'TemporaryFile', observed))
                if stage == 'assembly':
                    stack.enter_context(patch.object(stream, '_assembly_tokens', side_effect=OSError('assembly failure')))
                if stage == 'hash':
                    stack.enter_context(patch.object(stream.CanonicalOutput, 'preimage_chunks', side_effect=OSError('hash read failure')))
                if stage == 'receipt':
                    stack.enter_context(patch.object(formulas, 'identity', fail_receipt))
                with self.assertRaises(OSError):
                    full.evaluate_to(model, request, BrokenSink(target), capacity=certificate)
            self.assertTrue(opened)
            self.assertTrue(all(output.closed for output in opened), stage)

    def test_nonseekable_full_detail_without_fileno(self):
        model, request = study()
        request['version'] = schema.VERSION
        target = io.BytesIO()
        receipt = full.evaluate_to(model, request, ForwardSink(target, 17))
        self.assertEqual(target.getvalue(), formulas.encoded(materialized_result(model, request)))
        self.assertEqual(hashlib.sha256(target.getvalue()).hexdigest(), receipt['sha256'])

    def test_standalone_validation_does_not_reuse_execution_cache(self):
        model, request = study()
        request['version'] = schema.VERSION
        result = model.evaluate(request)
        result['output_identity'] = '0' * 64
        with self.assertRaises(wire.Rejected):
            full.validate_result(result)

    def test_large_synthetic_existing_spool_exact_oracle(self):
        item = {'public': '\u2603' * 4096, 'unknown': None, 'ties': [1, 1]}
        with stream.ArraySpool(1, {}, lambda value: None) as comparison, \
                stream.ArraySpool(2, {}, lambda value: None) as edge:
            fragment = b'      ' + json.dumps(item, indent=2, sort_keys=True).replace('\n', '\n      ').encode('ascii')
            edge._write(b'[\n' + b',\n'.join([fragment] * 1024) + b'\n    ]')
            edge.finished = True
            comparison.finish()
            result = dict(version=schema.SUMMARY_VERSION, comparisons=comparison, pareto=dict(edges=edge), model={})
            with stream.CanonicalOutput(result) as output:
                output.assemble()
                self.assertGreater(output.file.tell(), 24 << 20)
                expected = hashlib.sha256()
                for block in canonical_chunks(dict(result, comparisons=[], pareto=dict(edges=[item] * 1024))):
                    expected.update(block)
                self.assertEqual(output.identity(), expected.hexdigest())
                result['output_identity'] = output.identity()
                expected_final = hashlib.sha256()
                for block in canonical_chunks(dict(result, comparisons=[], pareto=dict(edges=[item] * 1024))):
                    expected_final.update(block)
                final = hashlib.sha256()
                for block in output.chunks():
                    self.assertLessEqual(len(block), stream.OUTPUT_CHUNK_BYTES)
                    final.update(block)
                self.assertEqual(final.hexdigest(), expected_final.hexdigest())


if __name__ == '__main__':
    unittest.main()
