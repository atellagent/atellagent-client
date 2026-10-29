# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Delivery-scoped transport for the shared SDK model operation."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from atellagent_client.sdk.operations_modules.model_invocations import model_invocation_async
from .contracts import ConnectedProtocolError


class ConnectedModelActions:
    async def invoke_model_async(self, messages, *, stream=False, effect_key=None, **options):
        if stream or 'workflow_context' in options:
            raise ValueError('Delivery model actions use server-bound authority and do not stream')
        if options.get('context_mode') == 'platform':
            latest = next((index for index in range(len(messages) - 1, -1, -1)
                           if messages[index].get('role') == 'user'), None)
            if latest is None:
                raise ValueError('Platform conversation requires a new user turn')
            messages = messages[latest:]
        if effect_key is None:
            value = json.dumps({'messages': messages, 'options': options},
                               sort_keys=True, separators=(',', ':'), allow_nan=False)
            effect_key = 'model-' + hashlib.sha256(value.encode()).hexdigest()
        if not isinstance(effect_key, str) or not effect_key or len(effect_key) > 128:
            raise ValueError('effect_key must be nonempty and at most 128 characters')
        if any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-'
               for char in effect_key):
            raise ValueError('effect_key contains unsupported characters')
        message_id = self._context.message.message_id
        request_id = str(uuid5(NAMESPACE_URL, f'connected-model:{message_id}:{effect_key}'))
        endpoint = self._url(self._context.config.model_action_path_template) + '/' + effect_key

        async def request(method, url, **kwargs):
            if method == 'POST' and url.endswith('/model-invocations'):
                kwargs['json'] = {'request': kwargs['json']}
            elif method == 'GET' and url.endswith('/model-invocations/responses/' + request_id):
                pass
            else:
                raise ConnectedProtocolError('Model response is not bound to this delivery effect')
            kwargs['headers'] = {'X-Atellagent-Connected-Capability': self._context.capability}
            response = await self._context.session.request_authenticated(method, endpoint, **kwargs)
            if response.http_version != 'HTTP/2':
                raise ConnectedProtocolError('Connected model action did not use HTTP/2')
            return response

        return await model_invocation_async(base_url=self._context.session.base_url,
            api_version=self._context.config.api_version, request=request, headers={},
            messages=messages, stream=False, **options)
