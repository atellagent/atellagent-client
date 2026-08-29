# Python SDK

## Purpose and placement

Use the SDK inside Python code you own. It creates a connected runtime or a
direct client call using an enrolled service-account configuration. It is a
`connected` placement and `sdk` integration surface.

## Authentication and public entry points

`AtellagentClient`, `ConnectedSDKRuntime`, `ServiceAccountConfig`, and
`load_service_account_config_from_yaml` are the public entry points. Enroll
before runtime execution; the token is single-use and is never part of the YAML
configuration. Provider keys remain with customer provider SDK code.

```python
from atellagent_client.sdk import ConnectedSDKRuntime, load_service_account_config_from_yaml

runtime = ConnectedSDKRuntime(load_service_account_config_from_yaml("integration.yaml"))
```

Use `boundary_identity_only` unless a custom integration can present configured,
verifiable external identity evidence. The standard external-host hooks do not
accept caller-supplied identity.

## Coverage and failures

The SDK can participate in `decision` or `route` model governance where the
matching integration documents it. It can also submit tool calls through the
documented boundary. Workflow context is optional: it can add context to a
call, but a workflow is not required for policy to apply.

Authentication, validation, and control failures raise documented SDK errors;
do not catch them and retry through an ungoverned provider or tool path. This
guide does not document private service behavior or support underscored modules.
