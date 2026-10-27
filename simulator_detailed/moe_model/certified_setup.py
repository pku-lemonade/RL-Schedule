"""Exact admission/capacity once; immutable, execution-local authenticated handoff."""
from copy import deepcopy
from dataclasses import dataclass
from decimal import localcontext
from functools import lru_cache
import hashlib
import hmac
import os
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from weakref import WeakSet

if __package__:
    from . import causal_full_analysis as full, causal_full_capacity as capacity, causal_full_schema as schema
    from . import counterfactual as formulas, design_space as api, design_space_schema as wire
else:
    import causal_full_analysis as full
    import causal_full_capacity as capacity
    import causal_full_schema as schema
    import counterfactual as formulas
    import design_space as api
    import design_space_schema as wire

VERSION = 'certified-summary-setup-v1'
_models = WeakSet()
_requests = WeakSet()
_executions = WeakSet()


def freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(map(freeze, value))
    return value


def thaw(value):
    if isinstance(value, MappingProxyType):
        return {key: thaw(child) for key, child in value.items()}
    if type(value) is tuple:
        return list(map(thaw, value))
    return value


@lru_cache(maxsize=1)
def bindings():
    directory = Path(__file__).resolve().parent
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob('*.py'))}
    schemas = dict(model=schema.model_schema(), request=schema.request_schema(schema.SUMMARY_VERSION),
        result=schema.result_schema(schema.SUMMARY_VERSION), limits=capacity.limits_schema())
    return freeze(dict(source_identity=formulas.identity(sources), schema_identity=formulas.identity(schemas),
        serializer_identity=capacity._source_bindings()['serializer_identity']))


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class AdmittedModel:
    _specification: object
    _timing: object
    identity: str
    timing_identity: str
    code: object
    _mode: str = schema.MODE

    def specification(self):
        return thaw(self._specification)

    def template(self, *, output_detail='full'):
        return full.template(self, output_detail=output_detail)

    def prepare_summary(self, request, limits, *, authority):
        return authority.prepare(self, request, limits)

    def evaluate_to(self, request, sink, *, setup, limits, block_size=1):
        wire.need(type(request) is Prepared and request in _requests, 'SETUP_RECEIPT', '$', 'issued prepared request required')
        request.verify(self, setup, limits)
        certificate = thaw(request.certificate)
        capacity._gate(certificate, sink)
        execution = Execution(self, request, certificate)
        _executions.add(execution)
        with localcontext(api._arithmetic_context()):
            return full.evaluate_to(execution.model, execution.request, sink, block_size=block_size, _prepared=execution)


def admit(specification):
    model = api.DesignSpace(specification, admission_only=True)
    admitted = AdmittedModel(freeze(model._specification), freeze(model._timing), model.identity,
        formulas.identity(model._timing), bindings())
    _models.add(admitted)
    return admitted


class SetupAuthority:
    """One execution's ephemeral key; transfer only through a trusted inherited FD."""

    def __init__(self):
        self.__key = os.urandom(32)

    def inherit_key(self, descriptor):
        wire.need(os.write(descriptor, self.__key) == 32, 'SETUP_KEY', '$', 'complete inherited key required')

    def prepare(self, model, request, limits):
        wire.need(type(model) is AdmittedModel and model in _models and model.code == bindings(),
            'SETUP_MODEL', '$', 'current immutable admitted model capability required')
        owned, frozen_limits = deepcopy(request), deepcopy(limits)
        wire.validate(owned, schema.request_schema(schema.SUMMARY_VERSION))
        wire.validate(frozen_limits, capacity.limits_schema())
        wire.need(owned['input_identity'] == model.identity, 'SETUP_IDENTITY', '$', 'request must bind admitted model')
        request_identity = formulas.identity(owned)
        proof_model = SimpleNamespace(_specification=thaw(model._specification), identity=model.identity)
        timing = thaw(model._timing)
        grids, work = full._prepare(proof_model._specification, timing, owned)
        certificate = capacity._certificate(proof_model, owned, frozen_limits, timing, work,
            request_identity, model.timing_identity)
        capacity._gate(certificate)
        state = dict(model=proof_model._specification, timing=timing, request=owned,
            grids=[[full.rational(rate) for rate in grid] for grid in grids], work=work)
        payload = formulas.encoded(state)
        body = dict(version=VERSION, mode=schema.MODE, input_identity=model.identity,
            request_identity=request_identity, timing_identity=model.timing_identity, code=thaw(model.code),
            records=full._records(proof_model._specification),
            payload_sha256=hashlib.sha256(payload).hexdigest(), payload_bytes=len(payload), capacity=certificate)
        receipt = dict(body, authenticator=hmac.digest(self.__key, formulas.encoded(body), 'sha256').hex())
        prepared = Prepared(model, freeze(owned), request_identity, tuple(tuple(grid) for grid in grids), freeze(work),
            freeze(certificate), freeze(receipt), payload)
        _requests.add(prepared)
        return prepared


