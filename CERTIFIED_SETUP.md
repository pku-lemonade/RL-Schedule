# Execution-local certified summary setup

`DesignSpace.certified(specification)` performs the unchanged exact schema and
timing admission, with zero numerical evaluations, and owns recursively immutable
model/timing state. `model.prepare_summary(request, limits, authority=authority)`
validates the request, runs the unchanged exact `_prepare` and capacity estimator,
checks capacity, and returns an immutable prepared request capability.

```
authority = certified_setup.SetupAuthority()
model = DesignSpace.certified(specification)
prepared = model.prepare_summary(request, limits, authority=authority)
receipt = model.evaluate_to(prepared, sink, setup=prepared.receipt, limits=limits)
prepared.validate_receipt(receipt)
```

The closed setup receipt binds canonical model/request/timing identities, mode,
all model/request/result/limit schemas, the Python source closure, both canonical
serializers, the exact capacity certificate (work/cardinalities, digit/identifier
bounds and caller limits), and the complete prepared payload. No acceptance
boolean is trusted. Output records, order, identity and encoding are unchanged.

In-process execution requires issued capabilities and exact receipt equality.
The immutable inputs are copied into an execution-owned view; reference identity
checks replace repeated input canonicalization and input schema validation.
All numerical result checks, complete output hashing and canonical serialization
remain. Current disk/sink checks are performed on every execution. Legacy mutable
`DesignSpace` APIs and their exact gates remain compatible and unchanged.

For a fresh process, pass `prepared.payload` and `prepared.receipt` as data. The
trusted launcher transfers the authority's ephemeral 32-byte key through an
inherited pipe using `authority.inherit_key(write_fd)`. The receiving process
calls `import_setup(payload, receipt, key_fd=read_fd, input_identity=expected_model,
request_identity=expected_request, limits=expected_limits)`. Expected identities,
limits and the inherited key FD belong to the execution's trusted handoff, not
to an untrusted receipt. HMAC and payload SHA-256 are checked before decoding the
prepared state; admission and capacity are not repeated. The caller closes FDs.
No key is placed in the receipt, payload, environment or persistent file. There
is no PKI, trust store, key-distribution service or private specialization.

The trust boundary is serialized caller data and receipts, not arbitrary Python
code allowed to modify module internals. Source identities are computed once per
fresh process and describe its immutable loaded implementation; replacing live
Python code is outside the capability contract. Fresh processes reject stale
source/schema/serializer receipts. First-time proof is never bypassed: the local
authority can issue only from the exact admitted-model capability.