def _current(receipt):
    keys = {'version', 'mode', 'input_identity', 'request_identity', 'timing_identity', 'code',
        'records', 'payload_sha256', 'payload_bytes', 'capacity', 'authenticator'}
    wire.need(type(receipt) is dict and set(receipt) == keys, 'SETUP_RECEIPT', '$', 'closed setup receipt required')
    wire.need(receipt['version'] == VERSION and receipt['mode'] == schema.MODE and receipt['code'] == thaw(bindings()),
        'SETUP_STALE', '$', 'current source, mode, schemas and serializer required')


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class Prepared:
    model: AdmittedModel
    data: object
    identity: str
    grids: tuple
    work: object
    certificate: object
    _receipt: object
    payload: bytes

    @property
    def receipt(self):
        return thaw(self._receipt)

    def verify(self, model, receipt, limits):
        _current(receipt)
        wire.need(self in _requests and type(model) is AdmittedModel and model in _models
            and model.identity == self.model.identity and model.code == self.model.code,
            'SETUP_IDENTITY', '$', 'same immutable admitted model capability required')
        wire.need(formulas.encoded(receipt) == formulas.encoded(self.receipt), 'SETUP_IDENTITY', '$', 'same prepared request and exact closed proof required')
        wire.need(formulas.encoded(limits) == formulas.encoded(thaw(self.certificate['limits'])),
            'SETUP_LIMIT', '$', 'exact caller limits required')

    def validate_receipt(self, receipt):
        wire.need(self in _requests, 'SETUP_RECEIPT', '$', 'issued prepared request capability required')
        wire.need(type(receipt) is dict and set(receipt) == {'output_identity', 'bytes_written', 'sha256', 'capacity', 'receipt_identity'},
            'CAPACITY_RECEIPT', '$', 'closed completion receipt required')
        for key in ('output_identity', 'sha256', 'receipt_identity'):
            wire.need(type(receipt[key]) is str and len(receipt[key]) == 64 and set(receipt[key]) <= set('0123456789abcdef'),
                'CAPACITY_RECEIPT', '$', 'canonical SHA-256 identity required')
        wire.need(formulas.encoded(receipt['capacity']) == formulas.encoded(thaw(self.certificate)),
            'CAPACITY_RECEIPT', '$', 'exact certified capacity binding required')
        wire.need(type(receipt['bytes_written']) is int and 0 <= receipt['bytes_written'] <= self.certificate['bounds']['result_bytes'],
            'CAPACITY_RECEIPT', '$', 'certified byte count required')
        wire.need(formulas.identity({key: value for key, value in receipt.items() if key != 'receipt_identity'}) == receipt['receipt_identity'],
            'CAPACITY_RECEIPT', '$', 'complete output receipt identity required')


def import_setup(payload, receipt, *, key_fd, input_identity, request_identity, limits):
    _current(receipt)
    key = os.read(key_fd, 33)
    wire.need(len(key) == 32, 'SETUP_KEY', '$', 'execution-local inherited ephemeral key required')
    body = {name: value for name, value in receipt.items() if name != 'authenticator'}
    wire.need(type(receipt['authenticator']) is str and hmac.compare_digest(receipt['authenticator'],
        hmac.digest(key, formulas.encoded(body), 'sha256').hex()), 'SETUP_AUTH', '$', 'authentic exact-proof handoff required')
    wire.need(receipt['input_identity'] == input_identity and receipt['request_identity'] == request_identity,
        'SETUP_IDENTITY', '$', 'independently expected model and request identities required')
    wire.need(type(payload) is bytes and len(payload) == receipt['payload_bytes']
        and hashlib.sha256(payload).hexdigest() == receipt['payload_sha256'], 'SETUP_PAYLOAD', '$', 'exact authenticated prepared payload required')
    wire.need(formulas.encoded(limits) == formulas.encoded(receipt['capacity']['limits']),
        'SETUP_LIMIT', '$', 'exact independently supplied caller limits required')
    state = wire.strict_json(payload)
    model = AdmittedModel(freeze(state['model']), freeze(state['timing']), input_identity, receipt['timing_identity'], freeze(receipt['code']))
    prepared = Prepared(model, freeze(state['request']), request_identity,
        tuple(tuple(full.number(rate) for rate in grid) for grid in state['grids']), freeze(state['work']),
        freeze(receipt['capacity']), freeze(receipt), payload)
    _models.add(model)
    _requests.add(prepared)
    return prepared


class Execution:
    def __init__(self, model, prepared, certificate):
        self.model = SimpleNamespace(_specification=thaw(model._specification), _timing=thaw(model._timing), identity=model.identity)
        self.request = thaw(prepared.data)
        self.request_identity = prepared.identity
        self.grids, self.work, self.capacity = prepared.grids, thaw(prepared.work), certificate

    def validate_inputs(self, result):
        wire.need(self in _executions and result['model'] is self.model._specification and result['request'] is self.request
            and result['input_identity'] == self.model.identity and result['request_identity'] == self.request_identity,
            'OUTPUT_IDENTITY', '$', 'exact owned copies of the immutable admitted inputs required')


def check_execution(execution, model, request):
    wire.need(type(execution) is Execution and execution in _executions
        and execution.model is model and execution.request is request,
        'SETUP_RECEIPT', '$', 'verified execution-local capability required')
